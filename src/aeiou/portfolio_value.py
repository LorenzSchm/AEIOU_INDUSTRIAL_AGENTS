"""Bounded use of uncertain costs; no outcomes or challenge identities as inputs."""

import math

from arena_kit.cost import closing_terms, total_cost

from .optimizer import valid_terms


def fixed_price_first(case, events):
    """Test a sustainable fixed-price opening before offering costly index risk.

    This is a one-message discovery step, not a ban on indexed agreements.
    Once the supplier has responded to a buyer proposal, ordinary compensated
    risk exploration resumes. Existing indexed quotes and other categories keep
    their current policy. No case IDs or post-run scoring inputs are used.
    """
    return bool(
        case["buyer"].get("category_goal") in {"stabilise", "secure_supply"}
        and case["buyer"].get("index_risk_pct", 0) > 0
        and "index_clause" in case["negotiable"]
        and None in case.get("term_options", {}).get("index_clause", [])
        and events
        and events[-1].get("status") == "open"
        and not any(m.get("from") == "buyer" for m in events)
        and any(
            s.get("terms", {}).get("unit_price") is not None
            and "index_clause" in s.get("terms", {})
            and s["terms"]["index_clause"] is None
            for s in events[-1].get("term_sheets", [])
        )
    )


def sensitivity_cost(case, terms, standard, scenarios):
    weight = sum(s["weight"] for s in scenarios)
    return sum(s["weight"] * total_cost(case, terms, standard, s) for s in scenarios) / weight


def bounded_anchor(case, events, package, standard, cost, scenarios):
    """Improve sensitivity cost within the existing 0.25% point-cost envelope.

    Only first contact: a new estimate never retracts an executed concession.
    This changes price, not coverage or the production acceptance cost model.
    """
    if (
        not scenarios
        or case["buyer"].get("category_goal") not in {"stabilise", "secure_supply"}
        or not events
        or events[-1].get("status") != "open"
        or any(m.get("from") == "buyer" for m in events)
    ):
        return package
    base = package["terms"]
    quotes = [
        {f: v for f, v in closing_terms(case, sheet.get("terms", {}), standard).items() if f in case["negotiable"]}
        for sheet in events[-1].get("term_sheets", [])
    ]
    quotes = [t for t in quotes if valid_terms(case, t)]
    if not quotes:
        return package
    ceiling = min(cost(base) * 1.0025, min(cost(t) for t in quotes) - 0.01)
    original = sensitivity_cost(case, base, standard, scenarios)
    lower, upper = max(1, math.ceil(base["unit_price"] * 85)), round(base["unit_price"] * 100)
    stride = max(1, math.ceil((upper - lower) / 128))

    def candidate(cents):
        terms = {**base, "unit_price": cents / 100}
        point = cost(terms)
        if not valid_terms(case, terms) or not math.isfinite(point) or point > ceiling:
            return None
        expected = sensitivity_cost(case, terms, standard, scenarios)
        return (expected, cents, point) if math.isfinite(expected) and expected < original - 0.01 else None

    choices = [row for cents in range(lower, upper + 1, stride) if (row := candidate(cents))]
    if not choices:
        return package
    center = min(choices)[1]
    choices += [
        row
        for cents in range(max(lower, center - stride), min(upper, center + stride) + 1)
        if (row := candidate(cents))
    ]
    expected, cents, point = min(choices)
    return {
        **package,
        "terms": {**base, "unit_price": cents / 100},
        "cost": point,
        "uncertainty_anchor": {
            "original_price": base["unit_price"],
            "original_expected_cost": original,
            "expected_cost": expected,
            "point_cost_limit": ceiling,
            "limitation": "Uncalibrated sensitivities, not a predicted score or acceptance probability.",
        },
    }


def risk_exchange_ceiling(case, packages, standard, scenarios):
    """Do not offer a costly risk option merely because it beats the opening quote."""
    fixed = [p for p in packages if p["terms"].get("index_clause") is None]
    if not scenarios or not fixed or "index_clause" not in case["negotiable"]:
        return None
    return min(sensitivity_cost(case, p["terms"], standard, scenarios) for p in fixed) * 1.0025
