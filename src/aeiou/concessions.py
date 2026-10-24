"""Keep observed, still-safe concessions when candidate portfolios are rebuilt."""

import copy

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .optimizer import valid_terms


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
