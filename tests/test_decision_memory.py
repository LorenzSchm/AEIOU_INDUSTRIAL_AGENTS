import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from arena_kit.cost import total_cost
from assessment_helpers import assessment_fields

from aeiou.agent import BuyerDecisionTimeout
from aeiou.decision_memory import build_context, message_ref, validate_assessment
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package


def setup_case():
    case = json.loads((Path(__file__).parent / "fixtures/sandbox-cases.json").read_text())[1]
    sid, other = case["suppliers"]
    return case, sid, other


def message(sid, price=18, payment=60, lead=35, status="open"):
    return {"from": sid, "body": "Our quotation.", "status": status,
            "term_sheets": [{"term_sheet_id": f"offer:{sid}:{price}:{payment}",
                             "terms": {"unit_price": price, "payment_days": payment,
                                       "lead_time_days": lead, "incoterm": "DAP"}}]}


def send(payload, sid):
    return {"supplier_id": sid, "action": "send", "body": "Please clarify your delivery terms.",
            "term_sheets": [], **assessment_fields(payload, sid)}


def test_ledger_exposes_cheaper_price_but_more_expensive_package_and_buyer_concession():
    case, sid, other = setup_case()
    threads = {sid: [message(sid), message("buyer", 16), message(sid, 17.99, 14, 42), message("buyer", 17)],
               other: [message(other, 12, lead=49)]}
    context = build_context(case, threads, {}, {})
    ledger = context["suppliers"][sid]
    assert ledger["supplier_change"]["cost_delta"] > 0  # lower price loses on payment/delivery
    assert ledger["buyer_change"]["cost_delta"] > 0
    assert {item["term"] for item in ledger["supplier_change"]["term_changes"]} == {
        "unit_price", "payment_days", "lead_time_days"}
    assert ledger["current_best_valid_offer"]["cost"] == total_cost(case, threads[sid][2]["term_sheets"][0]["terms"])
    assert context["suppliers"][other]["current_best_valid_offer"] is None  # 49 days violates a must-have


def test_history_survives_window_but_withdrawn_supplier_is_not_a_live_comparison():
    case, sid, other = setup_case()
    events = [message(sid, 18 - i / 10) for i in range(20)]
    threads = {sid: events, other: [message(other, 15), {"from": other, "status": "walked_away", "body": "No bid."}]}
    remembered = {"next_move": "Earlier conclusion"}
    context = build_context(case, threads, {}, {other: remembered})
    assert len(context["suppliers"][sid]["offer_history"]) == 20
    assert message_ref(sid, 0) in context["sources"]
    assert context["suppliers"][other]["live"] is False
    assert context["suppliers"][other]["current_best_valid_offer"] is None
    context["suppliers"][other]["previous_assessment"]["next_move"] = "Changed"
    assert remembered["next_move"] == "Earlier conclusion"
    threads[sid].append({"from": sid, "status": "open", "body": "Our previous quotation is withdrawn."})
    assert build_context(case, threads, {}, {})["suppliers"][sid]["current_best_valid_offer"] is None


@pytest.mark.parametrize("problem", ["stale", "invented_ref", "supplier_as_fact", "missing_latest", "missing"])
def test_assessment_rejects_stale_or_ungrounded_output(problem):
    case, sid, _ = setup_case()
    context = build_context(case, {sid: [message(sid), message(sid, 17)]}, {}, {})
    action = send({"decision_context": context}, sid)
    assessment = action["assessment"]
    if problem == "stale":
        assessment["observed_message"] = message_ref(sid, 0)
    elif problem == "invented_ref":
        assessment["evidence"][0]["source_refs"] = ["made-up"]
    elif problem == "supplier_as_fact":
        assessment["evidence"][0]["kind"] = "public_record"
    elif problem == "missing_latest":
        assessment["evidence"][0]["source_refs"] = [message_ref(sid, 0)]
    else:
        del action["assessment"]
    with pytest.raises(ValueError):
        validate_assessment(action, context)


