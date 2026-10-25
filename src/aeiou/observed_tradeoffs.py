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
        target = round(quote["unit_price"] - max(0.01, quote["unit_price"] * 0.02), 2) if live else terms["unit_price"]
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


def delivery_response(case, events, packages, standard, cost, ceiling):
    """Counter on a cheaper quoted delivery package, not an inferred service premium.

    A supplier can bundle duration with delivery. Compare complete package costs
    rather than attributing the price difference to a single issue. Preserve A;
    any longer commitment must already be present in the controlled portfolio.
    """
    fields = set(case["negotiable"])
    if (
        case["buyer"].get("category_goal") != "secure_supply"
        or not {"unit_price", "lead_time_days", "contract_months", "volume_per_year"} <= fields
        or len(packages) < 2
        or latest(events).get("status") != "open"
        or not any(m.get("from") == "buyer" for m in events)
    ):
        return packages
    quotes = []
    for sheet in latest(events).get("term_sheets", []):
        full = closing_terms(case, sheet.get("terms", {}), standard)
        terms = {f: full.get(f) for f in case["negotiable"]}
        if valid_terms(case, terms) and terms["volume_per_year"] >= case["buyer"]["need_per_year"]:
            quotes.append(terms)
    need = case["buyer"]["need_lead_time_days"]
    timely = [q for q in quotes if q["lead_time_days"] <= need]
    late = [q for q in quotes if q["lead_time_days"] > need]
    if not timely or not late:
        return packages
    quote = min(late, key=cost)
    if cost(quote) >= min(cost(q) for q in timely) - 0.01:
        return packages
    slot = next((p for p in reversed(packages) if p["id"] != "A" and p["terms"]["lead_time_days"] > need), None)
    if slot is None:
        return packages
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    durations = sorted(
        {p["terms"]["contract_months"] for p in packages if p["terms"]["contract_months"] > quote["contract_months"]}
    )
    terms = {**quote, "contract_months": durations[0] if durations else quote["contract_months"]}
    terms["unit_price"] = max(
        round(quote["unit_price"] * 0.98, 2),
        max((p["unit_price"] for p in previous if same_structure(p, terms)), default=0),
    )
    if (
        not valid_terms(case, terms)
        or terms["contract_months"] < case["buyer"]["horizon_years"] * 12
        or cost(terms) >= min(ceiling, *(cost(q) for q in quotes)) - 0.01
    ):
        return packages
    result = copy.deepcopy(packages)
    selected = next(p for p in result if p["id"] == slot["id"])
    selected.update(
        terms=terms,
        cost=cost(terms),
        observed_tradeoff=True,
        label="Observed lower-cost delivery schedule with conditional commitment",
        reference_quote=quote,
        standing_cost=cost(quote),
        observed_tradeoff_evidence={"timely_quotes": timely, "lower_cost_late_quote": quote},
        issue="delivery_flex",
    )
    return result
