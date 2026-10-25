"""Retain a useful liability trade only after observing comparable supplier prices."""

import copy
from itertools import combinations

from arena_kit.cost import closing_terms
from arena_kit.play import latest

from .optimizer import valid_terms
from .reciprocity import same_structure


def liability_response(case, events, packages, standard, cost, ceiling):
    field = "chargeback_eur_per_complaint"
    buyers = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
    if field not in case["negotiable"] or len(buyers) < 2 or not packages:
        return packages
    if latest(events).get("status") != "open":
        return packages

    quotes, evidence, live_quotes = [], [], []
    for index, message in enumerate(events):
        if message.get("from") == "buyer":
            continue
        current = []
        for sheet in message.get("term_sheets", []):
            full = closing_terms(case, sheet.get("terms", {}), standard)
            terms = {f: full.get(f) for f in case["negotiable"]}
            if valid_terms(case, terms):
                current.append(terms)
                quotes.append(terms)
        if index == len(events) - 1:
            live_quotes = current
        # Same-message alternatives isolate the issue better than quotes from
        # different rounds. Never interpret a multi-issue change as a fee effect.
        for a, b in combinations(current, 2):
            lo, hi = sorted((a, b), key=lambda t: t[field])
            if lo[field] >= hi[field] or lo["unit_price"] >= hi["unit_price"]:
                continue
            if any(lo[f] != hi[f] for f in case["negotiable"] if f not in {field, "unit_price"}):
                continue
            if cost(lo) + 0.01 < cost(hi):
                evidence.append({"turn": index, "lower": lo, "higher": hi})
    if not evidence:
        return packages

    previous = [s["terms"] for m in buyers for s in m["term_sheets"]]
    candidates = []
    for package in packages:
        terms = package["terms"]
        if package["id"] == "A":
            continue  # preserve the original buyer-value anchor
        related = [
            e
            for e in evidence
            if terms[field] <= e["lower"][field]
            and all(terms[f] == e["lower"][f] for f in case["negotiable"] if f not in {field, "unit_price"})
        ]
        comparable = [q for q in quotes if same_structure(q, terms)]
        prior = [p for p in previous if same_structure(p, terms)]
        if not related or not comparable or not prior:
            continue
        quote = min(comparable, key=cost)
        # Ask for a small further improvement on an actually quoted structure.
        # Historical quotes are evidence, not currently executable acceptances.
        live = [q for q in live_quotes if same_structure(q, terms)]
        # Retain the discovered option, but do not bid up toward a stale quote.
        # Only a fresh counter on this structure permits the settlement step.
        target = round(quote["unit_price"] - max(0.01, quote["unit_price"] * 0.01), 2) if live else terms["unit_price"]
        bid = {**terms, "unit_price": max(target, terms["unit_price"], max(p["unit_price"] for p in prior))}
        if not valid_terms(case, bid) or cost(bid) >= min(ceiling, cost(quote)) - 0.01:
            continue
        candidates.append((cost(quote), package, bid, related[-1], quote))
    if not candidates:
        return packages
    _, selected, bid, observed, quote = min(candidates, key=lambda row: row[0])
    result = copy.deepcopy(packages)
    for package in result:
        if package["id"] == selected["id"]:
            package.update(
                terms=bid,
                cost=cost(bid),
                observed_tradeoff=True,
                label="Counter on the supplier's lower-cost liability terms",
                observed_tradeoff_evidence=observed,
                reference_quote=quote,
                standing_cost=cost(quote),
            )
    return result
