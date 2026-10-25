import copy
import json
from decimal import Decimal

import pytest

from aeiou.acceptance_floor import AcceptanceFloor
from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package
from aeiou.runner import RecordingTable

POLICY = {"case_id": "case:P18", "c_open": 295173.23, "c_best": 222651.27, "minimum_score": 80}
STRATEGY = "<acceptance_floor>" + json.dumps(POLICY) + "</acceptance_floor>"


def fixture(price=5.60, status="final_offer", sends=2):
    case = json.loads((ROOT / "data/package/cases/practice/P18.json").read_text())
    sid = "sup:70740"
    opening = {"from": "sales@70740", "status": "open", "message_id": "opening",
               "term_sheets": [{"term_sheet_id": "offer:opening", "terms": {
                   "unit_price": 6.10, "payment_days": 30, "contract_months": 12, "index_clause": None}}]}
    quote = {"from": "sales@70740", "status": status, "message_id": "latest",
             "term_sheets": [{"term_sheet_id": "offer:latest", "terms": {
                 "unit_price": price, "payment_days": 14, "contract_months": 36,
                 "index_clause": {"series": "AS-14404", "material_share": .45,
                                  "deadband_pct": 0, "base_month": "2026-08"}}}]}
    threads = {sid: [opening, *[{"from": "buyer", "body": "Conditional counter"} for _ in range(sends)], quote]}
    return case, sid, threads


def agent(backend=None):
    return PromptLabAgent(package(), backend, PromptSpec("floor-test", STRATEGY))


def test_floor_does_not_round_a_sub_80_score_up():
    floor = AcceptanceFloor.from_dict(POLICY)
    assert floor.max_cost == Decimal("237155.66")
    assert floor.allows(237155.66)
    assert not floor.allows(237155.67)
    assert not floor.allows(float("nan"))
    assert not floor.allows(float("inf"))
    assert not floor.allows(None)


def test_explicit_zero_score_minimum_keeps_the_price_and_opening_cost_caps():
    floor = AcceptanceFloor.from_dict({**POLICY, "minimum_score": 0, "maximum_unit_price": 5.10})
    assert floor.max_cost == Decimal("295173.23")
    assert floor.allows(251825.93, {"unit_price": 5.10})
    assert not floor.allows(200000, {"unit_price": 5.11})
    assert not floor.allows(295173.24, {"unit_price": 5.10})
    with pytest.raises(ValueError, match="explicit maximum_unit_price"):
        AcceptanceFloor.from_dict({**POLICY, "minimum_score": 0})
    with pytest.raises(ValueError, match="score in"):
        AcceptanceFloor.from_dict({**POLICY, "minimum_score": -1, "maximum_unit_price": 5.10})


@pytest.mark.parametrize("sends", [2, 10])
def test_actual_bad_final_never_becomes_an_acceptance(sends):
    case, sid, threads = fixture(sends=sends)
    a = agent()
    with pytest.raises(ContractError, match="ACCEPTANCE_SCORE_FLOOR_NOT_MET"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})
    state = a.decision_protocol_state(case, threads)["suppliers"][sid]
    assert "accept" not in state["legal_actions"]
    assert "stop" in state["legal_actions"]
    assert state["eligible_offer_ids"] == []
    with pytest.raises(NegotiationBlocked, match="ACCEPTANCE_SCORE_FLOOR_NOT_MET"):
        a.contract(case, threads, {"supplier_id": sid, "action": "stop"})


def test_term_costs_decide_acceptance_not_just_unit_price():
    case, sid, threads = fixture(price=4.80)
    move = agent().contract(case, threads, {"supplier_id": sid, "action": "accept", "offer_position": 1})
    assert move["message"]["action"]["term_sheet_id"] == "offer:latest"
    threads[sid][-1]["term_sheets"][0]["terms"]["unit_price"] = 4.81
    with pytest.raises(ContractError, match="ACCEPTANCE_SCORE_FLOOR_NOT_MET"):
        agent().contract(case, threads, {"supplier_id": sid, "action": "accept", "offer_position": 1})
    # The same 4.81 unit price qualifies with less costly payment and index terms.
    threads[sid][-1]["term_sheets"][0]["terms"]["payment_days"] = 90
    threads[sid][-1]["term_sheets"][0]["terms"]["index_clause"]["deadband_pct"] = 5
    assert agent().contract(case, threads, {"supplier_id": sid, "action": "accept", "offer_position": 1})


def test_unavailable_cheap_alternative_cannot_bypass_gate():
    case, sid, threads = fixture()
    alternative = copy.deepcopy(threads[sid][-1]["term_sheets"][0])
    alternative["term_sheet_id"] = "offer:alternative"
    alternative["terms"]["unit_price"] = 4.51
    threads[sid][-1]["term_sheets"].append(alternative)
    threads[sid][-1]["standing_offer_id"] = "offer:latest"
    assert agent().decision_protocol_state(case, threads)["suppliers"][sid]["eligible_offer_ids"] == []
    with pytest.raises(ContractError, match="standing offer"):
        agent().contract(case, threads, {"supplier_id": sid, "action": "accept", "offer_position": 2})


