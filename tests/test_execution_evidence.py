import copy
import json

import pytest
from test_negotiation_data_pipeline import NeverCall
from test_p18_package_alternatives import second_reply, standard_payment_agent

from aeiou.config import ROOT
from aeiou.decision_memory import validate_assessment
from aeiou.execution_evidence import apply_menu_evidence
from aeiou.execution_policy import controlled_action, controlled_assessment
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package

POLICY = json.loads((ROOT / "config/p18-evidence-menu.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v19.txt").read_text()
REFS = ["idx:PPI-C2431@2026-06", "idx:PPI-C2431@2026-08"]
CALC = "p18:ppi_recent_change_pct"


def agent(policy=None, assessment_enabled=True):
    return PromptLabAgent(
        package(),
        NeverCall(),
        PromptSpec(
            "p18-evidence-menu",
            STRATEGY,
            assessment_enabled=assessment_enabled,
            execution_policy=copy.deepcopy(POLICY if policy is None else policy),
        ),
    )


@pytest.fixture
def first_reply():
    case, sid, threads = second_reply()
    threads[sid] = threads[sid][:3]
    return case, sid, threads


@pytest.fixture
def prepared(first_reply):
    case, sid, threads = first_reply
    a = agent()
    a.prepare(case)
    payload = json.loads(a.render_lab(case, threads))
    action = controlled_action(case, threads, payload["protocol_state"], a.acceptance_floor, a.execution_policy)
    action.update(controlled_assessment(action, payload["decision_context"]))
    return action, payload, threads[sid]


def test_checked_ppi_reaches_actual_menu_email_without_model_or_package_changes(first_reply):
    case, sid, threads = first_reply
    a = agent()
    move = a.decide(case, threads)
    baseline = standard_payment_agent().decide(case, threads)
    assert move["message"]["term_sheets"] == baseline["message"]["term_sheets"]
    body = move["message"]["body"]
    assert "PPI-C2431 changed by -2.27% from 2026-06 to 2026-08" in body
    assert all(ref in body for ref in REFS)
    assert "can coexist with the annual increase" in body
    assert body.index("PPI-C2431 changed") < body.index("Option A:")
    assert all(private not in body for private in ("p18:", "70.01", "244400", "score"))
    decision = a.decisions[-1]
    assert decision["message_index"] == 3
    assert decision["source"] == "policy:package-alternatives"
    assert decision["execution_evidence"] == {
        "argument_id": "ppi_recent_change_pct",
        "source_refs": REFS,
        "calculation_id": CALC,
        "status": "included",
        "value": -2.27,
        "quote": "changed by -2.27%",
    }
    assert len(decision["numeric_checks"]) == 1
    check = decision["numeric_checks"][0]
    assert check["calculation_id"] == CALC and check["source_refs"] == REFS
    assert check["value"] == pytest.approx((129.1 / 132.1 - 1) * 100, abs=0.0001)
    assert decision["assessment"]["evidence"][-1]["kind"] == "public_record"
    assert not a.audit["contract_rejections"] and not a.audit["validation_failures"]
    assert set(move["message"]) == {"subject", "body", "term_sheets"}


@pytest.mark.parametrize("damage", ["absent_fact", "deferred", "supplier_claim", "absent_registry"])
def test_unavailable_sources_are_skipped_instead_of_sent(prepared, damage):
    action, payload, events = prepared
    if damage == "absent_fact":
        del payload["research"]["facts"][REFS[0]]
    elif damage == "deferred":
        payload["decision_context"]["sources"][REFS[0]]["available"] = False
    elif damage == "supplier_claim":
        payload["decision_context"]["sources"][REFS[0]]["kind"] = "supplier_claim"
    else:
        del payload["decision_context"]["sources"][REFS[0]]
    result, audit = apply_menu_evidence(action, payload, events, POLICY)
    assert result == action and result["numeric_claims"] == []
    assert audit["status"] == "skipped" and audit["reason"] == "sources_not_delivered"
    assert "PPI-C2431 changed" not in result["body"]
    validate_assessment(result, payload["decision_context"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", float("nan")),
        ("value", True),
        ("value", 0),
        ("value", "132.1"),
        ("month", "2025-06"),
        ("series", "AS-14404"),
    ],
)
def test_wrong_or_malformed_index_records_are_never_claimed(prepared, field, value):
    action, payload, events = prepared
    payload["research"]["facts"][REFS[0]][field] = value
    result, audit = apply_menu_evidence(action, payload, events, POLICY)
    assert result == action and audit["reason"] == "invalid_index_records"


@pytest.mark.parametrize("damage", ["value", "references", "absent"])
def test_recalculation_must_agree_with_registered_calculation(prepared, damage):
    action, payload, events = prepared
    calc = payload["decision_context"]["calculations"][CALC]
    if damage == "value":
        calc["value"] = -5.49
    elif damage == "references":
        calc["source_refs"] = ["idx:AS-14404@2026-06", "idx:AS-14404@2026-09"]
    else:
        del payload["decision_context"]["calculations"][CALC]
    result, audit = apply_menu_evidence(action, payload, events, POLICY)
    assert result == action and audit["reason"] == "calculation_mismatch"


def test_a_rising_window_is_not_presented_as_a_decline(prepared):
    action, payload, events = prepared
    payload["research"]["facts"][REFS[1]]["value"] = 135
    payload["decision_context"]["calculations"][CALC]["value"] = round((135 / 132.1 - 1) * 100, 4)
    result, audit = apply_menu_evidence(action, payload, events, POLICY)
    assert result == action and audit["reason"] == "window_is_not_a_decline"


@pytest.mark.parametrize(
    "body",
    [
        f"Please review {REFS[0]} and {REFS[1]}.",
        "The PPI-C2431 movement between 2026-06 and 2026-08 supports a review.",
    ],
)
def test_previous_buyer_argument_is_not_sent_as_new_evidence(first_reply, body):
    case, sid, threads = first_reply
    threads[sid][1]["body"] = body
    a = agent()
    result = a.decide(case, threads)
    assert "PPI-C2431 changed" not in result["message"]["body"]
    assert a.decisions[-1]["execution_evidence"]["reason"] == "already_sent"
    assert not a.decisions[-1]["numeric_checks"]


def test_annual_supplier_claim_does_not_hide_unused_recent_window(first_reply):
    case, sid, threads = first_reply
    threads[sid][-1]["body"] = "PPI-C2431 rose 12.3% in twelve months and is 38% of our cost."
    a = agent()
    assert "changed by -2.27%" in a.decide(case, threads)["message"]["body"]
    assert a.decisions[-1]["execution_evidence"]["status"] == "included"


def test_eligible_acceptance_still_precedes_evidence(first_reply):
    case, sid, threads = first_reply
    threads[sid][-1]["term_sheets"][0]["terms"] = {
        **POLICY["early_package_alternatives"]["packages"][0],
        "contract_months": 36,
    }
    a = agent()
    assert a.decide(case, threads)["message"]["action"]["type"] == "accept"
    assert "execution_evidence" not in a.decisions[-1]


def test_old_policy_and_inputs_are_unchanged(prepared):
    action, payload, events = prepared
    original = copy.deepcopy((action, payload, events))
    result, audit = apply_menu_evidence(
        action, payload, events, {k: v for k, v in POLICY.items() if k != "menu_evidence_argument"}
    )
    assert result == action and audit is None
    enriched, _ = apply_menu_evidence(action, payload, events, POLICY)
    validate_assessment(enriched, payload["decision_context"])
    assert (action, payload, events) == original


@pytest.mark.parametrize("value", [True, None, "", "order_prices", "as_recent_change_pct"])
def test_evidence_opt_in_rejects_unsupported_arguments(value):
    with pytest.raises(ValueError, match="menu_evidence_argument"):
        agent(policy={**POLICY, "menu_evidence_argument": value})


def test_evidence_requires_a_menu_and_normal_validation():
    with pytest.raises(ValueError, match="menu_evidence_argument"):
        agent(policy={k: v for k, v in POLICY.items() if k != "early_package_alternatives"})
    with pytest.raises(ValueError, match="numeric-claim validation"):
        agent(assessment_enabled=False)
