"""Keep observed, still-safe concessions when candidate portfolios are rebuilt."""

import copy
import math

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left

from .optimizer import alternative_cost, valid_terms


def match_material_progress(case, events, packages, cost):
    """Bounded reciprocity after an observable discount in a routine purchase.

    A material cumulative discount activates the rule; only the latest supplier
    price step is reciprocated. Payment/duration improvements may accompany it,
    but reduced service, quantity, duration or payment time must not masquerade
    as a like-for-like discount. No supplier floor or acceptance is inferred.
    """
    if (
        case["buyer"].get("category_goal") != "routine"
        or not packages
        or latest(events).get("status") != "open"
        or moves_left(case, events) <= 2
    ):
        return packages
    quotes = []
    for index, message in enumerate(events):
        if message.get("from") == "buyer" or message.get("status") != "open":
            continue
        std = standard_terms(events[: index + 1])
        full = [closing_terms(case, s.get("terms", {}), std) for s in message.get("term_sheets", [])]
        full = [t for t in full if valid_terms(case, {f: t.get(f) for f in case["negotiable"]})]
        if full:
            quotes.append(min(full, key=cost))
    if len(quotes) < 2:
        return packages
    opening, previous, current = quotes[0], quotes[-2], quotes[-1]
    step = previous["unit_price"] - current["unit_price"]
    cumulative = (opening["unit_price"] - current["unit_price"]) / opening["unit_price"]

    def shape(t):
        return {k: v for k, v in t.items() if k not in {"unit_price", "payment_days", "contract_months"}}

    if (
        step <= 0.001
        or cumulative < 0.01
        or shape(opening) != shape(current)
        or shape(previous) != shape(current)
        or any(current[f] < max(opening[f], previous[f]) for f in ("payment_days", "contract_months"))
        or cost(current) >= cost(previous) - 0.01
    ):
        return packages
    history = [s.get("terms", {}) for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    result = copy.deepcopy(packages)
    ceiling = min(alternative_cost(case), cost(current) - 0.01)
    for package in result:
        terms = package["terms"]
        prior = [
            t["unit_price"]
            for t in history
            if valid_terms(case, t) and all(t.get(f) == terms.get(f) for f in case["negotiable"] if f != "unit_price")
        ]
        if not prior:
            continue
        last = max(prior)
        gap = max(0, current["unit_price"] - last)
        # Never jump more than the supplier's observed step or a quarter of the
        # remaining gap. Existing stronger policy concessions are not withdrawn.
        bounded = math.floor((last + min(step, gap * 0.25)) * 100 + 1e-7) / 100
        price = max(terms["unit_price"], bounded)
        proposed = {**terms, "unit_price": price}
        if price <= terms["unit_price"] or not valid_terms(case, proposed) or cost(proposed) > ceiling:
            continue
        package.update(
            terms=proposed,
            cost=cost(proposed),
            reciprocal_progress={
                "supplier_price_step": step,
                "cumulative_discount": cumulative,
                "prior_buyer_price": last,
                "buyer_step_limit": min(step, gap * 0.25),
                "standing_cost": cost(current),
            },
        )
    return result


def preserve_progress(case, events, packages, cost, ceiling):
    if not packages or latest(events).get("status") != "open":
        return packages
    fields = [f for f in case["negotiable"] if f != "unit_price"]

    def same_shape(a, b):
        return all(a.get(f) == b.get(f) for f in fields)

    quotes = [
        closing_terms(case, s.get("terms", {}), standard_terms(events)) for s in latest(events).get("term_sheets", [])
    ]
    choices = []
    for message in events:
        if message.get("from") != "buyer":
            continue
        for sheet in message.get("term_sheets", []):
            terms = sheet.get("terms", {})
            if not valid_terms(case, terms):
                continue
            matching = [q for q in quotes if same_shape(terms, q)]
            if not matching or cost(terms) > ceiling or cost(terms) >= min(cost(q) for q in matching) - 0.01:
                continue
            if any(same_shape(terms, p["terms"]) and p["terms"]["unit_price"] >= terms["unit_price"] for p in packages):
                continue
            choices.append(terms)
    if not choices:
        return packages
    # The latest supported structure carries the strongest price concession we
    # actually sent on that structure, not a newly invented settlement price.
    shape = choices[-1]
    terms = max((t for t in choices if same_shape(t, shape)), key=lambda t: t["unit_price"])
    result = copy.deepcopy(packages)
    index = next((i for i in range(1, len(result)) if same_shape(result[i]["terms"], terms)), None)
    if index is None:
        index = next((i for i in reversed(range(1, len(result))) if not result[i].get("closing_probe")), None)
    if index is None and len(result) >= 3:
        return packages
    package = {
        "id": result[index]["id"] if index is not None else "R",
        "terms": dict(terms),
        "cost": cost(terms),
        "label": "Maintain observed concession",
        "retained_concession": True,
    }
    if index is None:
        result.append(package)
    else:
        result[index] = package
    return result
