import copy
import json

import pytest
from arena_kit.cost import total_cost
from assessment_helpers import assessment_fields

from aeiou.acceptance_floor import AcceptanceFloor
from aeiou.config import ROOT
from aeiou.price_planner import payment_tradeoffs
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package

STRATEGY = (ROOT / "prompts/p18-diagnostic-v20.txt").read_text()
POLICY = json.loads((ROOT / "config/p18-approval-opening.json").read_text())["case:P18"]
FLOOR = AcceptanceFloor.from_prompt(STRATEGY)
CASE = json.loads((ROOT / "data/package/cases/practice/P18.json").read_text())
RUN = ROOT / "tests/fixtures/p18-v19-verified-offer.json"
CLAUSE = {"series": "AS-14404", "material_share": .45, "deadband_pct": 5, "base_month": "2026-08"}


def agent(backend):
    return PromptLabAgent(package(), backend, PromptSpec(
        "v20-offline", STRATEGY, assessment_enabled=True, execution_policy=copy.deepcopy(POLICY)))


def test_all_payment_options_are_priced_and_keep_exact_clause_duration_and_limits():
    terms = {"unit_price": 5.10, "payment_days": 30, "contract_months": 36, "index_clause": CLAUSE}
    original = copy.deepcopy(terms)
    rows = payment_tradeoffs(CASE, terms, FLOOR, minimum_contract_months=24)
    assert [r["terms"]["payment_days"] for r in rows] == [14, 30, 45, 60, 90]
    assert terms == original
    by_days = {r["terms"]["payment_days"]: r for r in rows}
    assert by_days[45]["terms"]["unit_price"] == 5.09
    assert by_days[45]["complete_cost"] == 244241.61
    assert by_days[45]["estimated_score"] == 70.2292
    assert by_days[45]["payment_days_change"] == 15
    assert by_days[45]["price_change"] == -.01
    assert by_days[30]["terms"]["unit_price"] == 5.07
    for row in rows:
        proposed = row["terms"]
        assert proposed["index_clause"] == CLAUSE and proposed["contract_months"] == 36
        assert FLOOR.allows(row["complete_cost"], proposed)
        assert proposed["unit_price"] <= terms["unit_price"]
        assert "requires supplier approval" in row["status"]
        if proposed["unit_price"] < min(terms["unit_price"], float(FLOOR.maximum_unit_price)):
            assert not FLOOR.allows_cost(total_cost(CASE, {**proposed, "unit_price": proposed["unit_price"] + .01}))
    assert payment_tradeoffs(CASE, {**terms, "contract_months": 12}, FLOOR, minimum_contract_months=24) == []


def test_tradeoffs_do_not_invent_options_when_payment_is_not_negotiable():
    case = copy.deepcopy(CASE)
    case["negotiable"].remove("payment_days")
    assert payment_tradeoffs(case, {"unit_price": 5.10}, FLOOR) == []
    assert payment_tradeoffs(CASE, {}, None) == []


def test_revised_opening_passes_the_real_action_contract_without_changing_anchor():
    run = json.loads(RUN.read_text())
    sid = "sup:70740"
    body = STRATEGY.split("Opening email:\n\n", 1)[1].split("\n\nThese prices", 1)[0]

    class Backend:
        def complete(self, system, user):
            payload = json.loads(user)
            for ref in ("doc:20260812-70740-M3", "po:4500014022-10", "po:4500014427-10"):
                assert payload["decision_context"]["sources"][ref]["available"]
            return json.dumps({"supplier_id": sid, "action": "send", "body": body,
                               "term_sheets": [{"unit_price": 4.60, "payment_days": 30, "contract_months": 36,
                                                "index_clause": {**CLAUSE, "deadband_pct": 0}}],
                               **assessment_fields(payload, sid)})

    a = agent(Backend())
    move = a.decide(run["case"], {sid: run["threads"][sid][:1]})
    assert move["message"]["body"] == body
    assert move["message"]["term_sheets"][0]["terms"]["unit_price"] == 4.60
    assert "committee" in body
    assert "idx:PPI" not in body and "idx:AS" not in body
    assert "score" not in body.lower() and "244400" not in body


def test_new_open_stage_compromise_reaches_the_send_and_final_510_30_still_fails():
    run = json.loads(RUN.read_text())
    sid = "sup:70740"
    proposed = {"unit_price": 5.09, "payment_days": 45, "contract_months": 36, "index_clause": CLAUSE}

    class Backend:
        def complete(self, system, user):
            payload = json.loads(user)
            offer = next(o for o in payload["price_plan"]["suppliers"][sid]["offers"]
                         if o["unit_price"] == 5.20)
            trade = next(t for t in offer["qualifying_payment_tradeoffs"] if t["terms"]["payment_days"] == 45)
            assert trade["terms"] == proposed
            fields = assessment_fields(payload, sid)
            fields["numeric_claims"] = [{"calculation_id": "p18:as_recent_change_pct", "value": -5.49,
                                          "quote": "changed by -5.49%"}]
            body = ("Thank you. We can propose EUR 5.09/kg with 45-day payment on the same 36-month, 5% structure. "
                    "AS-14404 changed by -5.49% from 2026-06 to 2026-09 "
                    "(idx:AS-14404@2026-06, idx:AS-14404@2026-09). "
                    "This recent window can coexist with the annual rise. Could you approve the complete package?")
            return json.dumps({"supplier_id": sid, "action": "send", "body": body,
                               "term_sheets": [proposed], **fields})

    a = agent(Backend())
    move = a.decide(run["case"], {sid: run["threads"][sid][:5]})
    assert move["message"]["term_sheets"][0]["terms"] == proposed
    assert a.decisions[-1]["numeric_checks"][0]["calculation_id"] == "p18:as_recent_change_pct"
    with pytest.raises(ContractError, match="execution controls"):
        a.contract(run["case"], {sid: run["threads"][sid][:7]},
                   {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:70740-3"})


def test_exact_qualifying_45_day_reply_is_accepted_without_another_model_call():
    run = json.loads(RUN.read_text())
    sid = "sup:70740"
    threads = copy.deepcopy(run["threads"])
    threads[sid] = threads[sid][:7]
    threads[sid][-1]["term_sheets"][0]["terms"].update(unit_price=5.09, payment_days=45)

    class NoModel:
        def complete(self, *args):
            raise AssertionError("Qualifying offer must be accepted first")

    move = agent(NoModel()).decide(run["case"], threads)
    assert move["message"] == {"action": {"type": "accept", "term_sheet_id": "offer:70740-3"}}
