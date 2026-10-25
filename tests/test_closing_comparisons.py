"""Compare proposed closing costs, never inferred supplier acceptance."""

import copy
import json

import pytest
from assessment_helpers import assessment_fields

from aeiou.acceptance_floor import AcceptanceFloor
from aeiou.config import ROOT
from aeiou.price_planner import closing_comparisons
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package

SID = "sup:70740"
POLICY = json.loads((ROOT / "config/p18-accept-510.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v28.txt").read_text()


class NoModel:
    def complete(self, *args):
        raise AssertionError("An exact eligible acceptance must not call the model")


def agent(backend=None):
    return PromptLabAgent(
        package(),
        backend or NoModel(),
        PromptSpec(
            "closing-comparison-test", STRATEGY, assessment_enabled=True, execution_policy=copy.deepcopy(POLICY)
        ),
    )


def scenario():
    fixture = json.loads((ROOT / "tests/fixtures/p18-v19-verified-offer.json").read_text())
    case, events = fixture["case"], fixture["threads"][SID][:5]
    # Synthetic structured variants of the recorded open reply. They test cost
    # ordering and transport, not how the supplier responds to a new proposal.
    events[3]["term_sheets"] = [
        {"terms": {**copy.deepcopy(p), "contract_months": 36}} for p in POLICY["early_package_alternatives"]["packages"]
    ]
    alternative = events[-1]["term_sheets"][1]["terms"]
    alternative["payment_days"] = 14
    alternative["index_clause"]["deadband_pct"] = 3
    events[-1]["body"] = "Please review our complete quotes."
    return case, {SID: events}


def payload(a, case, threads):
    a.prepare(case)
    return json.loads(a.render_lab(case, threads))


def test_common_price_reverses_the_quoted_cost_order_and_preserves_source_status():
    case, threads = scenario()
    before = copy.deepcopy(threads)
    p = payload(agent(), case, threads)
    supplier = p["protocol_state"]["suppliers"][SID]
    quotes = supplier["offers"]
    fast = next(o for o in quotes if o["terms"]["payment_days"] == 14)
    slow = next(o for o in quotes if o["terms"]["payment_days"] == 60)
    assert fast["cost"] < slow["cost"]
    plan = p["price_plan"]["suppliers"][SID]
    rows = plan["closing_package_comparison"]
    assert [(r["terms"]["payment_days"], r["terms"]["index_clause"]["deadband_pct"]) for r in rows] == [
        (60, 5),
        (30, 5),
        (14, 3),
    ]
    assert [r["complete_cost"] for r in rows] == [243915.66, 245527.26, 248562.44]
    assert [r["estimated_score"] for r in rows] == [70.6787, 68.4565, 64.2713]
    assert [r["cost_above_lowest_comparison"] for r in rows] == [0, 1611.60, 4646.78]
    assert len(rows[-1]["origins"]) == 2
    assert rows[1]["origins"] == [{"kind": "buyer_proposal", "message_index": 3}]
    assert all("requires supplier approval" in r["status"] for r in rows)
    assert all(r["within_buyer_limits"] for r in rows)
    assert plan["best_eligible_offer_id"] is None
    assert not supplier["eligible_offer_ids"]
    assert threads == before


@pytest.mark.parametrize("price", [None, True, float("nan"), float("inf"), 0, -1, 5.11])
def test_unconfigured_or_unauthorized_reference_price_does_not_create_comparisons(price):
    case, threads = scenario()
    a = agent()
    state = a.decision_protocol_state(case, threads)
    assert (
        closing_comparisons(
            case, threads[SID], state["suppliers"][SID]["offers"], price, a.acceptance_floor, minimum_contract_months=24
        )
        == []
    )


def test_minimum_duration_and_positive_floor_are_still_reflected_in_comparison():
    case, threads = scenario()
    a = agent()
    offers = a.decision_protocol_state(case, threads)["suppliers"][SID]["offers"]
    strict = AcceptanceFloor.from_dict({**a.acceptance_floor.metadata(), "minimum_score": 70.01})
    rows = closing_comparisons(case, threads[SID], offers, 5.10, strict, minimum_contract_months=24)
    assert [r["within_buyer_limits"] for r in rows] == [True, False, False]
    for message in threads[SID]:
        for sheet in message.get("term_sheets", []):
            sheet["terms"]["contract_months"] = 12
    offers = a.decision_protocol_state(case, threads)["suppliers"][SID]["offers"]
    assert closing_comparisons(case, threads[SID], offers, 5.10, strict, minimum_contract_months=24) == []


def test_comparisons_reach_the_model_and_decision_audit_without_rewriting_its_action():
    class Buyer:
        def complete(self, system, user):
            p = json.loads(user)
            rows = p["price_plan"]["suppliers"][SID]["closing_package_comparison"]
            assert [r["complete_cost"] for r in rows] == [243915.66, 245527.26, 248562.44]
            return json.dumps(
                {
                    "supplier_id": SID,
                    "action": "send",
                    "body": "Could you approve this complete proposal?",
                    "term_sheets": [rows[1]["terms"]],
                    **assessment_fields(p, SID),
                }
            )

    case, threads = scenario()
    a = agent(Buyer())
    move = a.decide(case, threads)
    assert move["message"]["term_sheets"][0]["terms"]["payment_days"] == 30
    assert a.decisions[-1]["price_plan"]["closing_package_comparison"][1]["complete_cost"] == 245527.26
    assert len(a.decisions[-1]["model_requests"]) == 1


def test_v28_still_accepts_an_actual_510_fourteen_day_three_percent_final():
    case, threads = scenario()
    final = threads[SID][-1]
    final["status"] = "final_offer"
    final["term_sheets"] = [copy.deepcopy(final["term_sheets"][1])]
    final["term_sheets"][0]["terms"]["unit_price"] = 5.10
    final["standing_offer_id"] = final["term_sheets"][0]["term_sheet_id"]
    a = agent()
    assert a.decide(case, threads)["message"]["action"] == {
        "type": "accept",
        "term_sheet_id": final["term_sheets"][0]["term_sheet_id"],
    }
    assert a.decisions[-1]["source"] == "policy:accept"
