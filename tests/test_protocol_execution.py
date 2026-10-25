import copy

import pytest

from aeiou.agent import Negotiator
from aeiou.negotiation_state import offer_state, package_repeats, protocol_state, walk_away_diagnostic
from aeiou.prompt_protocol import ContractError, PromptLabAgent, prompt_specs
from aeiou.research import package
from aeiou.simulation import local_cases


def fixture(count=1, sends=0, case_id="case:S01"):
    case = copy.deepcopy(local_cases()[case_id])
    sid = case["suppliers"][0]
    terms = {"unit_price": 16, "payment_days": 60}
    if case_id == "case:T02":
        terms = {"unit_price": 215, "payment_days": 30, "volume_per_year": 8490, "contract_months": 24}
    events = []
    for i in range(count):
        events.append(
            {
                "from": "supplier",
                "message_id": f"msg:{i}",
                "status": "open",
                "term_sheets": [{"term_sheet_id": f"offer:{i}", "terms": copy.deepcopy(terms)}],
            }
        )
    events.extend({"from": "buyer", "body": "Earlier request."} for _ in range(sends))
    return case, sid, {sid: events}


def agent(backend=None):
    return PromptLabAgent(package(), backend, prompt_specs()["original"])


def test_package_counter_survives_changed_other_offers_and_order():
    case, sid, threads = fixture(count=5)
    for i, msg in enumerate(threads[sid]):
        msg["term_sheets"].append(
            {"term_sheet_id": f"offer:other-{i}", "terms": {"unit_price": 18 - i / 10, "payment_days": 14}}
        )
        if i % 2:
            msg["term_sheets"].reverse()
    rows = offer_state(case, threads[sid], 5)
    target = next(o for o in rows if o["terms"]["payment_days"] == 60)
    assert target["repeat_count"] == 5
    assert target["acceptance_eligible"] is True


def test_package_counter_resets_changed_term_not_number_representation():
    case, sid, threads = fixture(count=5)
    threads[sid][1]["term_sheets"][0]["terms"]["unit_price"] = 16.0
    assert package_repeats(case, threads[sid], {"unit_price": 16, "payment_days": 60}) == 5
    threads[sid][-2]["term_sheets"][0]["terms"]["payment_days"] = 30
    assert package_repeats(case, threads[sid], {"unit_price": 16, "payment_days": 60}) == 1


def test_duplicate_message_id_and_duplicate_sheet_are_not_extra_confirmations():
    case, sid, threads = fixture(count=1)
    threads[sid][0]["term_sheets"].append(copy.deepcopy(threads[sid][0]["term_sheets"][0]))
    threads[sid] *= 5
    assert offer_state(case, threads[sid], 5)[0]["repeat_count"] == 1


def test_early_acceptance_is_rejected_even_when_other_package_repeated_five_times():
    case, sid, threads = fixture(count=5)
    threads[sid][-1]["term_sheets"].append(
        {"term_sheet_id": "offer:new", "terms": {"unit_price": 15.5, "payment_days": 60}}
    )
    with pytest.raises(ContractError, match="five required"):
        agent().contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:new"})


