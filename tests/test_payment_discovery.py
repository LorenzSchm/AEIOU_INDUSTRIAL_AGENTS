import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.payment_discovery import paired_payment
from aeiou.simulation import local_cases
from aeiou.strategic_planner import admit_proposals


def scenario():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNSEEN-PAYMENT", negotiable=["unit_price", "payment_days", "index_clause"])
    clause = {"series": "UNSEEN", "material_share": 0.3, "deadband_pct": 4, "base_month": "2026-01"}
    case["fixed_terms"].pop("index_clause", None)
    case["buyer"].update(category_goal="stabilise", index_risk_pct=3)
    case["term_options"].update(payment_days=[14, 30, 60], index_clause=[None, clause])
    terms = {"unit_price": 20, "payment_days": 14, "index_clause": clause}
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": {**terms, "unit_price": 30}}]}]

    def cost(t):
        return total_cost(case, t)

    packages = [
        {"id": "A", "terms": {**terms, "index_clause": None}, "cost": cost({**terms, "index_clause": None})},
        {"id": "B", "terms": terms, "cost": cost(terms)},
    ]
    return case, events, packages, cost


def test_pair_preserves_originals_and_changes_only_payment_and_price():
    case, events, packages, cost = scenario()
    original = copy.deepcopy((events, packages))
    result = paired_payment(case, events, packages, {}, cost, float("inf"))
    assert result[:2] == packages and len(result) == 3
    probe = result[2]
    assert probe["terms"]["payment_days"] == 30
    assert probe["terms"]["index_clause"] == packages[1]["terms"]["index_clause"]
    assert probe["cost"] <= packages[1]["cost"]
    assert (events, packages) == original


@pytest.mark.parametrize("mode", ["leverage", "no_slot", "already_sent", "terminal", "ceiling", "no_later_term"])
def test_discovery_does_not_change_other_categories_or_reopen_or_exceed_bounds(mode):
    case, events, packages, cost = scenario()
    ceiling = float("inf")
    if mode == "leverage":
        case["buyer"]["category_goal"] = "leverage"
    elif mode == "no_slot":
        packages.append({**packages[0], "id": "C"})
    elif mode == "already_sent":
        events.insert(0, {"from": "buyer", "term_sheets": [{"terms": packages[1]["terms"]}]})
    elif mode == "terminal":
        events[-1]["status"] = "agreed"
    elif mode == "ceiling":
        ceiling = 0
    else:
        case["term_options"]["payment_days"] = [14]
    assert paired_payment(case, events, packages, {}, cost, ceiling) == packages


def test_model_cannot_remove_or_reprice_the_controlled_comparison():
    case, events, packages, cost = scenario()
    candidates = paired_payment(case, events, packages, {}, cost, float("inf"))
    sid = case["suppliers"][0]
    data = {"supplier_id": sid, "action": "send", "proposals": [{"base_id": "B", "changes": {"unit_price": 25}}]}
    result, trace = admit_proposals(case, {sid: events}, sid, candidates, {}, data, {})
    assert result == candidates
    assert trace["submitted"] == data["proposals"]
    assert trace["rejected"] and trace["selected"] is None
