"""Use executable competition and retain our own bargaining position across turns."""

import copy

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left

from .economic_beliefs import total_cost
from .optimizer import estimate, evaluate, valid_terms
from .reciprocity import same_structure
from .scenario_planner import equivalent_package


def competitive_packages(case, threads, sid, packages, intelligence):
    if not packages:
        return packages
    if any(m.get("from") == "buyer" for m in threads[sid]):
        return continue_competitive_bid(case, threads, sid, packages, intelligence)
    rows = evaluate(case, threads, intelligence)
    rivals = [r for r in rows if r["supplier_id"] != sid and r["acceptable"]]
    own = [r for r in rows if r["supplier_id"] == sid]
    if not rivals or not own:
        return packages
    rival = min(rivals, key=lambda r: r["cost"])
    reference = min(r["cost"] for r in own)
    if reference <= 0 or rival["cost"] >= reference:
        return packages
    standard = standard_terms(threads[sid])
    supplier = estimate(case, sid, standard, intelligence)
    cost = lambda terms: total_cost(case, terms, standard, supplier)  # noqa: E731
    result = copy.deepcopy(packages)
    for package in result:
        # Preserve the original relative bargaining distance, but measure it from
        # the best valid rival's COMPLETE cost. Reprice this supplier's own terms;
        # never transfer a rival's quality/freight commitments to this supplier.
        target = min(package["cost"], rival["cost"] * min(1, package["cost"] / reference))
        # If the desired competition target lies outside the solver's bounded
        # neighborhood, take a bounded step instead of silently keeping the ask.
        bounded_target = max(target, cost({**package["terms"], "unit_price": package["terms"]["unit_price"] * 0.8}))
        terms = equivalent_package(case, package["terms"], package["terms"], cost, target_cost=bounded_target)
        if terms is not None and terms["unit_price"] < package["terms"]["unit_price"] and cost(terms) < package["cost"]:
            package.update(
                terms=terms,
                cost=cost(terms),
                competitive_anchor={
                    "supplier_id": rival["supplier_id"],
                    "offer_id": rival["sheet_id"],
                    "reference_cost": rival["cost"],
                    "target_cost": target,
                },
            )
    return result


def continue_competitive_bid(case, threads, sid, packages, intelligence):
    """Do not regenerate a large concession from the seller's expensive opening.

    Existing comparable offers are the lower bound. Move a fraction of the gap
    to a currently executable counter, translating its complete cost to our
    package's terms. Terminal offers and final two turns keep closing behavior.
    This only changes multi-supplier RFQs; no opponent floor is inferred.
    """
    events = threads[sid]
    remaining = moves_left(case, events)
    if len(threads) < 2 or latest(events).get("status") != "open" or remaining <= 2:
        return packages
    standard = standard_terms(events)
    supplier = estimate(case, sid, standard, intelligence)
    cost = lambda terms: total_cost(case, terms, standard, supplier)  # noqa: E731
    quotes = [
        {f: t.get(f) for f in case["negotiable"]}
        for sheet in latest(events).get("term_sheets", [])
        for t in [closing_terms(case, sheet.get("terms", {}), standard)]
    ]
    quotes = [q for q in quotes if valid_terms(case, q)]
    if not quotes:
        return packages
    quote = min(quotes, key=cost)
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    fraction = 0.35 if remaining <= 4 else 0.20
    result = copy.deepcopy(packages)
    for package in result:
        comparable = [p["unit_price"] for p in previous if same_structure(p, package["terms"])]
        if not comparable:
            continue
        prior = max(comparable)
        equivalent = equivalent_package(case, quote, package["terms"], cost, target_cost=cost(quote))
        if equivalent is None:
            continue
        cap = round(prior + max(0, equivalent["unit_price"] - prior) * fraction, 2)
        price = max(prior, min(package["terms"]["unit_price"], cap))
        terms = {**package["terms"], "unit_price": price}
        # Supplier sustainability costs can make a lower price more expensive.
        if terms != package["terms"] and valid_terms(case, terms) and cost(terms) <= package["cost"] + 0.001:
            package.update(
                terms=terms,
                cost=cost(terms),
                concession_progress={
                    "prior_price": prior,
                    "equivalent_counter_price": equivalent["unit_price"],
                    "gap_fraction": fraction,
                    "price_cap": cap,
                },
            )
    return result