def test_transport_checks_again_before_any_network_call_or_buyer_message():
    class NeverSend:
        def send_message(self, *args):
            raise AssertionError("Disallowed acceptance reached the client")

    case, sid, threads = fixture()
    run = {"case": case, "threads": copy.deepcopy(threads), "prompt": {"acceptance_floor": POLICY}}
    table = RecordingTable(NeverSend(), run, case)
    with pytest.raises(NegotiationBlocked, match="ACCEPTANCE_SCORE_FLOOR_NOT_MET"):
        table.send(sid, {"action": {"type": "accept", "term_sheet_id": "offer:latest"}})
    assert run["threads"] == threads


def test_model_cannot_force_acceptance_after_validation_retry():
    class InsistentBackend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            assert payload["acceptance_policy"]["maximum_total_cost"] == 237155.66
            assert "accept" not in payload["protocol_state"]["suppliers"]["sup:70740"]["legal_actions"]
            return json.dumps({"supplier_id": "sup:70740", "action": "accept", "term_sheet_id": "offer:latest"})

    case, _, threads = fixture()
    backend = InsistentBackend()
    a = agent(backend)
    with pytest.raises(ContractError, match="no substitute strategy"):
        a.decide(case, threads)
    assert backend.calls == 2
    assert a.decisions == []


def test_benchmark_mismatch_and_bad_configuration_fail_closed():
    case, _, threads = fixture()
    floor = AcceptanceFloor.from_dict(POLICY)
    with pytest.raises(ValueError, match="CASE_MISMATCH"):
        floor.check_case({**case, "case_id": "case:P08"})
    threads["sup:70740"][0]["term_sheets"][0]["terms"]["unit_price"] = 6.11
    with pytest.raises(ValueError, match="OPENING_MISMATCH"):
        floor.check_case(case, threads)
    for bad in [float("nan"), True, 101]:
        with pytest.raises(ValueError):
            AcceptanceFloor.from_dict({**POLICY, "minimum_score": bad})
    with pytest.raises(ValueError):
        AcceptanceFloor.from_prompt("<acceptance_floor>{}")


def test_policy_survives_metadata_and_unconfigured_prompts_stay_unchanged():
    spec = PromptSpec("floor-test", STRATEGY)
    assert spec.metadata()["acceptance_floor"] == POLICY
    assert "<acceptance_floor_contract>" in spec.system
    assert "acceptance_floor" not in PromptSpec("plain", "Negotiate").metadata()
    case, sid, threads = fixture()
    plain = PromptLabAgent(package(), None, PromptSpec("plain", "Negotiate"))
    assert plain.contract(case, threads, {"supplier_id": sid, "action": "accept", "offer_position": 1})


def test_released_v5_carries_the_actual_floor():
    strategy = (ROOT / "prompts/p18-diagnostic-v5.txt").read_text()
    assert AcceptanceFloor.from_prompt(strategy).metadata() == POLICY


def v8_spec():
    return PromptSpec("v8-user-price-limit", (ROOT / "prompts/p18-diagnostic-v8.txt").read_text(),
                      assessment_enabled=True)


def v9_spec():
    return PromptSpec("v9-user-price-limit", (ROOT / "prompts/p18-diagnostic-v9.txt").read_text(),
                      assessment_enabled=True)


@pytest.mark.parametrize("status", ["open", "final_offer", "agreed"])
def test_latest_user_limit_permits_exactly_500_on_current_terms(status):
    spec = v9_spec()
    floor = spec.acceptance_floor
    assert floor.maximum_unit_price == Decimal("5.00")
    assert floor.minimum_score == Decimal("66.57991")
    assert floor.max_cost == Decimal("246888.17")
    assert not floor.allows(246888.18, {"unit_price": 5.00})
    case, sid, threads = fixture(price=5.00, status=status)
    a = PromptLabAgent(package(), None, spec)
    state = a.decision_protocol_state(case, threads)["suppliers"][sid]
    assert state["offers"][0]["cost"] == 246888.17
    assert state["eligible_offer_ids"] == ["offer:latest"]
    move = a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})
    assert move["message"]["action"]["type"] == "accept"


