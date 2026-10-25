"""Capacity options preserve coverage, hard limits and exact public economics."""

import copy
import json

import pytest
from arena_kit.cost import total_cost

from aeiou.config import ROOT
from aeiou.optimizer import case_with_supplier_defaults, valid_terms
from aeiou.price_planner import capacity_opening_tradeoffs
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package

SID = "sup:70410"


def scenario():
    case = json.loads((ROOT / "data/package/cases/practice/P10.json").read_text())
    events = [{"from": "sales@70410", "status": "open", "body": "Our quotation.", "term_sheets": [
        {"term_sheet_id": "offer:opening", "terms": {
            "unit_price": 32.20, "volume_per_year": 19890, "contract_months": 12, "lead_time_days": 54}}]}]
    return case, events


def test_delay_is_offset_at_cent_precision_with_full_coverage():
    case, events = scenario()
    before = copy.deepcopy((case, events))
    std = {"ppm_commitment": 300}
    rows = capacity_opening_tradeoffs(case, events, std)
    assert [(r["terms"]["contract_months"], r["terms"]["lead_time_days"]) for r in rows] == [
        (24, 42), (24, 54), (24, 56), (36, 42), (36, 54), (36, 56)]
    assert [r["terms"]["unit_price"] for r in rows] == [27.37, 26.75, 26.65, 27.37, 26.75, 26.65]
    assert rows[0]["complete_cost"] == 1294790.64
    assert rows[1]["delay_cost_at_reference_price"] == 28800
    assert rows[2]["delay_cost_at_reference_price"] == 33600
    for row in rows:
        terms = row["terms"]
        assert valid_terms(case_with_supplier_defaults(case, events), terms)
        assert terms["volume_per_year"] == 23400
        assert row["complete_cost"] <= row["reference_cost"]
        assert total_cost(case, {**terms, "unit_price": terms["unit_price"] + .01}, std) > row["reference_cost"]
        assert "requires supplier approval" in row["status"]
    assert (case, events) == before


def test_real_lead_time_must_have_cannot_be_traded_away():
    case, events = scenario()
    case["buyer"]["must_haves"].append({"field": "lead_time_days", "op": "<=", "value": 42})
    rows = capacity_opening_tradeoffs(case, events)
    assert rows and {r["terms"]["lead_time_days"] for r in rows} == {42}


def test_fixed_terms_and_later_supplier_quotes_do_not_change_opening_anchor():
    case, events = scenario()
    expected = capacity_opening_tradeoffs(case, events)
    events.extend([{"from": "buyer", "term_sheets": [{"terms": {"unit_price": 1}}]},
                   {"from": "sales@70410", "term_sheets": [{"terms": {"unit_price": 34.10}}]}])
    assert capacity_opening_tradeoffs(case, events) == expected
    assert all(set(r["terms"]) == set(case["negotiable"]) for r in expected)


@pytest.mark.parametrize("opening", [None, True, -1, float("nan"), float("inf"), "32.20"])
def test_invalid_opening_is_not_a_price_anchor(opening):
    case, events = scenario()
    events[0]["term_sheets"][0]["terms"]["unit_price"] = opening
    assert capacity_opening_tradeoffs(case, events) == []


def test_non_capacity_cases_unchanged():
    case, events = scenario()
    case["type"] = "price_increase"
    assert capacity_opening_tradeoffs(case, events) == []


def test_tradeoffs_reach_buyer_without_post_run_results_or_live_offer_ids():
    case, events = scenario()
    spec = PromptSpec("p10-capacity-test", (ROOT / "prompts/p10-capacity-v1.txt").read_text(),
                      assessment_enabled=True)
    agent = PromptLabAgent(package(), None, spec)
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, {SID: events}))
    rows = payload["price_plan"]["suppliers"][SID]["capacity_opening_tradeoffs"]
    assert len(rows) == 6
    assert all("offer_id" not in row and "estimated_score" not in row for row in rows)
    assert "c_best" not in json.dumps(rows)
    assert not payload["protocol_state"]["suppliers"][SID]["eligible_offer_ids"]
    assert agent.acceptance_floor is None
    assert agent.execution_policy is None
