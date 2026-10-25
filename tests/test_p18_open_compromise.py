import copy
import json

import pytest
from arena_kit.cost import total_cost

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.execution_evidence import apply_menu_evidence
from aeiou.execution_policy import controlled_action, controlled_assessment
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package

STRATEGY = (ROOT / "prompts/p18-diagnostic-v21.txt").read_text()
POLICY = json.loads((ROOT / "config/p18-open-compromise.json").read_text())["case:P18"]
SID = "sup:70740"
FIXTURES = ["p18-v19-verified-offer.json", "p18-v20-fourteen-day-counter.json"]


class NoModel:
    def complete(self, *args):
        raise AssertionError("This policy decision must not call the model")


def agent(policy=None):
    return PromptLabAgent(package(), NoModel(), PromptSpec(
        "v21-offline", STRATEGY, assessment_enabled=True,
        execution_policy=copy.deepcopy(POLICY if policy is None else policy)))


def recorded(name=FIXTURES[1], through=5):
    run = json.loads((ROOT / "tests/fixtures" / name).read_text())
    return run["case"], {SID: run["threads"][SID][:through]}


def control(a, case, threads):
    return controlled_action(case, threads, a.decision_protocol_state(case, threads),
                             a.acceptance_floor, a.execution_policy)


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("duration", [24, 36])
def test_recorded_14_and_30_day_counters_get_one_45_day_proposal_with_checked_as(fixture, duration):
    case, threads = recorded(fixture)
    for sheet in threads[SID][-1]["term_sheets"]:
        sheet["terms"]["contract_months"] = duration
    before = copy.deepcopy(threads)
    a = agent()
    move = a.decide(case, threads)
    terms = move["message"]["term_sheets"][0]["terms"]
    assert terms == {**POLICY["open_payment_compromise"], "contract_months": duration}
    assert total_cost(case, terms) == 244241.61
    assert a.acceptance_floor.allows(total_cost(case, terms), terms)
    body = move["message"]["body"]
    assert "EUR 5.09/kg, payment within 45 days net" in body
    assert "AS-14404 changed by -5.49% from 2026-06 to 2026-09" in body
    assert "PPI-C2431" not in body
    assert not any(private in body.lower() for private in ("score", "244400", "70.01", "p18:"))
    assert "new proposal for your approval" in body
    decision = a.decisions[-1]
    assert decision["source"] == "policy:payment-compromise"
    assert decision["execution_evidence"]["status"] == "included"
    assert decision["numeric_checks"][0]["calculation_id"] == "p18:as_recent_change_pct"
    assert decision["model_requests"] == []
    assert threads == before


def test_compromise_is_not_sent_before_menu_or_repeated_after_reconstruction():
    case, threads = recorded(through=1)
    assert control(agent(), case, threads) is None
    case, threads = recorded(through=3)
    a = agent()
    assert a.decide(case, threads)["message"]["body"].startswith("Thank you for working through the options")
    case, threads = recorded()
    move = agent().decide(case, threads)
    move["message"]["body"] = "Same complete compromise, reworded."
    reply = copy.deepcopy(threads[SID][-1])
    reply["body"] = "Thank you. We can offer EUR 5.15 on the previous payment structure."
    for sheet in reply["term_sheets"]:
        sheet["terms"].update(contract_months=24, unit_price=5.15)
    threads[SID].extend([{**move["message"], "from": "buyer"}, reply])
    assert control(agent(), case, threads) is None


@pytest.mark.parametrize("restriction", [
    "We cannot accept 45-day payment.", "Payment is limited to a maximum of 30 days.",
    "Payment must remain at 14 days net.", "45 days is not an option.",
    "Das Zahlungsziel ist nicht verhandelbar.",
])
def test_explicit_payment_restriction_prevents_forced_compromise(restriction):
    case, threads = recorded()
    threads[SID][-1]["body"] += "\n" + restriction
    assert control(agent(), case, threads) is None
    threads[SID][-1]["body"] = "Thank you for considering our offer."
    threads[SID][0]["body"] += "\n" + restriction
    assert control(agent(), case, threads) is None


@pytest.mark.parametrize("damage", ["short_duration", "different_clause", "no_rounds", "higher_cost"])
def test_open_compromise_never_ignores_package_or_round_limits(damage):
    case, threads = recorded()
    a = agent()
    if damage == "no_rounds":
        case["round_limit"] = 2
    elif damage == "higher_cost":
        case["buyer"]["index_paths"]["AS-14404"] = .20
    else:
        for sheet in threads[SID][-1]["term_sheets"]:
            if damage == "short_duration":
                sheet["terms"]["contract_months"] = 12
            else:
                sheet["terms"]["index_clause"]["deadband_pct"] = 0
    assert control(a, case, threads) is None


