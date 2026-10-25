"""Stress uncertain supplier cost estimates without treating them as known floors."""

import math

from arena_kit.cost import total_cost


def weighted_quote_improvement(case, proposed, standing, standard, scenarios):
    """Weighted sensitivity comparison, not a calibrated acceptance probability."""
    rows = [
        {
            "weight": s["weight"],
            "quote_cost": total_cost(case, standing, standard, s),
            "proposal_cost": total_cost(case, proposed, standard, s),
        }
        for s in scenarios
    ]
    if not rows or not all(math.isfinite(r["proposal_cost"]) and r["weight"] > 0 for r in rows):
        return None
    change = sum(r["weight"] * (r["proposal_cost"] - r["quote_cost"]) for r in rows) / sum(r["weight"] for r in rows)
    return rows if change < -0.01 else None


def robust_quote_improvement(case, proposed, standing, standard, supplier, confidence_pct):
    """Compare like-for-like against a real quote across an estimate sensitivity band.

    Three stress points are not probabilities, a confidence interval, or a proof
    for all intermediate values. This permits exploration, never automatic acceptance.
    """
    if not supplier or not isinstance(confidence_pct, (int, float)):
        return None
    if not math.isfinite(confidence_pct) or not 0 < confidence_pct < 100:
        return None
    band = confidence_pct / 100
    rows = []
    for factor in (1 - band, 1.0, 1 + band):
        assumed = {**supplier, "full_cost": supplier["full_cost"] * factor}
        quote_cost = total_cost(case, standing, standard, assumed)
        proposal_cost = total_cost(case, proposed, standard, assumed)
        if not math.isfinite(proposal_cost) or proposal_cost > quote_cost - 0.01:
            return None
        rows.append({"cost_factor": factor, "quote_cost": quote_cost, "proposal_cost": proposal_cost})
    return rows
