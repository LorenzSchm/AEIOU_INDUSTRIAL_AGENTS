import copy
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from arena_kit.cost import total_cost
from assessment_helpers import assessment_fields

from aeiou.acceptance_floor import AcceptanceFloor
from aeiou.agent import BuyerDecisionTimeout
from aeiou.config import ROOT
from aeiou.decision_memory import build_context, validate_assessment
from aeiou.evidence_transport import MAX_PAGE_CHARS, retrieve, source_records
from aeiou.execution_policy import load_execution_policies
from aeiou.price_planner import maximum_affordable_price
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package
from aeiou.supplier_signals import supplier_signals

CONTROLS = {"case_id": "case:P18", "accept_eligible_offers": True, "closing_price": 5.09,
            "maximum_unit_price": 5.10, "maximum_total_cost": 251825.93,
            "minimum_contract_months": 24, "one_final_counter": True}
CLAUSE = {"series": "AS-14404", "material_share": .45, "deadband_pct": 0, "base_month": "2026-08"}


def scenario(price=5.30, status="final_offer"):
    case = json.loads((ROOT / "data/package/cases/practice/P18.json").read_text())
    sid = "sup:70740"
    opening = {"from": "sales@70740", "status": "open", "body": "Opening quotation.",
               "term_sheets": [{"term_sheet_id": "offer:opening", "terms": {
                   "unit_price": 6.10, "payment_days": 30, "contract_months": 12, "index_clause": None}}]}
    terms = {"unit_price": price, "payment_days": 14, "contract_months": 24, "index_clause": CLAUSE}
    final = {"from": "sales@70740", "status": status,
             "body": "Thank you. It is a pleasure working with you. This is our final offer. Please decide. "
                     "The base price must cover our processing and operational costs.",
             "term_sheets": [{"term_sheet_id": "offer:current", "terms": terms}]}
    threads = {sid: [opening, {"from": "buyer", "body": "Our proposal.",
                               "term_sheets": [{"terms": {**terms, "unit_price": 4.72}}]}, final]}
    return case, sid, threads


def make_agent(backend=None, controls=True):
    strategy = (ROOT / "prompts/p18-diagnostic-v14.txt").read_text()
    return PromptLabAgent(package(), backend, PromptSpec(
        "pipeline-test", strategy, assessment_enabled=True, execution_policy=CONTROLS if controls else None))


class NeverCall:
    def complete(self, *args):
        raise AssertionError("Authorized closing should not need a model")


def test_polite_final_is_high_risk_and_uses_primary_quotes_not_menu_alternatives():
    case, sid, threads = scenario()
    threads[sid][0]["term_sheets"].append({"term_sheet_id": "alternative", "terms": {"unit_price": 9}})
    signal = supplier_signals(case, threads)[sid]
    assert signal["tone"] == "courteous"
    assert signal["stance"] == "final" and signal["withdrawal_risk_signal"] == "high"
    assert signal["latest_primary_price_movement"] == -.8
    assert "base_cost_objection" in signal["live_objections"]
    assert all(e["quote"] in threads[sid][-1]["body"] for e in signal["evidence"])
    threads[sid].append({"from": "sales@70740", "status": "walked_away",
                         "body": "Thank you. Our general offer stays valid for 14 days."})
    assert supplier_signals(case, threads)[sid]["stance"] == "terminal"


def test_non_final_language_does_not_invent_final_status():
    case, sid, threads = scenario(status="open")
    threads[sid][-1]["body"] = "This is not our final offer. We can discuss the price."
    assert supplier_signals(case, threads)[sid]["stance"] == "negotiating"


def test_supplier_weighted_math_is_conditional_and_not_a_verified_cost_floor():
    case, sid, threads = scenario()
    threads[sid][-1]["body"] = (
        "AS-14404 rose 5.9 % in twelve months and is 45 % of our cost. "
        "LCI-C rose 2.7 % in twelve months and is 5 % of our cost. "
        "PPI-C2431 rose 12.3 % in twelve months and is 38 % of our cost.")
    context = build_context(case, threads, {}, {})
    drivers = context["suppliers"][sid]["stated_annual_cost_drivers"]
    ref = drivers["conditional_calculation_id"]
    assert drivers["stated_share_sum_pct"] == 88
    assert context["calculations"][ref]["value"] == 7.464
    assert context["sources"][ref]["kind"] == "inference"
    assert "unknown" in context["calculations"][ref]["description"]
    assert context["calculations"][ref]["source_refs"] == ["message:sup:70740:2"]


@pytest.mark.parametrize("months", [12, 24, 36])
@pytest.mark.parametrize("days", [14, 90])
@pytest.mark.parametrize("deadband", [None, 0, 5])
def test_price_solver_uses_full_cost_and_returns_exact_cent_boundary(months, days, deadband):
    case, _, _ = scenario()
    terms = {"unit_price": 1, "payment_days": days, "contract_months": months,
             "index_clause": None if deadband is None else {**CLAUSE, "deadband_pct": deadband}}
    budget, cap = 251825.93, 5.10
    result = maximum_affordable_price(case, terms, budget, cap)
    assert result is not None
    assert total_cost(case, {**terms, "unit_price": result}) <= budget
    assert result <= cap
    if result < cap:
        assert total_cost(case, {**terms, "unit_price": round(result + .01, 2)}) > budget


