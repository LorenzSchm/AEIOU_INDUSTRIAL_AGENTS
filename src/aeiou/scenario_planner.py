"""Comparable exploratory packages and transparent buyer-cost stress scenarios."""

import copy
import math

from arena_kit.cost import complete_offer, total_cost
from arena_kit.docs import standard_terms

from .optimizer import estimate, valid_terms
from .term_exchange import exchange_history, ranked_exchanges

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
    return equivalent_package(case, base, {**base, field: value}, cost)


def equivalent_package(case, base, terms, cost):
    """Compensate an observed commercial structure without raising buyer cost."""
    terms = {**terms, "unit_price": base["unit_price"]}
    if not valid_terms(case, terms):
        return None
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
    # Whole-cent contracts cannot always hit a fractional-cent equal-cost root.
    # Round toward the buyer and allow at most one price tick of compensation.
    terms["unit_price"] = max(0.01, math.floor(lo * 100 + 1e-7) / 100)
    priced = cost(terms)
    tick = abs(cost({**terms, "unit_price": round(terms["unit_price"] + 0.01, 2)}) - priced)
    if valid_terms(case, terms) and priced <= target + 0.001 and target - priced <= max(1, tick + 0.01):
        return terms
    return None


def counteroffer_probe(case, events, base, std, cost):
    """Use explicit counteroffer terms, never infer a binding promise from prose."""
    buyer = next((m for m in reversed(events) if m.get("from") == "buyer"), None)
    if buyer is None or events[-1].get("from") == "buyer":
        return None
    offered = [complete_offer(case, {**std, **p.get("terms", {})}) for p in buyer.get("term_sheets", [])]
    fields = [f for f in case["negotiable"] if f != "unit_price"]
    probes = []
    for sheet in events[-1].get("term_sheets", []):
        terms = complete_offer(case, {**std, **sheet.get("terms", {})})
        # Once the supplier has countered with an index clause, retain that
        # alternative while the protected buyer package still has a fixed price.
        # Otherwise matching our last probe would make it disappear next turn.
        retain_index = (
            "index_clause" in fields and terms.get("index_clause") is not None and base.get("index_clause") is None
        )
        if not retain_index and any(all(terms.get(f) == p.get(f) for f in fields) for p in offered):
            continue
        probe = equivalent_package(case, base, terms, cost)
        if probe is not None and probe != base:
            probes.append(probe)
    # At equal buyer cost, retain the commercially closest price to the base.
    return max(probes, key=lambda p: p["unit_price"], default=None)


def plan_packages(case, events, sid, intelligence, candidates, state, use_counteroffers=True):
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
    # Respond to the supplier's actual counter even when it varies several terms
    # (e.g. an index clause with a longer contract, or a feasible delivery date).
    # A stays protected; the extra structure requires a compensating cash price.
    if use_counteroffers and state["moves_left"] > 0:
        probe = counteroffer_probe(case, events, base, std, cost)
        if probe is not None and probe not in [p["terms"] for p in packages]:
            packages = packages[:2] + [
                {
                    "id": "C",
                    "label": "Supplier terms with price compensation",
                    "terms": probe,
                    "cost": cost(probe),
                    "feedback_probe": True,
                }
            ]
    exchanges = exchange_history(case, events) if use_counteroffers else []
    # A single-term experiment makes the next reply interpretable. Reserve B
    # for this exchange and keep C available for bundled supplier counteroffers.
    if use_counteroffers and state["moves_left"] > 2:
        for exchange in ranked_exchanges(case, base, exchanges):
            probe = equivalent_probe(case, base, exchange["field"], exchange["value"], cost)
            if probe is None or probe["unit_price"] >= base["unit_price"]:
                continue
            if any(probe == p["terms"] for p in packages if p["id"] != "B"):
                continue
            swap = {
                "id": "B",
                "label": f"Conditional {exchange['field']} exchange",
                "terms": probe,
                "cost": cost(probe),
                "exchange_probe": True,
                "exchange": {
                    **exchange,
                    "required_price_reduction": round(base["unit_price"] - probe["unit_price"], 2),
                },
            }
            packages = [packages[0], swap] + [p for p in packages[1:] if p["id"] != "B"][:1]
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
        "exchange_observations": exchanges,
        "limitation": (
            "Buyer-cost sensitivity only. Assumptions are illustrative stresses, not forecasts of supplier behavior."
        ),
    }
