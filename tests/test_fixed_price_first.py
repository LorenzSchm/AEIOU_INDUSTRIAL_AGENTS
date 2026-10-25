import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.opponent_model import learn_beliefs
from aeiou.portfolio_value import fixed_price_first
from aeiou.simulation import local_cases
from aeiou.strategic_planner import admit_proposals, proposal_limits


def scenario():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNSEEN-FIXED", negotiable=["unit_price", "payment_days", "index_clause"])
    clause = {"series": "NEW-INDEX", "material_share": 0.3, "deadband_pct": 4, "base_month": "2026-01"}
    case["fixed_terms"].pop("index_clause", None)
    case["buyer"].update(category_goal="stabilise", index_risk_pct=3)
    case["term_options"]["index_clause"] = [None, clause]
    terms = {"unit_price": 23, "payment_days": 30, "index_clause": None}
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": terms}]}]
    return case, events, clause


def test_discovery_is_a_single_fixed_price_opening_not_a_permanent_index_ban():
    case, events, clause = scenario()
    assert fixed_price_first(case, events)
    response = copy.deepcopy(events[0])
    events += [{"from": "buyer", "term_sheets": [{"terms": {"unit_price": 21, "index_clause": None}}]}, response]
    assert not fixed_price_first(case, events)
    sid = case["suppliers"][0]
    terms = {"unit_price": 21, "payment_days": 30, "index_clause": None}
    candidates = [{"id": "A", "terms": terms, "cost": total_cost(case, terms)}]
    assert proposal_limits(case, {sid: events}, sid, candidates, {})["index_exploration_allowed"]


@pytest.mark.parametrize(
    "mode",
    [
        "leverage",
        "routine",
        "zero_risk",
        "fixed_index",
        "mandatory_index",
        "indexed_quote",
        "missing_clause",
        "agreed",
        "final_offer",
        "empty",
    ],
)
def test_no_change_outside_qualified_fixed_price_opening(mode):
    case, events, clause = scenario()
    if mode in {"leverage", "routine"}:
        case["buyer"]["category_goal"] = mode
    elif mode == "zero_risk":
        case["buyer"]["index_risk_pct"] = 0
    elif mode == "fixed_index":
        case["negotiable"].remove("index_clause")
    elif mode == "mandatory_index":
        case["term_options"]["index_clause"] = [clause]
    elif mode == "indexed_quote":
        events[0]["term_sheets"][0]["terms"]["index_clause"] = clause
    elif mode == "missing_clause":
        del events[0]["term_sheets"][0]["terms"]["index_clause"]
    elif mode == "empty":
        events = []
    else:
        events[0]["status"] = mode
    assert not fixed_price_first(case, events)


def test_model_cannot_reintroduce_index_option_in_fixed_discovery_message():
    case, events, clause = scenario()
    sid = case["suppliers"][0]
    terms = {"unit_price": 21, "payment_days": 30, "index_clause": None}
    candidates = [{"id": "A", "terms": terms, "cost": total_cost(case, terms)}]
    data = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [{"base_id": "A", "changes": {"unit_price": 19, "index_clause": clause}}],
    }
    before = copy.deepcopy((events, candidates, data))
    result, trace = admit_proposals(case, {sid: events}, sid, candidates, {}, data, learn_beliefs(case, events))
    assert result == candidates
    assert any("First test the fixed-price" in r for r in trace["rejected"])
    assert before == (events, candidates, data)
