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


def continued_situation():
    case, events, packages, cost = situation()
    first = combine_cash_continuity(case, events, packages, cost, equivalent_package)
    events.append({"from": "buyer", "term_sheets": [{"terms": p["terms"]} for p in first]})
    events.append(
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"terms": {**packages[0]["terms"], "unit_price": 11.7}},
                {"terms": {**first[2]["terms"], "unit_price": 11.5}},
            ],
        }
    )
    packages[0]["terms"]["unit_price"] = 10.2
    packages[1]["terms"] = equivalent_package(case, packages[0]["terms"], packages[1]["terms"], cost)
    return case, events, packages, cost, first


def test_countered_combination_advances_on_new_cost_frontier_without_fresh_cash_pair():
    case, events, packages, cost, first = continued_situation()
    before = copy.deepcopy((case, events, packages))
    result = combine_cash_continuity(case, events, packages, cost, equivalent_package)
    assert result[2]["package_stage"] == "continue"
    assert result[2]["terms"]["unit_price"] > first[2]["terms"]["unit_price"]
    assert result[2]["terms"]["payment_days"] == 21
    assert result[2]["terms"]["contract_months"] == 48
    assert result[:2] == packages[:2]
    assert cost(result[2]["terms"]) <= cost(packages[0]["terms"]) + 0.001
    assert (case, events, packages) == before


@pytest.mark.parametrize(
    "mode", ["withdrawn_structure", "changed_structure", "terminal", "no_progress", "protected_close"]
)
def test_continuation_requires_live_support_and_safe_progress(mode):
    case, events, packages, cost, _ = continued_situation()
    # A follow-up must earn its slot even if generic exploration has ended.
    packages[2]["label"] = "Maintain observed concession"
    if mode == "withdrawn_structure":
        events[-1]["term_sheets"].pop()
    elif mode == "changed_structure":
        events[-1]["term_sheets"][1]["terms"]["contract_months"] = 36
    elif mode == "terminal":
        events[-1]["status"] = "final_offer"
    elif mode == "no_progress":
        packages[0].pop("reciprocal_progress")
    else:
        packages[2]["closing_probe"] = True
    assert combine_cash_continuity(case, events, packages, cost, equivalent_package) == packages


@pytest.mark.parametrize("scale", [0.4, 3.0])
def test_full_agent_keeps_advancing_countered_package_in_unseen_trajectory(scale):
    from aeiou.research import package
    from aeiou.strategic import StrategicNegotiator

    case, _, _, _ = situation()
    case["buyer"]["fallback_unit_price"] *= scale
    case["buyer"]["current_unit_price"] *= scale
    if case["buyer"].get("alternative", {}).get("terms"):
        case["buyer"]["alternative"]["terms"]["unit_price"] *= scale
    base = {"unit_price": 12 * scale, "payment_days": 75, "contract_months": 24}
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": base}]}]
    agent = StrategicNegotiator(package())

    def decide():
        for index, message in enumerate(events):
            if message["from"] != "buyer":
                for sheet_index, sheet in enumerate(message["term_sheets"]):
                    sheet.setdefault("term_sheet_id", f"offer:unseen-{index}-{sheet_index}")
        return agent.decide(case, {"sup:UNSEEN": events})["message"]

    opening = decide()
    events += [
        {"from": "buyer", **opening},
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"terms": {**base, "unit_price": 11.7 * scale}},
                {"terms": {**base, "unit_price": 11.6 * scale, "payment_days": 21}},
            ],
        },
    ]
    second = decide()
    combined = next(
        s["terms"]
        for s in second["term_sheets"]
        if s["terms"]["payment_days"] == 21 and s["terms"]["contract_months"] > 24
    )
    events += [
        {"from": "buyer", **second},
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"terms": {**base, "unit_price": 11.4 * scale}},
                {"terms": {**combined, "unit_price": 11.3 * scale}},
            ],
        },
    ]
    third = decide()
    continued = next(
        s["terms"]
        for s in third["term_sheets"]
        if s["terms"]["payment_days"] == 21 and s["terms"]["contract_months"] == combined["contract_months"]
    )
    assert continued["unit_price"] > combined["unit_price"]
    assert total_cost(case, continued) <= total_cost(case, third["term_sheets"][0]["terms"]) + 0.001
    assert len(third["term_sheets"]) == 3
