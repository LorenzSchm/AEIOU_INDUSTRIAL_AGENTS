import copy
import json

import pytest

from aeiou.config import ROOT
from aeiou.decision_memory import build_context
from aeiou.price_planner import capacity_opening_config, capacity_opening_tradeoffs
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package, research


@pytest.fixture(scope="module")
def prepared():
    case = json.loads((ROOT / "data/package/cases/practice/P10.json").read_text())
    pkg = package()
    return case, pkg, research(case, pkg)


def test_complete_scoped_order_history_and_correct_volume(prepared):
    case, _, info = prepared
    assert len(info["p10_evidence"]["order_refs"]) == 72
    assert all(info["facts"][ref]["supplier"] == "sup:70410"
               and info["facts"][ref]["part"] == "part:340010" for ref in info["p10_evidence"]["order_refs"])
    context = build_context(case, {}, info, {})
    assert context["calculations"]["p10:ordered_last_12m_units"]["value"] == 19880
    assert context["calculations"]["p10:commitment_vs_orders_pct"]["value"] == pytest.approx(17.7062)
    assert info["facts"]["fc:340010@2026-09"]["volume_per_year"] == 21310
    assert info["collection"]["tables"]["po_lines"]["loaded_rows"] == 72


def test_source_groups_and_lower_anchor_reach_actual_model_input(prepared):
    case, pkg, _ = prepared
    threads = {"sup:70410": [{"from": "sales@70410", "status": "open", "body": "Our quotation.",
                             "term_sheets": [{"term_sheet_id": "offer:opening", "terms": {
                                 "unit_price": 32.2, "volume_per_year": 19890,
                                 "contract_months": 12, "lead_time_days": 54}}]}]}
    spec = PromptSpec("p10-v2-test", (ROOT / "prompts/p10-capacity-v2.txt").read_text(), assessment_enabled=True)
    agent = PromptLabAgent(pkg, None, spec)
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    for group in payload["research"]["p10_evidence"]["opening_arguments"]:
        for ref in group["source_refs"]:
            assert ref in payload["research"]["facts"]
            assert payload["decision_context"]["sources"][ref]["delivery"] == "full"
    rows = payload["price_plan"]["suppliers"]["sup:70410"]["capacity_opening_tradeoffs"]
    assert [r["terms"]["unit_price"] for r in rows] == [25.76, 25.14, 25.04, 25.76, 25.14, 25.04]
    assert spec.metadata()["capacity_opening"]["opening_fraction"] == .8
    assert "c_best" not in json.dumps(payload)
    assert agent.acceptance_floor is None


def test_lower_anchor_is_case_scoped(prepared):
    case, pkg, _ = prepared
    spec = PromptSpec("test", '<capacity_opening>{"case_id":"case:P10","opening_fraction":0.8}</capacity_opening>')
    agent = PromptLabAgent(pkg, None, spec)
    other = {**copy.deepcopy(case), "case_id": "case:P01"}
    with pytest.raises(ValueError, match="different case"):
        agent.prepare(other)
    assert capacity_opening_config("No anchor override") is None


@pytest.mark.parametrize("value", [True, 0, -1, 1.01, "0.8", None])
def test_invalid_anchor_rejected(value):
    value = json.dumps({"case_id": "case:P10", "opening_fraction": value})
    with pytest.raises(ValueError, match="opening_fraction"):
        capacity_opening_config(f"<capacity_opening>{value}</capacity_opening>")


def test_missing_po_data_is_not_invented(prepared):
    from aeiou.p10_evidence import enrich_p10

    case, pkg, _ = prepared
    info = {"facts": {}, "tables": {}}
    enrich_p10(case, pkg, info)
    assert not info["p10_evidence"]["opening_arguments"]
    assert not info["calculations"]


def test_lower_menu_still_respects_hard_delivery_constraint(prepared):
    case, _, _ = prepared
    case = copy.deepcopy(case)
    case["buyer"]["must_haves"].append({"field": "lead_time_days", "op": "<=", "value": 42})
    events = [{"from": "supplier", "term_sheets": [{"terms": {"unit_price": 32.2}}]}]
    rows = capacity_opening_tradeoffs(case, events, opening_fraction=.8)
    assert rows and all(r["terms"]["lead_time_days"] == 42 for r in rows)


def cited_offer_threads():
    return {"sup:70410": [
        {"from": "sales@70410", "status": "open", "body": "Our first quotation.", "term_sheets": [
            {"term_sheet_id": "offer:70410-0", "terms": {"unit_price": 32.2, "volume_per_year": 23400,
                                                         "contract_months": 24, "lead_time_days": 56}}]},
        {"from": "sales@70410", "status": "open", "body": "Our revised quotation.", "term_sheets": [
            {"term_sheet_id": "offer:70410-1", "terms": {"unit_price": 31.6, "volume_per_year": 23400,
                                                         "contract_months": 36, "lead_time_days": 56}}]},
    ]}


