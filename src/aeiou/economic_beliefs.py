"""Public-evidence supplier economics; never consume outcomes or hidden floors.

Quoted prices are censored, strategic evidence, not revealed production costs.
The finite scenarios are modeling assumptions, not calibrated probabilities.
"""

import math

from arena_kit.cost import cost_breakdown as published_breakdown
from arena_kit.cost import supplier_value
from arena_kit.cost import total_cost as published_cost


def infer_economics(case, events, prior, standard):
    cost = prior.get("full_cost")
    band = prior.get("confidence_pct")
    if (
        type(cost) not in (int, float)
        or not math.isfinite(cost)
        or cost <= 0
        or type(band) not in (int, float)
        or not math.isfinite(band)
        or not 0 < band < 100
    ):
        return None
    band /= 100
    rows = []
    evidence = set()
    for factor, cost_weight in ((1 - band, 0.25), (1, 0.5), (1 + band, 0.25)):
        for rate, rate_weight in ((0.04, 0.2), (0.12, 0.6), (0.30, 0.2)):
            supplier = {"full_cost": cost * factor, "interest_rate": rate, "standard": standard}
            quotes = []
            for index, message in enumerate(events):
                if message.get("from") == "buyer":
                    continue
                for sheet in message.get("term_sheets", []):
                    terms = sheet.get("terms", {})
                    if type(terms.get("unit_price")) not in (int, float) or terms["unit_price"] <= 0:
                        continue
                    try:
                        worth = supplier_value(case, terms, supplier)
                    except (KeyError, TypeError, ValueError, ZeroDivisionError):
                        continue
                    if math.isfinite(worth) and worth > 0:
                        quotes.append((worth, index))
            # Use the tightest bound once. Repeated/rephrased quotes cannot multiply
            # the evidence, and a loss-leading quote retains nonzero likelihood.
            likelihood = 1.0
            if quotes:
                worth, index = min(quotes)
                evidence.add(index)
                z = max(-30, min(30, (worth - cost * factor) / (cost * band * 0.5)))
                likelihood = 0.2 + 0.8 / (1 + math.exp(-z))
            rows.append({**supplier, "weight": cost_weight * rate_weight * likelihood})
    weight = sum(row["weight"] for row in rows)
    for row in rows:
        row["weight"] /= weight
    return {
        "scenarios": rows,
        "full_cost": sum(r["full_cost"] * r["weight"] for r in rows),
        "interest_rate": sum(r["interest_rate"] * r["weight"] for r in rows),
        "prior_source": prior.get("source"),
        "prior_band_pct": band * 100,
        "evidence_message_indices": sorted(evidence),
        "limitation": "Tempered quote likelihood; uncertainty scenarios are not calibrated supplier costs.",
    }


def cost_breakdown(case, terms, standard=None, supplier=None):
    scenarios = supplier.get("scenarios") if supplier else None
    if not scenarios:
        return published_breakdown(case, terms, standard, supplier)
    # Average costs AFTER evaluating the nonlinear relationship penalty. A cost
    # evaluated at average supplier economics generally gives a different ranking.
    result = {}
    for scenario in scenarios:
        for key, value in published_breakdown(case, terms, standard, scenario).items():
            result[key] = result.get(key, 0) + scenario["weight"] * value
    return result


def total_cost(case, terms, standard=None, supplier=None):
    if not supplier or not supplier.get("scenarios"):
        return published_cost(case, terms, standard, supplier)
    return round(sum(cost_breakdown(case, terms, standard, supplier).values()), 2)


def cost_range(case, terms, standard, supplier):
    scenarios = supplier.get("scenarios") if supplier else None
    costs = [published_cost(case, terms, standard, s) for s in (scenarios or [supplier])]
    return {"low": min(costs), "high": max(costs), "modeled": total_cost(case, terms, standard, supplier)}
