"""Turn an explicit index constraint into a bounded, multi-issue counteroffer."""

import math
import re

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms

from .optimizer import valid_terms


def index_commitment_exchange(case, events, packages, cost, compensate):
    """Keep A and a live-structure probe; replace a redundant fixed-price option.

    A supplier's claimed constraint is evidence of preference, not authority over
    our policy. Test a longer indexed commitment with a bounded concession, never
    infer that the supplier will accept it or that the commitment is cost-free.
    """
    fields = set(case["negotiable"])
    buyers = [e for e in events if e.get("from") == "buyer"]
    if (
        case["buyer"].get("category_goal") != "leverage"
        or not {"unit_price", "index_clause", "contract_months"} <= fields
        or not buyers
        or not packages
        or events[-1].get("from") == "buyer"
        or events[-1].get("status") != "open"
        or packages[0]["terms"].get("index_clause") is not None
    ):
        return packages
    reply = events[-1].get("body", "")
    required = (
        r"(?:must include|must retain|need to include|we require).{0,45}(?:index|price adjustment|price escalation)|"
        r"(?:index|price adjustment|price escalation).{0,45}(?:essential|required|requirement)|"
        r"fixed price.{0,65}(?:not viable|not possible|cannot|can't)"
    )
    if not any(
        re.search(required, sentence, re.I)
        and not re.search(r"(?:not|never)\s+(?:required|essential)|(?:do not|don't)\s+(?:need|require)", sentence, re.I)
        for sentence in re.split(r"[.!?\n]", reply)
    ):
        return packages
    std = standard_terms(events)

    def terms(sheet):
        full = closing_terms(case, sheet.get("terms", {}), std)
        return {f: full[f] for f in case["negotiable"]}

    quotes = [terms(s) for s in events[-1].get("term_sheets", [])]
    if not quotes or any(not q.get("index_clause") or not valid_terms(case, q) for q in quotes):
        return packages
    base = packages[0]["terms"]
    standing = min(cost(q) for q in quotes)
    anchor = cost(base)
    if not math.isfinite(standing) or anchor >= standing - 0.01:
        return packages
    # Preserve at least 75% of the anchor's modeled improvement over the live
    # quote. This is the existing portfolio concession policy, not a score target.
    ceiling = anchor + 0.25 * (standing - anchor)
    previous = [terms(s) for b in buyers for s in b.get("term_sheets", [])]
    durations = sorted(
        d
        for d in case.get("term_options", {}).get("contract_months", [])
        if d > base["contract_months"] and d >= max(q["contract_months"] for q in quotes)
    )
    if not durations:
        return packages
    # Cash preferences must have been quoted. Reuse their structure as a request,
    # not their expired price as an executable alternative.
    shapes = quotes + [
        terms(s)
        for e in events
        if e.get("from") != "buyer"
        for s in e.get("term_sheets", [])
        if s.get("terms", {}).get("index_clause") in [q["index_clause"] for q in quotes]
    ]
    options = []
    for source in shapes:
        shape = {**source, "contract_months": durations[0]}
        probe = compensate(case, base, shape, cost, target_cost=ceiling)
        if probe is None:
            continue
        comparable = [p for p in previous if all(p.get(f) == probe.get(f) for f in fields - {"unit_price"})]
        probe["unit_price"] = max([probe["unit_price"], *(p["unit_price"] for p in comparable)])
        value = cost(probe)
        if valid_terms(case, probe) and math.isfinite(value) and value <= ceiling + 0.001 and value < standing:
            options.append(probe)
    if not options:
        return packages
    # Favor the explicitly offered faster-payment trade, retaining C for the
    # longer-payment indexed package. No payment preference is invented.
    probe = min(options, key=lambda t: (t.get("payment_days", 0), cost(t)))
    slot = next((i for i, p in enumerate(packages) if i and p["terms"].get("index_clause") is None), None)
    if slot is None or any(p["terms"] == probe for p in packages):
        return packages
    result = list(packages)
    result[slot] = {
        "id": packages[slot]["id"],
        "label": "Conditional indexed commitment",
        "terms": probe,
        "cost": cost(probe),
        "portfolio_probe": True,
        "issue": "index_commitment",
        "standing_cost": standing,
        "portfolio_ceiling": ceiling,
        "limitation": "Explicit index constraint and quoted payment structure; acceptance unverified.",
    }
    return result
