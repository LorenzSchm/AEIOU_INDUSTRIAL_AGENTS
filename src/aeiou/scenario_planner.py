"""Comparable exploratory packages and transparent buyer-cost stress scenarios."""

import copy
import math

from arena_kit.cost import complete_offer
from arena_kit.docs import standard_terms

from .commercial_portfolio import combine_cash_continuity, merge_portfolio, portfolio_probes
from .economic_beliefs import total_cost
from .optimizer import estimate, valid_terms
from .portfolio_value import bounded_anchor, risk_exchange_ceiling, sensitivity_cost
from .supplier_signals import recent_kinds
from .term_exchange import exchange_history, ranked_exchanges
from .uncertainty import robust_quote_improvement

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


def equivalent_package(case, base, terms, cost, *, target_cost=None):
    """Compensate an observed commercial structure without raising buyer cost."""
    terms = {**terms, "unit_price": base["unit_price"]}
    if not valid_terms(case, terms):
        return None
    if (
        terms.get("contract_months", 0) < base.get("contract_months", 0)
        and terms.get("contract_months", 0) < case["buyer"]["horizon_years"] * 12
    ):
        return None
    target = cost(base) if target_cost is None else target_cost
    # Search a bounded plausible neighborhood in both directions. The relationship
    # penalty can make cost U-shaped; locate its minimum before the upper root.
    lower, upper = max(0.01, base["unit_price"] * 0.80), base["unit_price"] * 1.20
    lo, hi = lower, upper
    for _ in range(32):
        a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if cost({**terms, "unit_price": a}) < cost({**terms, "unit_price": b}):
            hi = b
        else:
            lo = a
    minimum = (lo + hi) / 2
    feasible = [v for v in (lower, minimum, base["unit_price"], upper) if cost({**terms, "unit_price": v}) <= target]
    if not feasible:
        return None
    lo, hi = max(feasible), upper
    for _ in range(36):
        mid = (lo + hi) / 2
        if cost({**terms, "unit_price": mid}) > target:
            hi = mid
        else:
            lo = mid
    # Check neighboring cents, including the case where the feasible minimum is
    # above the lower cent. Never round across the cost ceiling.
    prices = {math.floor(lo * 100 + 1e-7) / 100, math.ceil(lo * 100 - 1e-7) / 100}
    options = [
        {**terms, "unit_price": price}
        for price in prices
        if lower <= price <= upper and cost({**terms, "unit_price": price}) <= target + 0.001
    ]
    options = [t for t in options if valid_terms(case, t)]
    return max(options, key=lambda t: t["unit_price"], default=None)


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


def distinct_packages(packages):
    """Never let a supplier choose a higher price for the same commercial terms.

    A is the protected anchor. Other candidates must differ in a non-price term;
    settlement concessions on an identical structure belong in a later turn.
    """
    result = []
    for package in packages:
        structure = {k: v for k, v in package["terms"].items() if k != "unit_price"}
        duplicate = next(
            (p for p in result if {k: v for k, v in p["terms"].items() if k != "unit_price"} == structure),
            None,
        )
        if duplicate is None:
            result.append(package)
        elif duplicate["id"] != "A" and package["cost"] < duplicate["cost"]:
            result[result.index(duplicate)] = package
    return result


