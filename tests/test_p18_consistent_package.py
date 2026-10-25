"""Controller/transport checks with synthetic replies; never evidence of supplier acceptance."""

import copy
import json

import pytest
from arena_kit.cost import total_cost
from assessment_helpers import assessment_fields

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.execution_evidence import apply_menu_evidence
from aeiou.execution_policy import controlled_action, controlled_assessment
from aeiou.package_sequence import sequence_packages
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package
from aeiou.supplier_signals import supplier_signals

SID = "sup:70740"
POLICY = json.loads((ROOT / "config/p18-consistent-package.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v22.txt").read_text()


class NoModel:
    def complete(self, *args):
        raise AssertionError("The normal sequence must not call a model")


def agent(policy=None, backend=None):
    return PromptLabAgent(
        package(),
        backend or NoModel(),
        PromptSpec(
            "v22-offline",
            STRATEGY,
            assessment_enabled=True,
            execution_policy=copy.deepcopy(POLICY if policy is None else policy),
        ),
    )


def opening():
    run = json.loads((ROOT / "tests/fixtures/p18-v20-fourteen-day-counter.json").read_text())
    return run["case"], {SID: run["threads"][SID][:1]}


def reply(threads, move, price=5.40, status="open", **terms):
    threads[SID].append({**move["message"], "from": "buyer"})
    threads[SID].append(
        {
            "from": "sales@70740",
            "status": status,
            "body": "Thank you. Please review our current complete offer.",
            "term_sheets": [
                {
                    "term_sheet_id": f"offer:test-{len(threads[SID])}",
                    "terms": {**sequence_packages(POLICY)[0], "unit_price": price, **terms},
                }
            ],
        }
    )


def after_opening():
    case, threads = opening()
    reply(threads, agent().decide(case, threads))
    return case, threads


def control(a, case, threads):
    return controlled_action(
        case, threads, a.decision_protocol_state(case, threads), a.acceptance_floor, a.execution_policy
    )


def test_full_sequence_keeps_structure_injects_each_argument_once_and_accepts_exact_reply():
    case, threads = opening()
    stage_names = ["opening", "ppi_counter", "alloy_close"]
    arguments = ["po_price_history", "ppi_recent_change_pct", "as_recent_change_pct"]
    evidence = ["EUR 5.26/kg", "PPI-C2431 changed by -2.27%", "AS-14404 changed by -5.49%"]
    for i, price in enumerate([5.40, 5.20, 5.10]):
        a = agent()  # reconstruct from sent history before each stage
        move = a.decide(case, threads)
        assert move["message"]["term_sheets"] == [{"terms": sequence_packages(POLICY)[i]}]
        body = move["message"]["body"]
        assert evidence[i] in body
        assert all(text not in body for text in evidence[:i])
        assert "60 days" in body and "5% deadband" in body and "36-month" in body
        assert not any(text in body for text in ("Option A", "45 days", "score", "70.01", "244400"))
        decision = a.decisions[-1]
        assert decision["source"] == f"policy:package-sequence:{stage_names[i]}"
        assert decision["package_sequence_state"]["stage"] == stage_names[i]
        assert decision["execution_evidence"]["argument_id"] == arguments[i]
        assert decision["execution_evidence"]["status"] == "included"
        assert len(decision["numeric_checks"]) == (0 if i == 0 else 1)
        assert not decision["model_requests"]
        reply(threads, move, price=price, status="final_offer" if i == 2 else "open")
    a = agent()
    move = a.decide(case, threads)
    assert move["message"]["action"] == {
        "type": "accept",
        "term_sheet_id": threads[SID][-1]["term_sheets"][0]["term_sheet_id"],
    }
    assert a.decisions[-1]["source"] == "policy:accept"
    assert total_cost(case, threads[SID][-1]["term_sheets"][0]["terms"]) == 243915.66


@pytest.mark.parametrize("status", ["open", "final_offer"])
def test_acceptance_precedes_sequence_even_for_different_qualifying_structure(status):
    case, threads = after_opening()
    threads[SID][-1]["status"] = status
    threads[SID][-1]["term_sheets"][0]["terms"].update(unit_price=5.09, payment_days=45, contract_months=24)
    a = agent()
    assert a.decide(case, threads)["message"]["action"]["type"] == "accept"
    assert a.decisions[-1]["source"] == "policy:accept"


@pytest.mark.parametrize("days", [14, 30, 45, 60])
def test_low_score_final_stops_without_late_counter_or_model(days):
    case, threads = after_opening()
    threads[SID][-1]["status"] = "final_offer"
    threads[SID][-1]["term_sheets"][0]["terms"].update(unit_price=5.20, payment_days=days)
    a = agent()
    with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
        a.decide(case, threads)
    assert not a.audit["model_requests"]
    assert a.audit["terminal_limits"][-1]["protocol_state"]["suppliers"][SID]["rounds_left"] == 9


@pytest.mark.parametrize(
    "restriction",
    [
        "We cannot accept 60-day payment.",
        "Payment must remain at 30 days net.",
        "We cannot accept a 5% deadband.",
        "We only offer a 24-month contract.",
    ],
)
def test_explicit_objections_defer_with_visible_reason_instead_of_forcing_sequence(restriction):
    case, threads = after_opening()
    threads[SID][-1]["body"] += " " + restriction
    a = agent()
    a.prepare(case)
    assert control(a, case, threads) is None
    payload = json.loads(a.render_lab(case, threads))
    state = payload["package_sequence_state"][SID]
    assert state["reason"] == "explicit_structure_restriction" and state["source_quote"] in restriction


def test_a_short_payment_quote_alone_does_not_override_our_package_or_invent_a_refusal():
    case, threads = after_opening()
    threads[SID][-1]["term_sheets"][0]["terms"]["payment_days"] = 30
    a = agent()
    assert a.decide(case, threads)["message"]["term_sheets"][0]["terms"]["payment_days"] == 60


def test_rewording_does_not_restart_sequence_and_three_stages_are_not_repeated():
    case, threads = opening()
    for _ in range(3):
        move = agent().decide(case, threads)
        move["message"]["body"] = "Reworded complete proposal."
        reply(threads, move)
    a = agent()
    a.prepare(case)
    assert control(a, case, threads) is None
    assert json.loads(a.render_lab(case, threads))["package_sequence_state"][SID]["reason"] == "sequence_complete"


def test_old_menu_history_is_not_silently_reinterpreted_as_new_sequence():
    run = json.loads((ROOT / "tests/fixtures/p18-v19-verified-offer.json").read_text())
    threads = {SID: run["threads"][SID][:5]}
    a = agent()
    a.prepare(run["case"])
    assert control(a, run["case"], threads) is None
    state = json.loads(a.render_lab(run["case"], threads))["package_sequence_state"][SID]
    assert state["reason"] == "history_does_not_match_sequence"


def test_qualifying_nonstanding_alternative_gets_confirmation_instead_of_a_counter_or_acceptance():
    case, threads = after_opening()
    last = threads[SID][-1]
    last["standing_offer_id"] = last["term_sheets"][0]["term_sheet_id"]
    last["term_sheets"].append({"term_sheet_id": "advertised:good", "terms": sequence_packages(POLICY)[2]})
    a = agent()
    assert control(a, case, threads) is None
    with pytest.raises(ContractError, match="standing offer"):
        a.contract(case, threads, {"supplier_id": SID, "action": "accept", "term_sheet_id": "advertised:good"})


@pytest.mark.parametrize("damage", ["unread", "wrong_part", "wrong_price", "wrong_supplier", "wrong_price_unit"])
def test_opening_cannot_invent_or_misattribute_purchase_order_evidence(damage):
    case, threads = opening()
    a = agent()
    a.prepare(case)
    payload = json.loads(a.render_lab(case, threads))
    action = control(a, case, threads)
    action.update(controlled_assessment(action, payload["decision_context"]))
    ref = "po:4500014022-10"
    if damage == "unread":
        payload["decision_context"]["sources"][ref]["available"] = False
    else:
        key, value = {
            "wrong_part": ("part", "part:other"),
            "wrong_price": ("unit_price", 5.10),
            "wrong_supplier": ("supplier", "sup:other"),
            "wrong_price_unit": ("price_unit", 100),
        }[damage]
        payload["research"]["facts"][ref][key] = value
    result, audit = apply_menu_evidence(action, payload, threads[SID], POLICY)
    assert audit["status"] == "skipped" and result == action
    assert "EUR 5.26" not in result["body"]


def test_missing_required_evidence_defers_to_model_without_sending_the_unsubstantiated_script(monkeypatch):
    seen = []

    class Fallback:
        def complete(self, system, user):
            payload = json.loads(user)
            seen.append(payload["execution_control_deferral"])
            return json.dumps(
                {
                    "supplier_id": SID,
                    "action": "send",
                    "body": "Could you confirm the quotation date?",
                    "term_sheets": [],
                    **assessment_fields(payload, SID),
                }
            )

    case, threads = opening()
    a = agent(backend=Fallback())
    original = a.render_lab

    def missing(*args):
        payload = json.loads(original(*args))
        del payload["research"]["facts"]["po:4500014022-10"]
        return json.dumps(payload)

    monkeypatch.setattr(a, "render_lab", missing)
    move = a.decide(case, threads)
    assert seen[0]["reason"] == "sources_not_delivered" and seen[0]["deferred_to_model"]
    assert move["message"]["term_sheets"] == []
    assert "EUR 4.60" not in move["message"]["body"]
    assert a.decisions[-1]["execution_evidence"]["status"] == "skipped"


@pytest.mark.parametrize("damage", ["legacy", "bad_days", "bad_price", "bad_evidence", "bad_order", "no_final_stop"])
def test_bad_sequence_settings_fail_before_a_run(damage):
    policy = copy.deepcopy(POLICY)
    seq = policy["consistent_package_sequence"]
    if damage == "legacy":
        policy["one_final_counter"] = True
    elif damage == "bad_days":
        seq["payment_days"] = 20
    elif damage == "bad_price":
        seq["stages"][-1]["unit_price"] = 5.20
    elif damage == "bad_evidence":
        seq["stages"][1]["evidence_argument"] = "as_recent_change_pct"
    elif damage == "bad_order":
        seq["stages"][1]["unit_price"] = 4.50
    else:
        policy["stop_after_nonqualifying_final"] = False
    case, _ = opening()
    with pytest.raises(ValueError):
        agent(policy).prepare(case)


def test_sequence_requires_evidence_validation_and_never_sends_after_withdrawal():
    with pytest.raises(ValueError, match="assessment"):
        PromptLabAgent(package(), NoModel(), PromptSpec("bad", STRATEGY, execution_policy=POLICY))
    case, threads = opening()
    threads[SID][-1]["status"] = "walked_away"
    with pytest.raises(ContractError, match="No live supplier remains"):
        agent().decide(case, threads)


@pytest.mark.parametrize(
    "body,authority,blocked",
    [
        ("I am fully authorized to negotiate and conclude this agreement.", "supplier_confirmed", False),
        ("Our pricing committee has reviewed your request and authorized this offer.", "supplier_confirmed", False),
        ("This price requires pricing committee approval.", "approval_required_or_limited", True),
        (
            "I am fully authorized only up to 2%; further reductions require approval.",
            "approval_required_or_limited",
            True,
        ),
        ("I am not fully authorized to conclude this agreement.", "unspecified", False),
        ("Our committee has not authorized this offer.", "unspecified", False),
    ],
)
def test_confirmed_authority_is_not_treated_as_missing_approval(body, authority, blocked):
    case, threads = opening()
    threads[SID][-1]["body"] = body
    signals = supplier_signals(case, threads)[SID]
    assert signals["authority"] == authority
    assert ("approval_required" in signals["live_objections"]) == blocked
