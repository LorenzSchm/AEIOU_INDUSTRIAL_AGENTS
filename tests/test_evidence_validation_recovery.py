import copy
import json

import pytest
from assessment_helpers import assessment_fields
from test_p18_above_70 import agent, offer

from aeiou.decision_memory import normalize_evidence_labels, validate_assessment


def action_for(payload, sid, terms):
    return {
        "supplier_id": sid,
        "action": "send",
        "body": "Thank you. Would you consider this package?",
        "term_sheets": [{**terms, "unit_price": 4.72}],
        **assessment_fields(payload, sid),
    }


def test_recent_index_inputs_are_delivered_and_label_repairs_need_no_model_retry():
    case, sid, threads = offer(5.40)
    threads[sid][0]["body"] += " AS-14404 rose 5.9 % in twelve months and is 45 % of our cost."
    terms = threads[sid][-1]["term_sheets"][0]["terms"]
    captured = []

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            ctx = payload["decision_context"]
            for ref in ("idx:AS-14404@2026-06", "idx:AS-14404@2026-09"):
                assert ctx["sources"][ref]["available"]
                assert ref in payload["research"]["facts"]
            action = action_for(payload, sid, terms)
            action["assessment"]["evidence"][0]["kind"] = "public_record"
            action["assessment"]["evidence"].extend(
                [
                    {
                        "claim": (
                            "The supplier shares support a conditional contribution calculation, not a verified floor."
                        ),
                        "kind": "public_record",
                        "source_refs": [f"weighted-claims:{sid}:0"],
                        "confidence": "high",
                    },
                    {
                        "claim": "The offer and case data inform our proposal.",
                        "kind": "public_record",
                        "source_refs": [ctx["suppliers"][sid]["latest_message_ref"], "case"],
                        "confidence": "high",
                    },
                ]
            )
            calc = ctx["calculations"]["p18:as_recent_change_pct"]
            sentence = f"AS-14404 changed by {calc['value']:.2f}% from June to September 2026."
            action["body"] += " " + sentence
            action["numeric_claims"] = [
                {"calculation_id": "p18:as_recent_change_pct", "value": calc["value"], "quote": sentence}
            ]
            captured.append(copy.deepcopy(action))
            return json.dumps(action)

    backend = Backend()
    a = agent(backend)
    move = a.decide(case, threads)
    assert backend.calls == 1 and not a.audit["contract_rejections"]
    assert move["message"]["body"] == captured[0]["body"]
    assert move["message"]["term_sheets"][0]["terms"] == captured[0]["term_sheets"][0]
    decision = a.decisions[0]
    assert len(decision["assessment_label_repairs"]) == 3
    assert [e["kind"] for e in decision["assessment"]["evidence"]] == ["supplier_claim", "inference", "inference"]
    assert decision["assessment"]["evidence"][1]["confidence"] == "medium"
    assert str(a.acceptance_floor.minimum_score) == "70.01"


@pytest.mark.parametrize("ref", ["buyer-record:p18:fy2026-account-invoices", "invented:source"])
def test_unread_or_unknown_evidence_is_not_made_valid_by_label_repair(ref):
    case, sid, threads = offer(5.4)
    a = agent()
    a.prepare(case)
    payload = json.loads(a.render_lab(case, threads))
    action = action_for(payload, sid, threads[sid][-1]["term_sheets"][0]["terms"])
    action["assessment"]["evidence"].append(
        {
            "claim": "A claim without visible supporting content.",
            "kind": "public_record",
            "source_refs": [ref],
            "confidence": "high",
        }
    )
    original = copy.deepcopy(action)
    normalized, repairs = normalize_evidence_labels(action, payload["decision_context"])
    assert normalized == original == action and repairs == []
    with pytest.raises(ValueError, match=ref):
        validate_assessment(normalized, payload["decision_context"])


def test_failed_attempts_persist_source_diagnostics_and_repair_input():
    case, sid, threads = offer(5.4)
    ref = "buyer-record:p18:fy2026-account-invoices"

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 2:
                assert ref in payload["action_repair"]["error"]
                source = payload["action_repair"]["rejected_evidence"][-1]["sources"][0]
                assert {k: source[k] for k in ("ref", "kind", "available")} == {
                    "ref": ref,
                    "kind": "public_record",
                    "available": False,
                }
                assert source["delivery"] == "deferred" and source["retrievable"]
            action = action_for(payload, sid, threads[sid][-1]["term_sheets"][0]["terms"])
            action["assessment"]["evidence"].append(
                {"claim": "Unread invoices.", "kind": "public_record", "source_refs": [ref], "confidence": "high"}
            )
            return json.dumps(action)

    a = agent(Backend())
    with pytest.raises(ValueError, match="Two invalid outputs"):
        a.decide(case, threads)
    assert a.decisions == [] and len(a.audit["validation_failures"]) == 2
    assert a.audit["validation_failures"][1]["evidence"][-1]["sources"][0]["ref"] == ref


def test_label_normalization_never_promotes_inference_or_rewrites_numeric_values():
    case, sid, threads = offer(5.4)
    a = agent()
    a.prepare(case)
    payload = json.loads(a.render_lab(case, threads))
    action = action_for(payload, sid, threads[sid][-1]["term_sheets"][0]["terms"])
    action["assessment"]["evidence"][0]["kind"] = "inference"
    action["body"] = "The index changed by 99%."
    action["numeric_claims"] = [{"calculation_id": "p18:as_recent_change_pct", "value": 99, "quote": action["body"]}]
    normalized, repairs = normalize_evidence_labels(action, payload["decision_context"])
    assert normalized == action and repairs == []
    with pytest.raises(ValueError, match="computed value"):
        validate_assessment(normalized, payload["decision_context"])