def test_verified_offer_position_and_conflicting_selector():
    case, sid, threads = fixture(count=5)
    a = agent()
    move = a.contract(case, threads, {"supplier_id": sid, "action": "accept", "offer_position": 1})
    assert move["message"]["action"]["term_sheet_id"] == "offer:4"
    with pytest.raises(ContractError, match="live term_sheet_id"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:stale"})
    with pytest.raises(ContractError, match="live term_sheet_id"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept"})


def test_controller_never_inserts_or_rewrites_negotiation_text():
    case, sid, threads = fixture()
    a = agent()
    action = {"supplier_id": sid, "action": "send", "body": "Please quote all negotiable terms.", "term_sheets": []}
    move = a.contract(case, threads, action)
    assert move["message"]["body"] == action["body"]
    assert "how am i supposed to do that?" not in move["message"]["body"].casefold()
    threads[sid].append({"from": "buyer", **move["message"]})
    assert a.exact_request not in a.contract(case, threads, action)["message"]["body"]
    action["body"] = a.exact_request.replace("not\naccepted)", "not accepted)")
    preserved = a.contract(case, threads, action)
    assert preserved["message"]["body"] == action["body"]
    assert a.audit["verbatim_insertions"] == a.audit["verbatim_format_restorations"] == 0


def test_reserved_phase_and_exact_confirmation_keeps_all_terms():
    case, sid, threads = fixture(sends=6)
    a = agent()
    state = protocol_state(case, threads, 5)["suppliers"][sid]
    assert state["phase"] == "confirm" and state["rounds_left"] == 4
    assert "send" not in state["legal_actions"]
    with pytest.raises(ContractError, match="reserved"):
        a.contract(case, threads, {"supplier_id": sid, "action": "send", "body": "New price", "term_sheets": []})
    move = a.contract(case, threads, {"supplier_id": sid, "action": "confirm", "term_sheet_id": "offer:0"})
    assert move["message"]["term_sheets"] == []
    assert "unit_price: 16" in move["message"]["body"]
    assert "payment_days: 60" in move["message"]["body"]
    assert "does not accept" in move["message"]["body"]
    assert "how am i supposed to do that?" not in move["message"]["body"].casefold()


def test_confirmation_controller_reuses_current_id_without_model_call():
    class MustNotCall:
        def complete(self, *args):
            raise AssertionError("A repeated confirmation should not call the model")

    case, sid, threads = fixture(sends=6)
    a = agent(MustNotCall())
    a.contract(case, threads, {"supplier_id": sid, "action": "confirm", "term_sheet_id": "offer:0"})
    threads[sid].append(
        {
            "from": "supplier",
            "message_id": "msg:next",
            "status": "open",
            "term_sheets": [{"term_sheet_id": "offer:fresh", "terms": {"unit_price": 16, "payment_days": 60}}],
        }
    )
    move = a.decide(case, threads)
    assert move["message"]["term_sheets"] == []
    assert a.decisions[-1]["source"] == "protocol:confirmation"
    assert a.decisions[-1]["confirmation"]["term_sheet_id"] == "offer:fresh"


def test_round_cap_records_limit_without_sending_or_calling_model():
    from aeiou.action_policy import NegotiationBlocked

    class NeverCall:
        def complete(self, *args):
            raise AssertionError("No legal actions: do not call the model")

    case, sid, threads = fixture(sends=10, case_id="case:T02")
    a = agent(NeverCall())
    with pytest.raises(NegotiationBlocked, match="NO_LEGAL_ACTION"):
        a.decide(case, threads)
    assert protocol_state(case, threads, 5)["suppliers"][sid]["legal_actions"] == []
    assert a.audit["walk_aways"] == []
    assert a.audit["terminal_limits"][-1]["reason_code"] == "NO_LEGAL_ACTION"


def test_walk_away_disabled_and_deadline_never_sends_one():
    from aeiou.action_policy import NegotiationBlocked

    case, sid, threads = fixture(count=5)
    a = agent()
    with pytest.raises(ContractError, match="disabled"):
        a.contract(case, threads, {"supplier_id": sid, "action": "walk_away", "reason": "Still forbidden"})
    with pytest.raises(NegotiationBlocked, match="WAVE_DEADLINE"):
        a.close(case, threads)
    assert a.audit["walk_aways"] == []
    assert a.audit["terminal_limits"][-1]["reason_code"] == "WAVE_DEADLINE"


def test_round_cap_walk_away_has_objective_rule_diagnosis():
    case, sid, threads = fixture(sends=10, case_id="case:T02")
    diagnosis = walk_away_diagnostic(case, threads, sid, required_repeats=5)
    assert diagnosis["reason_code"] == "REPEAT_RULE_UNMET_AT_ROUND_LIMIT"
    assert diagnosis["acceptance_eligible_offer_available"] is False


def test_legacy_economic_walk_away_is_logged():
    case, sid, threads = fixture()
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 90}]
    a = Negotiator(package())
    from aeiou.action_policy import NegotiationBlocked

    with pytest.raises(NegotiationBlocked, match="NO_ELIGIBLE_OFFER"):
        a.close(case, threads)


def test_provider_refusal_is_distinct_from_a_json_error(monkeypatch):
    from types import SimpleNamespace

    from aeiou.agent import ClaudeBackend, ProviderRefusalError

    class Messages:
        def create(self, **kwargs):
            return SimpleNamespace(
                content=[], stop_reason="refusal", usage=SimpleNamespace(input_tokens=10, output_tokens=0)
            )

    monkeypatch.setattr("anthropic.Anthropic", lambda **kwargs: SimpleNamespace(messages=Messages()))
    backend = ClaudeBackend(model="test-model")
    with pytest.raises(ProviderRefusalError, match="stop_reason=refusal"):
        backend.complete("system", "user")
    assert backend.calls == backend.errors == backend.refusals == 1
    assert backend.empty_responses == 0
    assert backend.stop_reasons == {"refusal": 1}


def test_original_factory_uses_reference_protocol_and_preserves_strategy(monkeypatch):
    from aeiou.runner import make_agent

    monkeypatch.setattr("aeiou.runner.make_backend", lambda **kwargs: object())
    a = make_agent("original", package())
    assert a.required_repeats == 5
    assert a.spec.strategy == prompt_specs()["original"].strategy


def test_structured_seller_output_does_not_change_default_buyer_request(monkeypatch):
    from types import SimpleNamespace

    from aeiou.agent import ClaudeBackend
    from aeiou.language_sim import SUPPLIER_SCHEMA

    requests = []

    class Messages:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="{}")],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    monkeypatch.setattr("anthropic.Anthropic", lambda **kwargs: SimpleNamespace(messages=Messages()))
    backend = ClaudeBackend(model="test-model")
    backend.complete("unchanged buyer system", "buyer input")
    backend.complete_json("seller system", "seller input", SUPPLIER_SCHEMA)
    assert requests[0]["output_config"] == {"effort": "low"}
    assert requests[0]["system"][0]["text"] == "unchanged buyer system"
    assert requests[1]["output_config"]["format"]["schema"] == SUPPLIER_SCHEMA