@pytest.mark.parametrize("status", ["open", "final_offer"])
def test_qualifying_exact_offer_is_accepted_before_compromise_or_final_stop(status):
    case, threads = recorded(through=7)
    threads[SID][-1]["status"] = status
    threads[SID][-1]["term_sheets"][0]["terms"] = {
        **POLICY["open_payment_compromise"], "contract_months": 36}
    a = agent()
    assert a.decide(case, threads)["message"] == {
        "action": {"type": "accept", "term_sheet_id": "offer:70740-3"}}
    assert a.decisions[-1]["source"] == "policy:accept"


@pytest.mark.parametrize("fixture", FIXTURES)
def test_nonqualifying_final_stops_without_model_or_counter_despite_remaining_rounds(fixture):
    case, threads = recorded(fixture, through=7)
    before = copy.deepcopy(threads)
    a = agent()
    state = a.decision_protocol_state(case, threads)["suppliers"][SID]
    assert state["rounds_left"] == 7
    assert state["legal_actions"] == ["stop"]
    with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
        a.decide(case, threads)
    assert not a.audit["model_requests"] and not a.audit["walk_aways"]
    assert a.audit["terminal_limits"][-1]["source"] == "policy:final-offer-stop"
    assert threads == before
    late_change = {**POLICY["early_package_alternatives"]["packages"][2], "contract_months": 36}
    for action in ({"action": "send", "body": "Please approve 60 days.", "term_sheets": [late_change]},
                   {"action": "send", "body": "Could your owner improve this?", "term_sheets": []},
                   {"action": "confirm", "term_sheet_id": "offer:70740-3"}):
        with pytest.raises(ContractError, match="further negotiation is disabled"):
            a.contract(case, threads, {"supplier_id": SID, **action})


def test_qualifying_advertised_alternative_does_not_trigger_final_stop_or_false_acceptance():
    case, threads = recorded(through=7)
    terms = {**POLICY["open_payment_compromise"], "contract_months": 36}
    final = threads[SID][-1]
    final["standing_offer_id"] = "offer:70740-3"
    final["term_sheets"].append({"term_sheet_id": "advertised:45", "terms": terms})
    a = agent()
    assert control(a, case, threads) is None
    with pytest.raises(ContractError, match="standing offer"):
        a.contract(case, threads, {"supplier_id": SID, "action": "accept", "term_sheet_id": "advertised:45"})
    assert "send" in a.decision_protocol_state(case, threads)["suppliers"][SID]["legal_actions"]


def test_withdrawn_thread_remains_terminal():
    case, threads = recorded(through=9)
    with pytest.raises(ContractError, match="No live supplier remains"):
        agent().decide(case, threads)


@pytest.mark.parametrize("damage,reason", [
    ("unread", "sources_not_delivered"), ("mismatch", "calculation_mismatch"), ("used", "already_sent"),
])
def test_as_evidence_is_checked_against_actual_delivered_sources_and_sent_history(damage, reason):
    case, threads = recorded()
    a = agent()
    a.prepare(case)
    payload = json.loads(a.render_lab(case, threads))
    action = control(a, case, threads)
    action.update(controlled_assessment(action, payload["decision_context"]))
    if damage == "unread":
        payload["decision_context"]["sources"]["idx:AS-14404@2026-09"]["available"] = False
    elif damage == "mismatch":
        payload["decision_context"]["calculations"]["p18:as_recent_change_pct"]["value"] = -6
    else:
        threads[SID][1]["body"] += " AS-14404 between 2026-06 and 2026-09 declined."
    result, audit = apply_menu_evidence(action, payload, threads[SID], POLICY)
    assert audit["status"] == "skipped" and audit["reason"] == reason
    assert result == action and result["numeric_claims"] == []


@pytest.mark.parametrize("field,value", [("unit_price", 5.10), ("payment_days", 20),
                                         ("payment_days", True), ("index_clause", {}),
                                         ("unit_price", float("nan"))])
def test_bad_or_nonqualifying_compromise_is_rejected_before_start(field, value):
    policy = copy.deepcopy(POLICY)
    policy["open_payment_compromise"][field] = value
    case, _ = recorded()
    with pytest.raises(ValueError):
        agent(policy).prepare(case)


def test_invalid_final_stop_and_evidence_settings_fail_validation():
    with pytest.raises(ValueError, match="stop_after_nonqualifying_final"):
        agent({**POLICY, "stop_after_nonqualifying_final": "true"})
    with pytest.raises(ValueError, match="compromise_evidence_argument"):
        agent({**POLICY, "compromise_evidence_argument": "ppi_recent_change_pct"})
