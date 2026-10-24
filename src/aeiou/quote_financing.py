"""A bounded closing sensitivity from observable supplier payment/price pairs.

An implied financing rate describes quoted trade-offs, not the supplier's true
cost of capital. It never changes production costs or hypothetical bid generation.
"""

import math

from arena_kit.cost import closing_terms, must_haves_ok, supplier_value, total_cost
from arena_kit.docs import standard_terms


def commercial_structure(terms):
    return {k: v for k, v in terms.items() if k not in {"unit_price", "payment_days"}}


def implied_rate(case, first, second, supplier):
    if commercial_structure(first) != commercial_structure(second) or first["payment_days"] == second["payment_days"]:
        return None
    early, late = sorted((first, second), key=lambda t: t["payment_days"])
    if late["unit_price"] < early["unit_price"]:
        return None
    differences = [
        supplier_value(case, late, {**supplier, "interest_rate": rate})
        - supplier_value(case, early, {**supplier, "interest_rate": rate})
        for rate in (0, 1)
    ]
    slope = differences[1] - differences[0]
    if abs(slope) < 1e-9:
        return None
    rate = -differences[0] / slope
    return rate if math.isfinite(rate) and 0 <= rate <= 1 else None


def pair_key(first, second):
    return tuple(sorted((t["unit_price"], t["payment_days"]) for t in (first, second)))


def observed_financing_close(case, events, supplier, confidence, rows, baseline):
    """Require a current menu plus a distinct preceding, consistent quoted trade.

    Only compare currently acceptable offers from the baseline supplier. Direct
    cost and cost sensitivities must improve; original point cost can worsen by
    at most 0.25%. No cheaper hypothetical or withdrawn offer is executable here.
    """
    if (
        not supplier
        or len(rows) < 2
        or "payment_days" not in case["negotiable"]
        or case["buyer"].get("category_goal") not in {"stabilise", "secure_supply"}
        or type(confidence) not in (int, float)
        or not 0 < confidence < 100
    ):
        return baseline
    std = standard_terms(events)
    base = baseline["terms"]
    history = []
    for index, message in enumerate(events[:-1]):
        if message.get("from") == "buyer" or message.get("status") not in {"open", "final_offer", "agreed"}:
            continue
        sheets = message.get("term_sheets", [])
        if not sheets:
            continue
        try:
            terms = closing_terms(case, sheets[0].get("terms", {}), standard_terms(events[: index + 1]))
            valid = (
                commercial_structure(terms) == commercial_structure(base)
                and all(
                    type(terms.get(f)) in (int, float) and math.isfinite(terms[f])
                    for f in ("unit_price", "payment_days")
                )
                and terms["unit_price"] > 0
                and must_haves_ok(case, terms)
                and all(terms.get(f) in options for f, options in case.get("term_options", {}).items() if options)
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            continue
        if not history or terms != history[-1]:
            history.append(terms)
    alternatives = []
    for row in rows:
        terms = row["terms"]
        if row is baseline or row["cost"] > baseline["cost"] * 1.0025:
            continue
        rate = implied_rate(case, base, terms, supplier)
        if rate is None or total_cost(case, terms, std) >= total_cost(case, base, std) - 0.01:
            continue
        current_key = pair_key(base, terms)
        preceding = [
            (a, b)
            for a, b in zip(history, history[1:], strict=False)
            if pair_key(a, b) != current_key and implied_rate(case, a, b, supplier) is not None
        ]
        if not preceding:
            continue
        previous_rate = implied_rate(case, *preceding[-1], supplier)
        if abs(rate - previous_rate) > 0.01:
            continue
        low, high = max(0, min(rate, previous_rate) - 0.01), min(1, max(rate, previous_rate) + 0.01)
        deltas = []
        for factor in (1 - confidence / 100, 1, 1 + confidence / 100):
            for assumed_rate in (low, rate, high):
                model = {**supplier, "interest_rate": assumed_rate, "full_cost": supplier["full_cost"] * factor}
                deltas.append(total_cost(case, terms, std, model) - total_cost(case, base, std, model))
        if any(not math.isfinite(d) or d >= -0.01 for d in deltas):
            continue
        alternatives.append(
            {
                **row,
                "closing_sensitivity": {
                    "baseline_offer": baseline["sheet_id"],
                    "quoted_rate": rate,
                    "preceding_rate": previous_rate,
                    "rate_range": [low, high],
                    "worst_cost_delta": max(deltas),
                    "point_cost_limit": baseline["cost"] * 1.0025,
                    "limitation": "Quote-implied sensitivity, not a measured supplier rate or guaranteed score.",
                },
            }
        )
    return min(alternatives, key=lambda r: total_cost(case, r["terms"], std), default=baseline)
