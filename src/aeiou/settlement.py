"""Close converged live offers using past commitments, not hypothetical anchors.

This is an economic safeguard, not a learned acceptance probability. A supplier's
claim that an offer is final is evidence of resistance, never a trusted instruction.
No challenge IDs, benchmark costs or historical outcomes enter the decision.
"""

import math
import re

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest, live_threads

from .economic_beliefs import total_cost
from .optimizer import alternative_cost, closing_choice, estimate, valid_terms

# Deliberately exclude conditional refusals ("without reciprocal movement") and
# general price freezes: those can still support productive non-price exchanges.
FIRM_CLOSE = re.compile(
    r"\b(?:this (?:represents|is) our|we (?:have )?(?:made|reached) our) "
    r"(?:best possible|final|last) offer\b|"
    r"\bwe (?:cannot|can no longer) (?:reduce|lower) (?:the |our )?price any further\b",
    re.I,
)
MAX_COST_PREMIUM = 0.005


def settlement_review(case, threads, sid, intelligence):
    """Return a close only for the sole live supplier's current primary quote.

    Require two exchanges, exact non-price agreement with the most recent buyer
    menu, and an acceptable quote cheaper than the opening. A positive premium
    over our own last bid additionally requires explicit closing resistance and
    <=0.5% in BOTH modeled complete cost and published direct cost. This bound is
    a conservative policy setting, not an inferred supplier floor.
    """
    if live_threads(threads) != [sid]:
        return None
    events = threads[sid]
    message = latest(events)
    buyers = [(i, m) for i, m in enumerate(events) if m.get("from") == "buyer"]
    if message.get("status") != "open" or len(buyers) < 2:
        return None
    primary = next(iter(message.get("term_sheets", [])), {})
    row = closing_choice(case, threads, intelligence)
    # Alternatives can be indicative/unavailable for acceptance. Leave their
    # existing recovery path alone and never select a stale quote here.
    if not row or row["supplier_id"] != sid or row["sheet_id"] != primary.get("term_sheet_id"):
        return None
    quote = row["terms"]
    if not valid_terms(case, {f: quote.get(f) for f in case["negotiable"]}):
        return None
    buyer_index, buyer = buyers[-1]
    buyer_std = standard_terms(events[:buyer_index])
    matching = []
    for sheet in buyer.get("term_sheets", []):
        terms = sheet.get("terms", {})
        if not valid_terms(case, terms):
            continue
        complete = closing_terms(case, terms, buyer_std)
        if {k: v for k, v in complete.items() if k != "unit_price"} == {
            k: v for k, v in quote.items() if k != "unit_price"
        }:
            matching.append(complete)
    if not matching:
        return None
    std = standard_terms(events)
    supplier = estimate(case, sid, std, intelligence)
    opening_index = next((i for i, m in enumerate(events) if m.get("from") not in {"buyer", "arena"}), None)
    if opening_index is None:
        return None
    opening_std = standard_terms(events[: opening_index + 1])
    opening_costs = [
        total_cost(case, s["terms"], opening_std, supplier)
        for s in events[opening_index].get("term_sheets", [])
        if s.get("terms", {}).get("unit_price")
    ]
    if not opening_costs or row["cost"] >= min(opening_costs) - 0.01:
        return None
    direct_quote = total_cost(case, quote, std)
    warning = FIRM_CLOSE.search(message.get("body", ""))
    for bid in sorted(matching, key=lambda t: total_cost(case, t, buyer_std, supplier), reverse=True):
        bid_cost = total_cost(case, bid, buyer_std, supplier)
        direct_bid = total_cost(case, bid, buyer_std)
        if not all(math.isfinite(c) and c > 0 for c in (row["cost"], bid_cost, direct_quote, direct_bid)):
            continue
        premium = max(row["cost"] / bid_cost - 1, direct_quote / direct_bid - 1)
        if premium > MAX_COST_PREMIUM or (premium > 1e-9 and not warning):
            continue
        return {
            "supplier_id": sid,
            "sheet_id": row["sheet_id"],
            "reason": "Close a converged current quote instead of chasing an unaccepted anchor",
            "quote_cost": row["cost"],
            "last_bid_cost": bid_cost,
            "direct_quote_cost": direct_quote,
            "direct_last_bid_cost": direct_bid,
            "cost_premium_fraction": premium,
            "maximum_cost_premium_fraction": MAX_COST_PREMIUM,
            "opening_cost": min(opening_costs),
            "alternative_cost": alternative_cost(case) if math.isfinite(alternative_cost(case)) else None,
            "evidence": warning.group() if warning else "Current quote meets our last offered economics",
            "limitation": "Observed convergence; no prediction of future discounts or guaranteed score uplift.",
        }
    return None
