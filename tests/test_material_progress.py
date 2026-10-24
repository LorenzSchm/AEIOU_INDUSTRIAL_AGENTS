import copy

import pytest
from arena_kit.cost import complete_offer, total_cost

from aeiou.concessions import match_material_progress
from aeiou.simulation import local_cases


def setup():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"]["category_goal"] = "routine"
    offer = complete_offer(case, {"unit_price": 10, "payment_days": 30})
    bid = {**offer, "unit_price": 8}
    events = [
        {"from": "supplier", "status": "open", "term_sheets": [{"terms": offer}]},
        {"from": "buyer", "term_sheets": [{"terms": bid}]},
        {"from": "supplier", "status": "open", "term_sheets": [{"terms": {**offer, "unit_price": 9.7}}]},
    ]
    package = {"id": "A", "terms": {**bid, "unit_price": 8.02}, "cost": total_cost(case, {**bid, "unit_price": 8.02})}
    return case, events, [package]


def test_reciprocates_material_supplier_step_within_buyer_cost_limit():
    case, events, packages = setup()
    original = copy.deepcopy((case, events, packages))
    result = match_material_progress(case, events, packages, lambda t: total_cost(case, t))
    assert result[0]["terms"]["unit_price"] == 8.42
    assert result[0]["cost"] < total_cost(case, events[-1]["term_sheets"][0]["terms"])
    assert (case, events, packages) == original


def test_large_anchor_gap_does_not_allow_unbounded_acceleration():
    case, events, packages = setup()
    events[-1]["term_sheets"][0]["terms"]["unit_price"] = 9.88
    result = match_material_progress(case, events, packages, lambda t: total_cost(case, t))
    assert result[0]["terms"]["unit_price"] == 8.24
    assert result[0]["terms"]["payment_days"] == packages[0]["terms"]["payment_days"]


def test_accelerated_step_cannot_cross_buyer_alternative():
    case, events, packages = setup()
    counter = events[-1]["term_sheets"][0]["terms"]
    case["buyer"]["alternative"] = {"kind": "current_contract", "terms": {**counter, "unit_price": 8.35}}
    assert match_material_progress(case, events, packages, lambda t: total_cost(case, t)) == packages


@pytest.mark.parametrize(
    "mode",
    ["no_discount", "tiny_discount", "leverage", "strategic", "terminal", "shorter_payment", "service_cut", "ceiling"],
)
def test_unsupported_progress_preserves_policy(mode):
    case, events, packages = setup()
    counter = events[-1]["term_sheets"][0]["terms"]
    if mode == "no_discount":
        counter["unit_price"] = 10
    elif mode == "tiny_discount":
        counter["unit_price"] = 9.95
    elif mode == "leverage":
        case["buyer"]["category_goal"] = "leverage"
    elif mode == "strategic":
        case["buyer"]["category_goal"] = "stabilise"
    elif mode == "terminal":
        events[-1]["status"] = "final_offer"
    elif mode == "shorter_payment":
        counter["payment_days"] = 14
    elif mode == "service_cut":
        case["negotiable"].append("lead_time_days")
        case["fixed_terms"].pop("lead_time_days")
        case["term_options"]["lead_time_days"] = [42, 56]
        for event in events:
            for s in event["term_sheets"]:
                s["terms"]["lead_time_days"] = 42
        counter["lead_time_days"] = 56
        packages[0]["terms"]["lead_time_days"] = 42
    else:
        case["buyer"]["alternative"] = {"kind": "current_contract", "terms": {**counter, "unit_price": 8.1}}
    assert match_material_progress(case, events, packages, lambda t: total_cost(case, t)) == packages


def test_does_not_reapply_prior_discount_after_supplier_stops_moving():
    case, events, packages = setup()
    events += [copy.deepcopy(events[1]), copy.deepcopy(events[-1])]
    assert match_material_progress(case, events, packages, lambda t: total_cost(case, t)) == packages


def test_step_is_capped_at_quarter_gap_and_never_retracts_existing_offer():
    case, events, packages = setup()
    events[1]["term_sheets"][0]["terms"]["unit_price"] = 9.6
    packages[0]["terms"]["unit_price"] = 9.61
    result = match_material_progress(case, events, packages, lambda t: total_cost(case, t))
    assert result[0]["terms"]["unit_price"] == 9.62
    packages[0]["terms"]["unit_price"] = 9.65
    assert match_material_progress(case, events, packages, lambda t: total_cost(case, t)) == packages


def test_cash_discount_does_not_trigger_when_better_complete_quote_barely_moved():
    case, events, packages = setup()
    events[-1]["term_sheets"] = [
        {"terms": {"unit_price": 9.95, "payment_days": 90}},
        {"terms": {"unit_price": 9.9, "payment_days": 14}},
    ]
    assert match_material_progress(case, events, packages, lambda t: total_cost(case, t)) == packages