def test_weighted_cost_claim_checks_share_and_exact_email_number():
    case, sid, _ = setup_case()
    research = {"indices": [{"series": "Copper", "weight": .2, "change_pct": 47.7,
                             "weighted_change_pct": 9.54, "refs": ["idx:start", "idx:end"]}]}
    context = build_context(case, {sid: [message(sid)]}, research, {})
    action = send({"decision_context": context}, sid)
    action["body"] = "The weighted contribution is 9.54 percentage points."
    action["numeric_claims"] = [{"calculation_id": "index:0:weighted_change_pct", "value": 9.54,
                                 "quote": action["body"]}]
    _, checks = validate_assessment(action, context)
    assert checks[0]["value"] == 9.54
    action["numeric_claims"][0]["value"] = 47.7
    with pytest.raises(ValueError, match="computed value is 9.54"):
        validate_assessment(action, context)
    action["numeric_claims"][0]["value"] = 9.54
    action["body"] = action["numeric_claims"][0]["quote"] = "The contribution is 47.7 percentage points."
    with pytest.raises(ValueError, match="declared number"):
        validate_assessment(action, context)


def test_each_reply_updates_memory_with_one_call_and_no_private_fields_sent():
    case, sid, other = setup_case()
    threads = {sid: [message(sid)], other: [message(other)]}
    payloads = []

    class Backend:
        provider = "openai"

        def complete(self, system, user):
            payload = json.loads(user)
            payloads.append(payload)
            selected = [sid, other, sid][len(payloads) - 1]
            action = send(payload, selected)
            action["assessment"]["adaptation"] = f"Assessment {len(payloads)}"
            return json.dumps(action)

    spec = PromptSpec("eval", "My exact strategy.", assessment_enabled=True)
    agent = PromptLabAgent(package(), Backend(), spec)
    for price in [17, 16, 15]:
        move = agent.decide(case, threads)
        chosen = move["supplier_id"]
        assert set(move["message"]) == {"subject", "body", "term_sheets"}
        assert agent.decisions[-1]["message_index"] == len(threads[chosen])
        threads[chosen].extend([{**move["message"], "from": "buyer"}, message(chosen, price)])
    assert len(payloads) == 3
    assert payloads[2]["decision_context"]["suppliers"][sid]["previous_assessment"]["adaptation"] == "Assessment 1"
    assert payloads[2]["decision_context"]["suppliers"][other]["previous_assessment"]["adaptation"] == "Assessment 2"
    assert agent.supplier_assessments[sid]["adaptation"] == "Assessment 3"
    assert all(d["source"] == "openai:eval" for d in agent.decisions)
    assert agent.decisions[-1]["economics"]["supplier_change"]["cost_delta"] < 0
    fresh = PromptLabAgent(package(), Backend(), spec)
    fresh.prepare(case)
    assert fresh.supplier_assessments == {}
    different = copy.deepcopy(case)
    different["case_id"] = "case:another"
    agent.prepare(different)
    assert agent.supplier_assessments == {}


@pytest.mark.parametrize("repair_time", [5, 11])
def test_assessment_repair_keeps_same_deadline_and_only_commits_valid_memory(monkeypatch, repair_time):
    case, sid, _ = setup_case()
    threads = {sid: [message(sid)]}
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr("aeiou.prompt_protocol.time", SimpleNamespace(monotonic=lambda: clock.now))
    deadlines = []

    class Backend:
        def complete_before(self, system, user, deadline):
            deadlines.append(deadline)
            payload = json.loads(user)
            action = send(payload, sid)
            if len(deadlines) == 1:
                clock.now += 40
                action["assessment"]["observed_message"] = "stale"
            else:
                clock.now += repair_time
                assert "latest reply" in payload["action_repair"]["error"]
                assert payload["decision_context"]["suppliers"][sid]["previous_assessment"] is None
            return json.dumps(action)

    agent = PromptLabAgent(package(), Backend(), PromptSpec("eval", "Negotiate", assessment_enabled=True))
    if repair_time == 5:
        agent.decide(case, threads)
        assert agent.supplier_assessments[sid]["observed_message"] == message_ref(sid, 0)
        assert len(agent.decisions) == 1
    else:
        with pytest.raises(BuyerDecisionTimeout):
            agent.decide(case, threads)
        assert agent.decisions == [] and agent.supplier_assessments == {}
    assert deadlines == [150, 150]


def test_two_invalid_assessments_send_nothing_and_do_not_replace_memory():
    case, sid, _ = setup_case()

    class Backend:
        def complete(self, system, user):
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Hello."})

    agent = PromptLabAgent(package(), Backend(), PromptSpec("eval", "Negotiate", assessment_enabled=True))
    with pytest.raises(ContractError, match="Two invalid outputs"):
        agent.decide(case, {sid: [message(sid)]})
    assert agent.supplier_assessments == {} and agent.decisions == []
