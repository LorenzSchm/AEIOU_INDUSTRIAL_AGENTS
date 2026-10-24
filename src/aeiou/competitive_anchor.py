"""Anchor first contact to executable competition, not an expensive seller's ask."""

import copy

from arena_kit.docs import standard_terms

from .economic_beliefs import total_cost
from .optimizer import estimate, evaluate
from .scenario_planner import equivalent_package


def competitive_packages(case, threads, sid, packages, intelligence):
    if not packages or any(m.get("from") == "buyer" for m in threads[sid]):
        return packages
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
