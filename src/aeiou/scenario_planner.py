"""Comparable exploratory packages and transparent buyer-cost stress scenarios."""

import copy
import math

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms

from .optimizer import estimate, valid_terms

SCENARIOS = (
    {"id": "base", "label": "Published case rates"},
    {"id": "freight", "label": "Freight rates +25%", "freight_multiplier": 1.25},
    {"id": "capital", "label": "Buyer financing rate +4 percentage points", "interest_increment": 0.04},
    {
        "id": "fallback",
        "label": "Fallback price +10%, fallback risk +3 percentage points",
        "fallback_multiplier": 1.10,
        "risk_increment": 3,
    },
)


def stressed_case(case, scenario):
    changed = copy.deepcopy(case)
    buyer = changed["buyer"]
    buyer["freight_eur_per_unit"] = {
        k: v * scenario.get("freight_multiplier", 1) for k, v in buyer["freight_eur_per_unit"].items()
    }
    buyer["interest_rate"] += scenario.get("interest_increment", 0)
    buyer["fallback_unit_price"] *= scenario.get("fallback_multiplier", 1)
    buyer["fallback_risk_pct"] = buyer.get("fallback_risk_pct", 0) + scenario.get("risk_increment", 0)
    return changed


def equivalent_probe(case, base, field, value, cost):
    """Solve price against buyer cost, with bounded search for nonlinear relationship costs.

    This is a hypothetical offer. No supplier acceptance or feasibility is inferred.
    """
    terms = {**base, field: value}
    target = cost(base)
    lo, hi = 0.01, base["unit_price"]
    # Restrict probes to a lower cash price in exchange for a less favorable buyer term.
    if cost({**terms, "unit_price": lo}) > target or cost(terms) < target:
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        if cost({**terms, "unit_price": mid}) > target:
            hi = mid
        else:
            lo = mid
    terms["unit_price"] = round(lo, 2)
    if valid_terms(case, terms) and abs(cost(terms) - target) <= max(1, target * 0.0001):
        return terms
    return None


def plan_packages(case, events, sid, intelligence, candidates, state):
    std, supplier = standard_terms(events), estimate(case, sid, standard_terms(events), intelligence)

    def cost(terms):
        return total_cost(case, terms, std, supplier)

    packages = copy.deepcopy(candidates)
    if not packages:
        return {"packages": [], "recommendation": None, "scenarios": list(SCENARIOS)}
    base = packages[0]["terms"]
    observed = {p["field"] for p in state["preferences"] if p["confidence"] == "tentative"}
    # Preserve the best-cost package and early-payment alternative. Use the remaining
    # slot for a genuinely different question while time remains for its answer.
    if state["moves_left"] > 2:
        fields = sorted(("incoterm", "contract_months"), key=lambda f: f in observed)
        for field in fields:
            if field not in case["negotiable"]:
                continue
            probe = next(
                (
                    t
                    for value in case.get("term_options", {}).get(field, [])
                    if value != base.get(field)
                    and (t := equivalent_probe(case, base, field, value, cost)) is not None
                    and t not in [p["terms"] for p in packages]
                ),
                None,
            )
            if probe is not None:
                packages = packages[:2] + [
                    {"id": "C", "label": f"Explore {field}", "terms": probe, "cost": cost(probe)}
                ]
                break
    scenario_cases = [(s, stressed_case(case, s)) for s in SCENARIOS]
    for package in packages:
        package["cost"] = cost(package["terms"])
        package["scenario_costs"] = {s["id"]: total_cost(c, package["terms"], std, supplier) for s, c in scenario_cases}
        if not all(math.isfinite(v) for v in package["scenario_costs"].values()):
            raise ValueError("Nonfinite scenario cost")
    for package in packages:
        package["worst_cost"] = max(package["scenario_costs"].values())
        package["worst_regret"] = max(
            value - min(p["scenario_costs"][scenario] for p in packages)
            for scenario, value in package["scenario_costs"].items()
        )
        package["preference_matches"] = sum(
            pref["confidence"] == "tentative" and package["terms"].get(pref["field"]) == pref["value"]
            for pref in state["preferences"]
        )
    recommendation = min(packages, key=lambda p: (p["worst_regret"], p["cost"]))
    return {
        "packages": packages,
        "recommendation": recommendation["id"],
        "scenarios": list(SCENARIOS),
        "limitation": (
            "Buyer-cost sensitivity only. Assumptions are illustrative stresses, not forecasts of supplier behavior."
        ),
    }
