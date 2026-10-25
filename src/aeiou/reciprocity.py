"""Recover from repeated packages using observed counters, never hidden floors."""

import copy
import math
import re

from arena_kit.cost import closing_terms, total_cost
from arena_kit.play import latest

from .optimizer import valid_terms
from .supplier_signals import PRICE_FREEZE


def same_structure(a, b):
    return {k: v for k, v in a.items() if k != "unit_price"} == {k: v for k, v in b.items() if k != "unit_price"}


def responsive_packages(case, events, packages, standard, cost, ceiling):
    """Keep an anchor, but reserve a slot for an actionable observed-quote trade.

    After repeated counters, move a quarter of the remaining comparable price
    gap. Also try a single adjacent non-price improvement at the quoted price.
    These are requests, not predicted agreements. Both must beat the current
    executable quote and the external buyer ceiling. No opening-price chasing.
    """
    buyers = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
    if not buyers or latest(events).get("status") != "open" or not packages:
        return packages
    requested = bool(re.search(PRICE_FREEZE, latest(events).get("body", ""), re.I)) or bool(
        re.search(
            r"reciprocal (?:step|move)|(?:prices?|packages?).{0,25}(?:unchanged|not changed)|"
            r"best (?:possible )?pric|firm position|real jump|revised offer|"
            r"(?:cannot|can't) authorize any further|base pricing.{0,30}freez",
            latest(events).get("body", ""),
            re.I,
        )
    )
    repeated = len(buyers) >= 2 and [s["terms"] for s in buyers[-1]["term_sheets"]] == [
        s["terms"] for s in buyers[-2]["term_sheets"]
    ]
    quotes = [
        {f: full.get(f) for f in case["negotiable"]}
        for s in latest(events).get("term_sheets", [])
        for full in [closing_terms(case, s.get("terms", {}), standard)]
    ]
    quotes = [q for q in quotes if valid_terms(case, q)]
    if not quotes:
        return packages
    previous = [s["terms"] for m in buyers for s in m["term_sheets"]]
    # Once a supplier counters on our indexed structure, respond on that same
    # structure instead of waiting for two identical menus or an explicit impasse.
    # Preserve the fixed-price anchor and other categories' existing policy.
    indexed_quotes = [
        q
        for q in quotes
        if case["buyer"].get("category_goal") == "leverage"
        and "index_clause" in case["negotiable"]
        and packages[0]["terms"].get("index_clause") is None
        and q.get("index_clause") is not None
        and any(same_structure(p, q) for p in previous)
        and any(same_structure(p["terms"], q) for p in packages[1:])
    ]
    date_menu = (
        "effective_date" in case["negotiable"]
        and len(buyers) >= 2
        and len({q.get("effective_date") for q in quotes}) > 1
    )
    if not requested and not repeated and not date_menu and not indexed_quotes:
        return packages
    quote = min(quotes, key=cost)
    limit = min(ceiling, cost(quote) - 0.01)

    def allowed(t, cost_limit=None):
        return (
            valid_terms(case, t)
            and cost(t) <= (limit if cost_limit is None else cost_limit)
            and all(t["unit_price"] >= p["unit_price"] for p in previous if same_structure(p, t))
            and all(
                t.get(f, need) >= need
                for f, need in (
                    ("volume_per_year", case["buyer"]["need_per_year"]),
                    (
                        "contract_months",
                        min(
                            max(
                                p["terms"].get("contract_months", 12 * case["buyer"]["horizon_years"]) for p in packages
                            ),
                            12 * case["buyer"]["horizon_years"],
                        ),
                    ),
                )
            )
        )

    result = copy.deepcopy(packages)

    def insert(terms, label, **metadata):
        # Replace the old price on the SAME structure, including a stale A.
        # Never send two prices for an otherwise identical contract.
        match = next((i for i, p in enumerate(result) if same_structure(p["terms"], terms)), None)
        entry = {
            "id": result[match]["id"] if match is not None else f"R{len(result)}",
            "terms": terms,
            "cost": cost(terms),
            "label": label,
            "responsive_probe": True,
            "standing_cost": cost(quote),
            **metadata,
        }
        if match is not None:
            result[match] = entry
        elif len(result) < 3:
            result.append(entry)
        else:
            # Preserve A and an already-added reciprocal counter.
            slot = next((i for i in range(len(result) - 1, 0, -1) if not result[i].get("responsive_probe")), None)
            if slot is not None:
                entry["id"] = result[slot]["id"]
                result[slot] = entry

    counters = sorted(quotes, key=cost) if date_menu else [min(indexed_quotes, key=cost)] if indexed_quotes else [quote]
    for counter in counters:
        comparable = [p for p in previous if same_structure(p, counter)]
        # Late in a productive indexed negotiation, make one credible closing
        # proposal instead of another distant anchor. Infer the requested final
        # discount from the seller's last like-for-like concession, not a stored
        # challenge score or an assumed supplier floor.
        if counter in indexed_quotes and len(buyers) >= 2 and comparable:
            earlier = next(
                (m for m in reversed(events[:-1]) if m.get("from") != "buyer" and m.get("term_sheets")),
                {},
            )
            peers = [
                {f: full.get(f) for f in case["negotiable"]}
                for s in earlier.get("term_sheets", [])
                for full in [closing_terms(case, s.get("terms", {}), standard)]
            ]
            peers = [q for q in peers if valid_terms(case, q) and same_structure(q, counter)]
            if peers:
                seller_step = min(q["unit_price"] for q in peers) - counter["unit_price"]
                price = math.floor((counter["unit_price"] - max(0.01, seller_step / 2)) * 100 + 1e-8) / 100
                terms = {**counter, "unit_price": price}
                current = max(
                    (p["terms"]["unit_price"] for p in packages if same_structure(p["terms"], terms)),
                    default=max(p["unit_price"] for p in comparable),
                )
                if seller_step > 0.02 and price > current and allowed(terms):
                    insert(
                        terms,
                        "Conditional settlement after observed indexed concessions",
                        index_settlement=True,
                        seller_step=round(seller_step, 6),
                    )
                    continue
        if (len(buyers) >= 2 or counter in indexed_quotes) and comparable:
            last_price = max(p["unit_price"] for p in comparable)
            gap = counter["unit_price"] - last_price
            if gap > 0.02:
                terms = {**counter, "unit_price": round(last_price + max(0.01, gap * 0.25), 2)}
                # Never replace today's planned bid with a smaller concession.
                current = max(
                    (p["terms"]["unit_price"] for p in packages if same_structure(p["terms"], terms)),
                    default=last_price,
                )
                if allowed(terms) and (counter not in indexed_quotes or terms["unit_price"] > current):
                    insert(
                        terms,
                        "Reciprocal step on observed supplier terms",
                        **({"index_reciprocity": True} if counter in indexed_quotes else {}),
                    )

    # With a declared price restriction, start from the longest payment option
    # already quoted on each identical contract. Restarting from the cheap cash
    # option merely asks for a discount on terms the supplier has already priced.
    # This is a bounded probe, not evidence of acceptance or a true financing rate.
    if "payment_days" in case["negotiable"] and re.search(PRICE_FREEZE, latest(events).get("body", ""), re.I):
        frontier = []
        probe_limit = min(ceiling, cost(quote) * 1.0025)
        for counter in quotes:
            peers = [q for q in quotes if all(q[f] == counter[f] for f in q if f not in {"unit_price", "payment_days"})]
            if counter["payment_days"] < max(q["payment_days"] for q in peers):
                continue
            days = sorted(
                d
                for d in case.get("term_options", {}).get("payment_days", [])
                if type(d) in (int, float) and d > counter["payment_days"]
            )
            if not days:
                continue
            terms = {**counter, "payment_days": days[0]}
            if (
                terms in previous
                or not allowed(terms, probe_limit)
                or total_cost(case, terms, standard) >= min(total_cost(case, q, standard) for q in quotes) - 0.01
            ):
                continue
            frontier.append((total_cost(case, terms, standard), terms, counter))
        if frontier:
            _, terms, counter = min(frontier, key=lambda item: item[0])
            insert(
                terms,
                "Extend the supplier's quoted payment frontier",
                payment_frontier_probe=True,
                reference_quote=counter,
                point_cost_limit=probe_limit,
            )
            # Reserve this slot for learning a new term price; do not overwrite it
            # with another request starting from the already-priced cash option.
            return result

    # Ask for the nearest beneficial change, rather than insisting on a large
    # price cut AND every favorable term at once. Prefer untouched dimensions.
    marginal = []
    for field in case["negotiable"]:
        if field == "unit_price":
            continue
        options = case.get("term_options", {}).get(field, [])
        if isinstance(quote.get(field), (int, float)):
            options = sorted(options, key=lambda v: abs(v - quote[field]))
        for value in options:
            terms = {**quote, field: value}
            if value == quote.get(field) or terms in previous or not allowed(terms):
                continue
            if any(same_structure(p["terms"], terms) for p in result):
                continue
            marginal.append((cost(terms), field, terms))
            break
    # Quoting near the seller too early destroys price discovery. Only make this
    # larger concession on an explicit impasse, not simply because a quote exists.
    if marginal and requested:
        _, field, terms = min(marginal, key=lambda item: item[0])
        insert(terms, f"Observed price with one improved {field}")
    return result
