import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.commercial_portfolio import combine_cash_continuity
from aeiou.scenario_planner import equivalent_package
from aeiou.simulation import local_cases


def situation():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNSEEN-CASH", suppliers=["sup:UNSEEN"])
    case["buyer"].update(category_goal="routine", horizon_years=2)
    case["negotiable"] = ["unit_price", "payment_days", "contract_months"]
    case["fixed_terms"].pop("contract_months", None)
    case["term_options"] = {"payment_days": [21, 45, 75], "contract_months": [24, 36, 48]}
    base = {"unit_price": 10, "payment_days": 75, "contract_months": 24}
    cost = lambda t: total_cost(case, t)  # noqa: E731
    cash = equivalent_package(case, base, {**base, "payment_days": 21}, cost)
    packages = [
        {"id": "A", "terms": base, "reciprocal_progress": {"supplier_price_step": 0.2}},
        {"id": "B", "terms": cash},
        {"id": "C", "label": "Explore contract_months", "terms": {**base, "contract_months": 48}},
    ]
    events = [
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"terms": {**base, "unit_price": 12}},
                {"terms": {**base, "unit_price": 11.8, "payment_days": 21}},
            ],
        }
    ]
    return case, events, packages, cost


def test_combines_observed_cash_incentive_with_duration_without_raising_buyer_cost():
    case, events, packages, cost = situation()
    before = copy.deepcopy((case, events, packages))
    result = combine_cash_continuity(case, events, packages, cost, equivalent_package)
    assert result[:2] == packages[:2]
    assert result[2]["terms"]["payment_days"] == 21
    assert result[2]["terms"]["contract_months"] == 48
    assert cost(result[2]["terms"]) <= cost(packages[0]["terms"]) + 0.001
    assert result[2]["terms"]["unit_price"] < packages[0]["terms"]["unit_price"]
    assert (case, events, packages) == before


@pytest.mark.parametrize("mode", ["no_progress", "no_discount", "different_duration", "terminal", "protected_probe"])
def test_requires_real_discount_and_preserves_protected_options(mode):
    case, events, packages, cost = situation()
    if mode == "no_progress":
        packages[0].pop("reciprocal_progress")
    elif mode == "no_discount":
        events[-1]["term_sheets"][1]["terms"]["unit_price"] = 12
    elif mode == "different_duration":
        events[-1]["term_sheets"][1]["terms"]["contract_months"] = 36
    elif mode == "terminal":
        events[-1]["status"] = "final_offer"
    else:
        packages[2]["label"] = "Observed quote with reciprocal improvement"
    assert combine_cash_continuity(case, events, packages, cost, equivalent_package) == packages


def test_rejects_uncompensated_and_withdrawn_prior_concessions():
    case, events, packages, cost = situation()
    assert combine_cash_continuity(case, events, packages, cost, lambda *args: None) == packages
    prior = {**packages[0]["terms"], "payment_days": 21, "contract_months": 48, "unit_price": 10}
    events.insert(0, {"from": "buyer", "term_sheets": [{"terms": prior}]})
    # Honoring the prior price would exceed A's cost; omit rather than retract it.
    assert combine_cash_continuity(case, events, packages, cost, equivalent_package) == packages