def test_price_solver_handles_invalid_and_unaffordable_packages():
    case, _, threads = scenario()
    terms = threads["sup:70740"][-1]["term_sheets"][0]["terms"]
    assert maximum_affordable_price(case, {**terms, "contract_months": 12}, 1, 5.1) is None
    assert maximum_affordable_price(case, {**terms, "payment_days": 7}, 251825.93, 5.1) is None
    with pytest.raises(ValueError):
        maximum_affordable_price(case, terms, float("nan"), 5.1)


@pytest.mark.parametrize("price", [4.50, 5.09, 5.10])
def test_authorized_eligible_offer_is_accepted_before_any_model_or_counter(price):
    case, sid, threads = scenario(price)
    agent = make_agent(NeverCall())
    move = agent.decide(case, threads)
    assert move == {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": "offer:current"}}}
    assert agent.decisions[-1]["source"] == "policy:accept"
    assert agent.decisions[-1]["assessment"]["next_move"].startswith("Accept")


def test_final_above_cap_gets_one_direct_509_counter_and_keeps_terms():
    case, sid, threads = scenario()
    agent = make_agent(NeverCall())
    move = agent.decide(case, threads)
    terms = move["message"]["term_sheets"][0]["terms"]
    assert terms == {**threads[sid][-1]["term_sheets"][0]["terms"], "unit_price": 5.09}
    assert total_cost(case, terms) <= CONTROLS["maximum_total_cost"]
    assert agent.decisions[-1]["source"] == "policy:closing-counter"
    threads[sid].extend([{**move["message"], "from": "buyer"}, copy.deepcopy(threads[sid][-1])])

    class ContinueBackend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            assert "stop" not in payload["protocol_state"]["suppliers"][sid]["legal_actions"]
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Can your pricing colleague review it?",
                               "term_sheets": [], **assessment_fields(payload, sid)})

    backend = ContinueBackend()
    reconstructed = make_agent(backend)
    assert reconstructed.decide(case, threads)["message"]["term_sheets"] == []
    assert backend.calls == 1


def test_no_controls_for_unconfigured_strategies_and_no_terminal_reopen():
    case, sid, threads = scenario()
    a = make_agent(controls=False)
    assert a.spec.execution_policy is None
    assert "stop" in a.decision_protocol_state(case, threads)["suppliers"][sid]["legal_actions"]
    threads[sid].append({"from": sid, "status": "walked_away", "body": "Offer valid for 14 days."})
    with pytest.raises(ContractError, match="No live supplier"):
        make_agent(NeverCall()).decide(case, threads)


def test_better_cost_above_nominal_cap_is_never_accepted_and_scope_is_checked():
    case, sid, threads = scenario(5.20)
    threads[sid][-1]["term_sheets"][0]["terms"].update(index_clause=None, payment_days=90)
    agent = make_agent()
    with pytest.raises(ContractError, match="execution controls"):
        agent.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:current"})
    with pytest.raises(ContractError, match="do not stop early"):
        agent.contract(case, threads, {"supplier_id": sid, "action": "stop"})
    other = {**case, "case_id": "case:other"}
    with pytest.raises(ValueError, match="CASE_MISMATCH"):
        agent.prepare(other)


def test_execution_settings_do_not_silently_override_different_acceptance_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    assert load_execution_policies() == {}
    (tmp_path / "negotiation-policies.json").write_text(json.dumps({"case:P18": CONTROLS}))
    assert load_execution_policies()["case:P18"]["closing_price"] == 5.09
    spec = PromptSpec("bad", (ROOT / "prompts/p18-diagnostic-v13.txt").read_text(), execution_policy=CONTROLS)
    with pytest.raises(ValueError, match="limits differ"):
        PromptLabAgent(package(), None, spec)


def test_compact_context_keeps_calculations_and_raw_evidence_access_without_duplicate_ledgers():
    case, sid, threads = scenario(status="open")
    agent = make_agent(controls=False)
    agent.prepare(case)
    original = copy.deepcopy(agent.intelligence)
    payload = json.loads(agent.render_lab(case, threads))
    transport = payload["evidence_transport"]
    assert transport["delivered_payload_chars"] < transport["raw_payload_chars"] * .8
    assert transport["delivered_payload_chars"] == len(json.dumps(payload, ensure_ascii=False))
    assert agent.intelligence == original
    ledger = "buyer-record:p18:fy2026-account-invoices"
    assert ledger not in payload["research"]["facts"]
    assert payload["decision_context"]["sources"][ledger]["available"] is False
    assert payload["decision_context"]["calculations"]["p18:account_invoiced_fy2026_eur"]["value"] == 428119.02
    action = {"supplier_id": sid, "action": "send", "body": "Clarification.", **assessment_fields(payload, sid)}
    action["assessment"]["evidence"].append({"claim": "I read the invoices.", "kind": "public_record",
                                            "source_refs": [ledger], "confidence": "high"})
    with pytest.raises(ValueError, match="deferred"):
        validate_assessment(action, payload["decision_context"])