def plan_packages(case, events, sid, intelligence, candidates, state, use_counteroffers=True, use_portfolio=True):
    std, supplier = standard_terms(events), estimate(case, sid, standard_terms(events), intelligence)

    def cost(terms):
        return total_cost(case, terms, std, supplier)

    packages = copy.deepcopy(candidates)
    if not packages:
        return {"packages": [], "recommendation": None, "scenarios": list(SCENARIOS)}
    sensitivities = intelligence.get("economic_sensitivities", {}).get(sid, {}).get("scenarios")
    if use_portfolio and state["moves_left"] > 2:
        packages[0] = bounded_anchor(case, events, packages[0], std, cost, sensitivities)
    base = packages[0]["terms"]
    observed = {p["field"] for p in state["preferences"] if p["confidence"] == "tentative"}
    # Preserve the best-cost package and early-payment alternative. Use the remaining
    # slot for a genuinely different question while time remains for its answer.
    if state["moves_left"] > 2:
        fields = sorted(
            ("effective_date", "lead_time_days", "incoterm", "index_clause", "contract_months", "ppm_commitment"),
            key=lambda f: f in observed,
        )
        for field in fields:
            if field not in case["negotiable"]:
                continue
            values = case.get("term_options", {}).get(field, [])
            if field == "effective_date":
                # Prefer the date that saves actual contract spend at the same
                # price; do not choose the first date merely by enum order.
                values = sorted(values, key=lambda value: total_cost(case, {**base, field: value}, std))
            probe = next(
                (
                    t
                    for value in values
                    if value != base.get(field)
                    and (field != "lead_time_days" or value >= base.get(field, 0))
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
        else:
            # A point-estimate optimum can wrongly rule out every trade. Explore
            # only direct-cost-compensated variants that beat the real quote at
            # all three supplier-cost stress points. Keep protected A unchanged.
            replies = events[-1].get("term_sheets", [])
            current = complete_offer(case, {**std, **replies[0]["terms"]}) if replies else None
            uncertainty = intelligence.get("estimates", {}).get(sid, {}).get("confidence_pct")
            probes = []
            if current and supplier and uncertainty:
                for field in fields:
                    if field not in case["negotiable"]:
                        continue
                    for value in case.get("term_options", {}).get(field, []):
                        if value == base.get(field) or (field == "lead_time_days" and value < base[field]):
                            continue
                        probe = equivalent_probe(case, base, field, value, lambda t: total_cost(case, t, std))
                        if probe is None:
                            continue
                        stresses = robust_quote_improvement(case, probe, current, std, supplier, uncertainty)
                        if stresses:
                            probes.append(
                                {
                                    "id": "C",
                                    "label": f"Estimate-sensitive {field} exchange",
                                    "terms": probe,
                                    "cost": cost(probe),
                                    "standing_cost": cost(current),
                                    "uncertainty_probe": True,
                                    "estimate_stresses": stresses,
                                }
                            )
            if probes:
                packages = packages[:2] + [min(probes, key=lambda p: p["cost"])]
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
            if probe is None or probe == base:
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
    rounds = sum(m.get("from") == "buyer" for m in events)
    if (
        use_counteroffers
        and rounds >= 2
        and recent_kinds(state) & {"price_resistance", "closing_warning"}
        and not any(p.get("feedback_probe") for p in packages)
    ):
        # A separate settlement candidate may cost more than an ambitious anchor,
        # but must improve the actual standing quote. No acceptance is assumed.
        replies = events[-1].get("term_sheets", [])
        if replies:
            current = complete_offer(case, {**std, **replies[0].get("terms", {})})
            quote_cost = cost(current)
            step = min(
                current["unit_price"] * 0.01, max(0.01, abs(state.get("supplier_price_change") or 0) / max(1, rounds))
            )
            closing = {**current, "unit_price": round(max(0.01, current["unit_price"] - step), 2)}
            choices = [closing]
            if "effective_date" in case["negotiable"]:
                choices += [
                    {**closing, "effective_date": d} for d in case.get("term_options", {}).get("effective_date", [])
                ]
            choices = [t for t in choices if valid_terms(case, t) and cost(t) < quote_cost - 0.01]
            if choices:
                terms = min(choices, key=cost)
                if terms not in [p["terms"] for p in packages]:
                    packages = packages[:2] + [
                        {
                            "id": "C",
                            "label": "Observed quote with reciprocal improvement",
                            "terms": terms,
                            "cost": cost(terms),
                            "closing_probe": True,
                            "standing_cost": quote_cost,
                            "limitation": "Observed structure, acceptance unverified.",
                        }
                    ]
    if use_portfolio and state["moves_left"] > 2:
        probes = portfolio_probes(
            case,
            events,
            base,
            cost,
            supplier,
            intelligence.get("estimates", {}).get(sid, {}).get("confidence_pct"),
            equivalent_package,
            sensitivities=intelligence.get("economic_sensitivities", {}).get(sid, {}).get("scenarios"),
        )
        packages = merge_portfolio(packages, probes)
        packages = combine_cash_continuity(case, events, packages, cost, equivalent_package)
    packages = distinct_packages(packages)
    anchor_expected = packages[0].get("uncertainty_anchor", {}).get("expected_cost")
    if anchor_expected is not None:
        expected = lambda terms: sensitivity_cost(case, terms, std, sensitivities)  # noqa: E731
        survivors = [
            p
            for p in packages
            if p.get("bounded_uncertainty_probe")
            or p.get("uncertainty_probe")
            or expected(p["terms"]) <= anchor_expected * 1.0025
        ]
        for p in packages[1:] if len(survivors) < 2 else []:
            if (
                p.get("bounded_uncertainty_probe")
                or p.get("uncertainty_probe")
                or p.get("portfolio_probe")
                or p.get("feedback_probe")
                or p.get("closing_probe")
                or expected(p["terms"]) <= anchor_expected * 1.0025
            ):
                continue
            # Reprice a date/duration/cash alternative before discarding it.
            # Refining only A otherwise collapses first-contact trade discovery.
            terms = equivalent_package(case, p["terms"], p["terms"], expected, target_cost=anchor_expected * 1.0025)
            if (
                terms is not None
                and terms["unit_price"] <= p["terms"]["unit_price"]
                and cost(terms) <= p["cost"] * 1.0025
            ):
                p.update(terms=terms, cost=cost(terms), sensitivity_compensation=True)
                if p.get("exchange"):
                    p["exchange"]["required_price_reduction"] = round(base["unit_price"] - terms["unit_price"], 2)
        # A supplier may pick any sent option. A newly improved anchor is useless
        # if an expensive payment alternative gives the savings straight back.
        packages = [
            p
            for p in packages
            if p.get("bounded_uncertainty_probe")
            or p.get("uncertainty_probe")
            or sensitivity_cost(case, p["terms"], std, sensitivities) <= anchor_expected * 1.0025
        ]
    risk_ceiling = risk_exchange_ceiling(case, packages, std, sensitivities)
    if risk_ceiling is not None:
        packages = [
            p
            for p in packages
            if p["id"] == "A"
            or p.get("bounded_uncertainty_probe")
            or p.get("uncertainty_probe")
            or p["terms"].get("index_clause") is None
            or sensitivity_cost(case, p["terms"], std, sensitivities) <= risk_ceiling
        ]
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
        "risk_exchange_expected_cost_limit": risk_ceiling,
        "limitation": (
            "Buyer-cost sensitivity only. Assumptions are illustrative stresses, not forecasts of supplier behavior."
        ),
    }
