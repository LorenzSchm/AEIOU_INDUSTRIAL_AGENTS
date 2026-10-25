import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.commercial_portfolio import portfolio_probes
from aeiou.optimizer import valid_terms
from aeiou.portfolio_value import bounded_anchor, risk_exchange_ceiling, sensitivity_cost
from aeiou.scenario_planner import equivalent_package
from aeiou.simulation import local_cases


def setup_case():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNSEEN-VALUE", negotiable=["unit_price", "payment_days"])
    case["term_options"] = {"payment_days": [21, 45, 75]}
    case["buyer"].update(category_goal="stabilise", relationship_eur_per_point=14000, sustainable_margin_pct=1)
    base = {"unit_price": 25.0, "payment_days": 21}
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": {**base, "unit_price": 30}}]}]
    model = {"full_cost": 25, "interest_rate": 0.12, "standard": {"payment_days": 45}}
    scenarios = [{**model, "full_cost": cost, "weight": weight} for cost, weight in [(21, 0.35), (23, 0.5), (25, 0.15)]]
    cost = lambda t: total_cost(case, t, {}, model)  # noqa: E731
    return case, base, events, scenarios, cost


def test_first_anchor_improves_uncertainty_cost_inside_existing_point_envelope():
    case, base, events, scenarios, cost = setup_case()
    original = {"id": "A", "terms": base, "cost": cost(base)}
    result = bounded_anchor(case, events, original, {}, cost, scenarios)
    assert result["terms"]["unit_price"] < base["unit_price"]
    assert result["cost"] <= original["cost"] * 1.0025
    assert sensitivity_cost(case, result["terms"], {}, scenarios) < sensitivity_cost(case, base, {}, scenarios)
    assert valid_terms(case, result["terms"])
    assert original["terms"] == base
    # Uniform rescaling of scenario weights cannot alter the decision.
    rescaled = [{**s, "weight": s["weight"] * 7} for s in scenarios]
    assert bounded_anchor(case, events, original, {}, cost, rescaled)["terms"] == result["terms"]


@pytest.mark.parametrize("status", ["agreed", "closed", "final_offer"])
def test_uncertain_estimate_never_reopens_terminal_supplier(status):
    case, base, events, scenarios, cost = setup_case()
    events[-1]["status"] = status
    package = {"id": "A", "terms": base, "cost": cost(base)}
    assert bounded_anchor(case, events, package, {}, cost, scenarios) == package


def test_uncertain_estimate_never_retracts_executed_price_or_acts_without_evidence():
    case, base, events, scenarios, cost = setup_case()
    package = {"id": "A", "terms": base, "cost": cost(base)}
    assert bounded_anchor(case, events, package, {}, cost, None) == package
    events.insert(0, {"from": "buyer", "term_sheets": [{"terms": base}]})
    assert bounded_anchor(case, events, package, {}, cost, scenarios) == package


def test_index_is_benchmarked_against_best_fixed_option_not_expensive_opening():
    case, base, _, scenarios, _ = setup_case()
    case["negotiable"].append("index_clause")
    fixed = {**base, "index_clause": None}
    packages = [{"terms": fixed}, {"terms": {**fixed, "unit_price": 26}}]
    limit = risk_exchange_ceiling(case, packages, {}, scenarios)
    assert limit == sensitivity_cost(case, fixed, {}, scenarios) * 1.0025
    clause = {"series": "UNSEEN", "material_share": 0.5, "deadband_pct": 0, "base_month": "2026-01"}
    case["buyer"].update(index_risk_pct=10, index_paths={"UNSEEN": 0.08})
    assert sensitivity_cost(case, {**fixed, "index_clause": clause}, {}, scenarios) > limit


def test_cash_and_delivery_flexibility_require_full_compensation_and_a_real_reply():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNSEEN-FLEX", negotiable=["unit_price", "payment_days", "lead_time_days"])
    for field in ("payment_days", "lead_time_days"):
        case["fixed_terms"].pop(field, None)
    case["term_options"] = {"payment_days": [21, 45, 75], "lead_time_days": [24, 32, 48]}
    case["buyer"].update(
        category_goal="leverage",
        need_lead_time_days=24,
        late_cost_per_day=170,
        must_haves=[{"field": "lead_time_days", "op": "<=", "value": 32}],
    )
    base = {"unit_price": 23, "payment_days": 75, "lead_time_days": 24}
    current = {**base, "unit_price": 28, "payment_days": 21}
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": current}]}]
    cost = lambda t: total_cost(case, t)  # noqa: E731
    probes = portfolio_probes(case, events, base, cost, None, None, equivalent_package)
    assert not any(p["issue"] == "delivery_flex" for p in probes)
    events.insert(0, {"from": "buyer", "term_sheets": [{"terms": base}]})
    probes = portfolio_probes(case, events, base, cost, None, None, equivalent_package)
    flex = next(p for p in probes if p["issue"] == "delivery_flex")
    assert flex["terms"]["payment_days"] == 21
    assert flex["terms"]["lead_time_days"] == 32
    assert flex["terms"]["unit_price"] < base["unit_price"]
    assert valid_terms(case, flex["terms"])
    assert cost(flex["terms"]) <= cost(base)
    # A previously granted price cannot be retracted to fund this exchange.
    events.insert(0, {"from": "buyer", "term_sheets": [{"terms": {**flex["terms"], "unit_price": 28}}]})
    assert not any(
        p["issue"] == "delivery_flex"
        for p in portfolio_probes(case, events, base, cost, None, None, equivalent_package)
    )