def test_local_retrieval_is_bounded_and_cannot_read_paths_or_unknown_ids():
    records = {"record": {"text": "x" * (MAX_PAGE_CHARS * 2)}}
    first = retrieve(records, {"source_ref": "record"})
    second = retrieve(records, {"source_ref": "record", "offset": first["next_offset"]})
    assert len(first["text"]) == MAX_PAGE_CHARS and not first["complete_record"]
    assert first["content_sha256"] == second["content_sha256"]
    assert first["text"] + second["text"] == json.dumps(records["record"], sort_keys=True)[:MAX_PAGE_CHARS * 2]
    for action in ({"source_ref": "/etc/passwd"}, {"source_ref": "record", "offset": -1},
                   {"source_ref": "record", "offset": True}):
        with pytest.raises(ValueError):
            retrieve(records, action)


def test_model_can_retrieve_deferred_record_without_sending_supplier_message():
    case, sid, threads = scenario(status="open")
    # Small history tables are now delivered up front; the full PO ledger remains deferred.
    ref = "table:po_lines"

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 1:
                assert not payload["decision_context"]["sources"][ref]["available"]
                return json.dumps({"action": "retrieve_evidence", "source_ref": ref})
            assert payload["retrieved_evidence"][0]["source_ref"] == ref
            assert payload["decision_context"]["sources"][ref]["available"]
            fields = assessment_fields(payload, sid)
            fields["assessment"]["evidence"].append({"claim": "The history record is available for review.",
                "kind": "public_record", "source_refs": [ref], "confidence": "high"})
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Thank you. Please clarify the price.",
                               "term_sheets": [], **fields})

    backend = Backend()
    agent = make_agent(backend, controls=False)
    original = copy.deepcopy(threads)
    move = agent.decide(case, threads)
    assert move["message"]["body"].startswith("Thank you")
    assert threads == original and backend.calls == 2 and len(agent.decisions) == 1
    assert agent.decisions[0]["evidence_reads"][0]["source_ref"] == ref
    assert ref in source_records(agent.intelligence, threads)
    assert AcceptanceFloor.from_prompt(agent.spec.strategy).max_cost == Decimal("251825.93")


def test_retrieval_cannot_extend_the_shared_decision_deadline(monkeypatch):
    case, sid, threads = scenario(status="open")
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("aeiou.prompt_protocol.time", SimpleNamespace(monotonic=lambda: clock.now))

    class Backend:
        deadlines = []

        def complete_before(self, system, user, deadline):
            self.deadlines.append(deadline)
            clock.now += 26
            if len(self.deadlines) == 1:
                return json.dumps({"action": "retrieve_evidence", "source_ref": "inv:5105005073"})
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Please clarify.",
                               **assessment_fields(json.loads(user), sid)})

    backend = Backend()
    agent = make_agent(backend, controls=False)
    with pytest.raises(BuyerDecisionTimeout, match="no action was sent"):
        agent.decide(case, threads)
    assert backend.deadlines == [150, 150]
    assert agent.decisions == []


def test_retrieval_loop_stops_at_two_reads_and_two_invalid_outputs():
    case, _, threads = scenario(status="open")

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            assert len(json.loads(user).get("retrieved_evidence", [])) <= 2
            return json.dumps({"action": "retrieve_evidence", "source_ref": "inv:5105005073"})

    backend = Backend()
    agent = make_agent(backend, controls=False)
    with pytest.raises(ContractError, match="Two invalid outputs"):
        agent.decide(case, threads)
    assert backend.calls == 4 and agent.decisions == []
    assert all("read budget exhausted" in e["error"] for e in agent.last_rejections)


def test_evaluation_snapshots_controls_only_for_selected_case(monkeypatch, tmp_path):
    from aeiou import eval_suite

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    monkeypatch.setattr(eval_suite, "ACTIVE_EVALUATIONS", {})
    policy_file = tmp_path / "negotiation-policies.json"
    policy_file.write_text(json.dumps({"case:P18": CONTROLS}))
    report = eval_suite.prepare_evaluation(
        (ROOT / "prompts/p18-diagnostic-v14.txt").read_text(), variant_id="practice",
        case_ids=["case:P18"], repeats=1,
        catalog={"cases": [{"case_id": "case:P18", "title": "P18"}], "quota": {"left": 1}})
    policy_file.write_text("{}")
    saved = json.loads((tmp_path / "eval-suite" / f"{report['id']}.json").read_text())
    assert saved["execution_policies"] == {"case:P18": CONTROLS}
    assert report["arena_sessions_used"] == 0
    policy_file.write_text(json.dumps({"case:P18": CONTROLS}))
    sandbox = eval_suite.prepare_evaluation("General sandbox strategy", repeats=1)
    assert sandbox["execution_policies"] == {}