@pytest.mark.parametrize("price,cheaper_terms", [(5.01, False), (5.01, True), (5.20, False)])
def test_latest_user_limit_still_rejects_prices_above_500(price, cheaper_terms):
    case, sid, threads = fixture(price=price)
    if cheaper_terms:
        threads[sid][-1]["term_sheets"][0]["terms"].update(payment_days=90, index_clause=None)
    a = PromptLabAgent(package(), None, v9_spec())
    state = a.decision_protocol_state(case, threads)["suppliers"][sid]
    assert state["eligible_offer_ids"] == []
    if cheaper_terms:
        assert state["offers"][0]["meets_score_floor"]
    with pytest.raises(ContractError, match="ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})


def test_new_user_limit_permits_490_without_retaining_the_old_80_floor():
    floor = v8_spec().acceptance_floor
    assert floor.maximum_unit_price == Decimal("4.90")
    assert floor.max_cost == Decimal("241950.40")
    assert floor.minimum_score == Decimal("73.38857")
    assert floor.allows(241950.40, {"unit_price": 4.90})
    assert not floor.allows(241950.41, {"unit_price": 4.90})
    assert not floor.allows(230000, {"unit_price": 4.91})
    assert not floor.allows(230000)  # Missing terms cannot bypass an enabled unit-price cap.
    assert v8_spec().metadata()["acceptance_floor"]["maximum_unit_price"] == 4.9


@pytest.mark.parametrize("status", ["open", "final_offer", "agreed"])
def test_live_490_offer_is_eligible_even_below_80(status):
    case, sid, threads = fixture(price=4.90, status=status)
    a = PromptLabAgent(package(), None, v8_spec())
    state = a.decision_protocol_state(case, threads)["suppliers"][sid]
    assert state["offers"][0]["cost"] == 241950.40
    assert state["offers"][0]["meets_unit_price_limit"]
    assert state["eligible_offer_ids"] == ["offer:latest"]
    move = a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})
    assert move["message"]["action"]["type"] == "accept"


def test_cheaper_total_cost_does_not_authorize_a_higher_unit_price():
    case, sid, threads = fixture(price=4.91)
    terms = threads[sid][-1]["term_sheets"][0]["terms"]
    terms.update(payment_days=90, index_clause=None)
    a = PromptLabAgent(package(), None, v8_spec())
    state = a.decision_protocol_state(case, threads)["suppliers"][sid]
    assert state["offers"][0]["meets_score_floor"]
    assert not state["offers"][0]["meets_unit_price_limit"]
    assert "accept" not in state["legal_actions"]
    with pytest.raises(ContractError, match="ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})


def test_low_unit_price_does_not_authorize_excessive_uncovered_need_cost():
    case, sid, threads = fixture(price=4.90)
    threads[sid][-1]["term_sheets"][0]["terms"]["contract_months"] = 12
    a = PromptLabAgent(package(), None, v8_spec())
    with pytest.raises(ContractError, match="ACCEPTANCE_SCORE_FLOOR_NOT_MET"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})


def test_transport_enforces_new_unit_price_cap_before_network_or_recording():
    class NeverSend:
        def send_message(self, *args):
            raise AssertionError("Above-cap acceptance reached the client")

    case, sid, threads = fixture(price=4.91)
    threads[sid][-1]["term_sheets"][0]["terms"].update(payment_days=90, index_clause=None)
    run = {"case": case, "threads": copy.deepcopy(threads), "prompt": v8_spec().metadata()}
    table = RecordingTable(NeverSend(), run, case)
    with pytest.raises(NegotiationBlocked, match="ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED"):
        table.send(sid, {"action": {"type": "accept", "term_sheet_id": "offer:latest"}})
    assert run["threads"] == threads


def test_new_unit_cap_also_blocks_an_above_cap_counterproposal():
    class NeverSend:
        def send_message(self, *args):
            raise AssertionError("Above-cap proposal reached the client")

    case, sid, threads = fixture(price=5.20)
    terms = {**threads[sid][-1]["term_sheets"][0]["terms"], "unit_price": 4.91}
    a = PromptLabAgent(package(), None, v8_spec())
    with pytest.raises(ContractError, match="ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED"):
        a.contract(case, threads, {"supplier_id": sid, "action": "send", "body": "Counterproposal",
                                   "term_sheets": [terms]})
    run = {"case": case, "threads": copy.deepcopy(threads), "prompt": v8_spec().metadata()}
    with pytest.raises(NegotiationBlocked, match="ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED"):
        RecordingTable(NeverSend(), run, case).send(sid, {"body": "Counterproposal", "term_sheets": [{"terms": terms}]})
    assert run["threads"] == threads
    terms["unit_price"] = 4.90
    move = a.contract(case, threads, {"supplier_id": sid, "action": "send", "body": "Conditional close",
                                     "term_sheets": [terms]})
    assert move["message"]["term_sheets"][0]["terms"]["unit_price"] == 4.90


@pytest.mark.parametrize("status", ["walked_away", "no_bid"])
def test_lower_limit_does_not_reopen_a_terminal_thread(status):
    class NeverCalled:
        def complete(self, *args):
            raise AssertionError("Terminal thread triggered a model call")

    case, sid, threads = fixture(price=4.90)
    threads[sid].append({"from": "sales@70740", "status": status,
                         "body": "We withdraw. Our general offer remains valid for 14 days."})
    a = PromptLabAgent(package(), NeverCalled(), v8_spec())
    assert a.decision_protocol_state(case, threads)["suppliers"] == {}
    with pytest.raises(ContractError, match="live thread"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:latest"})
    with pytest.raises(ContractError, match="No live supplier"):
        a.decide(case, threads)


@pytest.mark.parametrize("cap", [True, False, None, 0, -1, float("nan"), float("inf"), "bad"])
def test_bad_unit_cap_configuration_fails_closed(cap):
    with pytest.raises(ValueError, match="maximum_unit_price"):
        AcceptanceFloor.from_dict({**POLICY, "maximum_unit_price": cap})