@pytest.mark.parametrize("field,ref", [("body", "offer:70410-0"), ("body", "offer:70410-1"),
                                       ("subject", "offer:70410-1")])
def test_own_quote_ids_rejected_without_rewriting_prose(prepared, field, ref):
    case, pkg, _ = prepared
    agent = PromptLabAgent(pkg, None, PromptSpec("test", "Negotiate P10."))
    action = {"supplier_id": "sup:70410", "action": "send", "subject": "New proposal",
              "body": "Thank you for your quotation.", "term_sheets": []}
    action[field] += f" ({ref})."
    original = copy.deepcopy(action)
    with pytest.raises(ContractError, match="not rival-quote evidence"):
        agent.contract(case, cited_offer_threads(), action)
    assert action == original


def test_structured_acceptance_and_rival_quote_citations_remain_available(prepared):
    from aeiou.evidence_transport import recipient_offer_citations

    case, pkg, _ = prepared
    agent = PromptLabAgent(pkg, None, PromptSpec("test", "Negotiate P10."))
    threads = cited_offer_threads()
    action = {"supplier_id": "sup:70410", "action": "accept", "term_sheet_id": "offer:70410-1"}
    assert agent.contract(case, threads, action)["message"]["action"]["term_sheet_id"] == "offer:70410-1"
    # A longer, distinct ID must not match the recipient ID merely as a substring.
    for body in ("Consider offer:70880-1.", "Consider offer:70410-10."):
        proposal = {"action": "send", "body": body}
        assert recipient_offer_citations(proposal, threads["sup:70410"]) == []


def test_own_quote_reference_uses_existing_model_repair_loop(prepared):
    case, pkg, _ = prepared

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 2:
                assert "not rival-quote evidence" in payload["action_repair"]["error"]
            return json.dumps({"supplier_id": "sup:70410", "action": "send", "term_sheets": [],
                               "body": "Thank you for " + ("offer:70410-1." if self.calls == 1
                                                             else "your quotation.")})

    backend = Backend()
    agent = PromptLabAgent(pkg, backend, PromptSpec("test", "Negotiate P10."))
    move = agent.decide(case, cited_offer_threads())
    assert backend.calls == 2
    assert move["message"]["body"] == "Thank you for your quotation."
    assert len(agent.audit["validation_failures"]) == 1
    assert len(agent.decisions) == 1


@pytest.mark.parametrize("ref", ["p10:ordered_last_12m_units", "table:po_lines",
                                 "buyer-record:p18:spend", "message:sup:70410:0",
                                 "attachment:sup:70410:0:0", "cost:sup:70410:0"])
def test_internal_citations_are_not_supplier_evidence(prepared, ref):
    case, pkg, _ = prepared
    agent = PromptLabAgent(pkg, None, PromptSpec("test", "Negotiate P10."))
    agent.prepare(case)
    action = {"supplier_id": "sup:70410", "action": "send", "subject": "Allocation",
              "body": f"Our evidence is ({ref}).", "term_sheets": []}
    original = copy.deepcopy(action)
    with pytest.raises(ContractError, match="private calculation/retrieval IDs"):
        agent.contract(case, cited_offer_threads(), action)
    assert action == original


def test_private_numeric_audit_and_canonical_public_citations_still_work(prepared):
    case, pkg, _ = prepared
    agent = PromptLabAgent(pkg, None, PromptSpec("test", "Negotiate P10."))
    agent.prepare(case)
    action = {"supplier_id": "sup:70410", "action": "send", "subject": "Allocation",
              "body": "Our estimate is EUR 24.31/unit (cost:340010).",
              "term_sheets": [], "numeric_claims": [{"calculation_id": "p10:ordered_last_12m_units"}],
              "assessment": {"evidence": [{"source_refs": ["table:po_lines"]}]}}
    sent = agent.contract(case, cited_offer_threads(), action)
    assert sent["message"]["body"] == action["body"]
    assert "numeric_claims" not in sent["message"]
    assert "assessment" not in sent["message"]


def test_internal_reference_is_repaired_before_sending(prepared):
    case, pkg, _ = prepared

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            if self.calls == 2:
                assert "private calculation/retrieval IDs" in json.loads(user)["action_repair"]["error"]
            return json.dumps({"supplier_id": "sup:70410", "action": "send", "term_sheets": [],
                               "body": "Our calculated annual order quantity is 19,880 units"
                                       + (" (p10:ordered_last_12m_units)." if self.calls == 1 else ".")})

    backend = Backend()
    agent = PromptLabAgent(pkg, backend, PromptSpec("test", "Negotiate P10."))
    sent = agent.decide(case, cited_offer_threads())
    assert backend.calls == 2
    assert sent["message"]["body"] == "Our calculated annual order quantity is 19,880 units."
    assert len(agent.audit["validation_failures"]) == 1
