"""Protect known contract value when offering a price-change date."""

from arena_kit.cost import closing_terms, total_cost


def compensated_date(case, proposed, reference, standard=None):
    if "effective_date" not in case["negotiable"]:
        return True
    proposed = closing_terms(case, proposed, standard)
    reference = closing_terms(case, reference, standard)
    if proposed["effective_date"] >= reference["effective_date"]:
        return True
    # Include financing, coverage and the old-price period. An uncertain supplier
    # margin must not alone justify giving away an earlier price increase.
    return total_cost(case, proposed, standard) <= total_cost(case, reference, standard) + 0.001
