"""A bounded first-contact payment comparison, with the incumbent menu intact."""

import math

from arena_kit.cost import closing_terms, total_cost

from .optimizer import valid_terms


def paired_payment(case, events, packages, standard, cost, ceiling, scenarios=()):
    """Ask for the next payment step on the same indexed contract.

    Price the new option using complete buyer costs, including supplier health.
    Keep point cost no worse than the incumbent indexed option, inside the
    existing 0.5% quote tolerance. Direct and sensitivity costs must beat a quote.
    Neither a quoted financing rate nor a historical winning price is assumed.
    """
    if (
        case["buyer"].get("category_goal") not in {"stabilise", "secure_supply"}
        or not {"payment_days", "index_clause"} <= set(case["negotiable"])
        or not 0 < len(packages) < 3
        or not events
        or events[-1].get("status") != "open"
        or any(m.get("from") == "buyer" for m in events)
    ):
        return packages
    indexed = [p for p in packages if p["terms"].get("index_clause")]
    if not indexed:
        return packages
    base = min(indexed, key=lambda p: p["cost"])["terms"]
    days = sorted(d for d in case.get("term_options", {}).get("payment_days", []) if d > base["payment_days"])
    if not days:
        return packages
    shape = {**base, "payment_days": days[0]}
    fixed = total_cost(case, {**shape, "unit_price": 0}, standard)
    slope = total_cost(case, {**shape, "unit_price": 1}, standard) - fixed
    if slope <= 0:
        return packages
    quotes = [
        {f: v for f, v in closing_terms(case, s.get("terms", {}), standard).items() if f in case["negotiable"]}
        for s in events[-1].get("term_sheets", [])
    ]
    quotes = [q for q in quotes if valid_terms(case, q)]
    if not quotes:
        return packages
    limit = min(ceiling, cost(base), min(cost(q) for q in quotes) * 1.005)
    direct_limit = min(total_cost(case, q, standard) for q in quotes)
    lower = math.ceil(base["unit_price"] * 100)
    upper = math.floor((direct_limit - fixed) / slope * 100)

    def expected(t):
        return (
            (
                sum(s["weight"] * total_cost(case, t, standard, s) for s in scenarios)
                / sum(s["weight"] for s in scenarios)
            )
            if scenarios
            else cost(t)
        )

    expected_limit = min(expected(q) for q in quotes)

    def candidate(cents):
        terms = {**shape, "unit_price": cents / 100}
        value = cost(terms)
        if (
            not valid_terms(case, terms)
            or not math.isfinite(value)
            or value > limit
            or total_cost(case, terms, standard) >= direct_limit - 0.01
        ):
            return None
        modeled = expected(terms)
        if not math.isfinite(modeled) or modeled >= expected_limit - 0.01:
            return None
        return modeled, cents, value

    stride = max(1, math.ceil((upper - lower) / 128))
    options = [row for cents in range(lower, upper + 1, stride) if (row := candidate(cents))]
    if not options:
        return packages
    center = min(options)[1]
    options += [
        row
        for cents in range(max(lower, center - stride), min(upper, center + stride) + 1)
        if (row := candidate(cents))
    ]
    modeled, cents, value = min(options)
    terms = {**shape, "unit_price": cents / 100}
    identity = next(label for label in ("C", "D", "E") if label not in {p["id"] for p in packages})
    return packages + [
        {
            "id": identity,
            "terms": terms,
            "cost": value,
            "label": "Same index protection with the next payment term",
            "paired_payment_probe": True,
            "reference_terms": base,
            "point_cost_limit": limit,
            "expected_cost": modeled,
            "limitation": "Cost-bounded request; supplier acceptance and relationship-cost effects are unknown.",
        }
    ]
