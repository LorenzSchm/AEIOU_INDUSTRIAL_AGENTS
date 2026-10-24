"""Recover from repeated packages using observed counters, never hidden floors."""

import copy
import re

from arena_kit.cost import closing_terms
from arena_kit.play import latest

from .optimizer import valid_terms


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
    requested = bool(
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
    if not requested and not repeated:
        return packages
    quotes = [
        {f: full.get(f) for f in case["negotiable"]}
        for s in latest(events).get("term_sheets", [])
        for full in [closing_terms(case, s.get("terms", {}), standard)]
    ]
    quotes = [q for q in quotes if valid_terms(case, q)]
    if not quotes:
        return packages
    quote = min(quotes, key=cost)
    limit = min(ceiling, cost(quote) - 0.01)
    previous = [s["terms"] for m in buyers for s in m["term_sheets"]]

    def allowed(t):
        return (
            valid_terms(case, t)
            and cost(t) <= limit
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

    def insert(terms, label):
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

    comparable = [p for p in previous if same_structure(p, quote)]
    if len(buyers) >= 2 and comparable:
        last_price = max(p["unit_price"] for p in comparable)
        gap = quote["unit_price"] - last_price
        if gap > 0.02:
            terms = {**quote, "unit_price": round(last_price + max(0.01, gap * 0.25), 2)}
            if allowed(terms):
                insert(terms, "Reciprocal step on observed supplier terms")

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
