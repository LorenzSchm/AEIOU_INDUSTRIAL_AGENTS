"""Small Bayesian ensemble of supplier hypotheses, using public package choices.

The weights describe assumptions about commercial preferences. They are not a
calibrated probability of acceptance, and no simulator floor or persona is read.
"""

import math
from statistics import median

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms

HYPOTHESES = (
    {"name": "balanced", "finance": 0.08, "duration": 0.0, "freight": 0.5},
    {"name": "liquidity", "finance": 0.40, "duration": 0.0, "freight": 0.5},
    {"name": "continuity", "finance": 0.08, "duration": 0.025, "freight": 0.5},
    {"name": "logistics", "finance": 0.08, "duration": 0.0, "freight": 1.0},
    {"name": "cash_and_continuity", "finance": 0.40, "duration": 0.025, "freight": 0.5},
    {"name": "price_focused", "finance": 0.0, "duration": 0.0, "freight": 0.0},
)

# Preference hypotheses include competing signs for risk/capacity. They are
# structural priors, not supplier personas assigned by challenge identity.
ISSUE_HYPOTHESES = tuple(
    {"name": name, "finance": 0.08, "duration": 0.0, "freight": 0.5, **weights}
    for name, weights in (
        ("quality_cost", {"ppm_commitment": 0.08, "chargeback_eur_per_complaint": -0.06}),
        ("quality_capable", {"ppm_commitment": 0.0, "chargeback_eur_per_complaint": 0.0}),
        ("index_security", {"index_clause": 0.06}),
        ("fixed_security", {"index_clause": -0.06}),
        ("delivery_constrained", {"lead_time_days": 0.08}),
        ("spare_capacity", {"volume_per_year": 0.06}),
        ("capacity_constrained", {"volume_per_year": -0.06}),
        ("cash_quality", {"finance": 0.4, "ppm_commitment": 0.08, "chargeback_eur_per_complaint": -0.06}),
    )
)


def issue_value(case, terms, field):
    """Dimensionless, legal-domain normalization avoids part-specific scales."""
    value = terms.get(field)
    options = case.get("term_options", {}).get(field, [])
    if field == "index_clause":
        if not isinstance(value, dict):
            return 0.0
        return float(value.get("material_share", 0)) / (1 + float(value.get("deadband_pct", 0)) / 10)
    numeric = [v for v in options if type(v) in (int, float)]
    if type(value) not in (int, float) or len(numeric) < 2 or max(numeric) == min(numeric):
        return 0.0
    return (value - min(numeric)) / (max(numeric) - min(numeric))


def supplier_utility(case, terms, hypothesis, standard=None):
    t = closing_terms(case, terms, standard)
    price = max(float(t["unit_price"]), 0.01)
    reference = case["buyer"]["reference_payment_days"]
    freight = case["buyer"]["freight_eur_per_unit"].get(t["incoterm"], 0)
    return (
        math.log(price)
        - hypothesis["finance"] * (t["payment_days"] - reference) / 360
        + hypothesis["duration"] * min(t["contract_months"], 36) / 12
        + hypothesis["freight"] * freight / price
        + sum(
            hypothesis.get(f, 0) * issue_value(case, t, f)
            for f in (
                "ppm_commitment",
                "chargeback_eur_per_complaint",
                "index_clause",
                "lead_time_days",
                "volume_per_year",
            )
        )
    )


def learn_beliefs(case, events, enabled=True, multi_issue=False):
    hypotheses = HYPOTHESES + ISSUE_HYPOTHESES if multi_issue else HYPOTHESES
    logs, observations, trace = [0.0] * len(hypotheses), 0, []
    seen = set()
    pending = None
    for index, message in enumerate(events):
        if message.get("from") == "buyer":
            pending = message
            continue
        if not enabled or pending is None:
            continue
        packages = [s.get("terms", {}) for s in pending.get("term_sheets", [])]
        pending = None
        replies = message.get("term_sheets", [])
        if len(packages) < 2 or len(replies) != 1 or message.get("status") not in {"open", "final_offer", "agreed"}:
            continue
        std = standard_terms(events[: index + 1])
        complete = [closing_terms(case, p, std) for p in packages]
        reply = closing_terms(case, replies[0].get("terms", {}), std)
        fields = [f for f in case["negotiable"] if f != "unit_price"]
        varied = {f for f in fields if len({repr(p.get(f)) for p in complete}) > 1}
        # This model knows cash, duration and freight only. Quality, volume,
        # index and delivery trades cannot identify its preference hypotheses.
        supported = {"payment_days", "contract_months", "incoterm"}
        if multi_issue:
            supported |= {
                "ppm_commitment",
                "chargeback_eur_per_complaint",
                "index_clause",
                "lead_time_days",
                "volume_per_year",
            }
        if not varied or varied - supported:
            continue
        matches = [i for i, p in enumerate(complete) if all(p.get(f) == reply.get(f) for f in fields)]
        if len(matches) != 1:
            continue
        selected = matches[0]
        # Repeating the identical package experiment is correlated evidence.
        signature = repr((complete, selected))
        if signature in seen:
            continue
        seen.add(signature)
        for i, hypothesis in enumerate(hypotheses):
            utilities = [supplier_utility(case, p, hypothesis, std) for p in complete]
            high = max(utilities)
            likelihood = math.exp((utilities[selected] - high) * 30) / sum(math.exp((u - high) * 30) for u in utilities)
            # Counters may copy package terms strategically; temper their evidence.
            logs[i] += (1.0 if message.get("status") == "agreed" else 0.35) * math.log(max(likelihood, 1e-9))
        observations += 1
        item = {"message_index": index, "offer_id": replies[0].get("term_sheet_id")}
        if multi_issue:
            item.update(joint_dimensions=sorted(varied), selected_package=selected)
        trace.append(item)
    high = max(logs)
    weights = [math.exp(v - high) for v in logs]
    total = sum(weights)
    posterior = [{**h, "weight": w / total} for h, w in zip(hypotheses, weights, strict=True)]
    return {
        "hypotheses": posterior,
        "observations": observations,
        "evidence": trace[-12:],
        "entropy": -sum(h["weight"] * math.log(h["weight"]) for h in posterior),
        "observed_price_step": comparable_price_step(case, events),
        "limitation": "Assumption-based posterior over package preferences, not calibrated acceptance probabilities.",
    }


def comparable_price_step(case, events):
    """Estimate concessions only between consecutive quotes on identical terms."""
    changes, previous = [], None
    fields = [f for f in case["negotiable"] if f != "unit_price"]
    for index, message in enumerate(events):
        if message.get("from") == "buyer":
            continue
        sheets = message.get("term_sheets", [])
        if len(sheets) != 1 or not sheets[0].get("terms", {}).get("unit_price"):
            previous = None
            continue
        terms = closing_terms(case, sheets[0]["terms"], standard_terms(events[: index + 1]))
        if previous and all(previous.get(f) == terms.get(f) for f in fields):
            step = previous["unit_price"] - terms["unit_price"]
            if step > 0:
                changes.append(step)
        previous = terms
    return median(changes[-3:]) if changes else 0


def predicted_choices(case, packages, belief, standard=None):
    scores = [0.0] * len(packages)
    for hypothesis in belief["hypotheses"]:
        values = [supplier_utility(case, p["terms"], hypothesis, standard) for p in packages]
        high = max(values, default=0)
        weights = [math.exp((v - high) * 30) for v in values]
        for i, value in enumerate(weights):
            scores[i] += hypothesis["weight"] * value / sum(weights)
    return scores


def equivalent_response_price(case, proposed, current, hypothesis, standard=None):
    """Solve a hypothetical counter on its OWN terms, not the old quote's terms.

    Finite bounds deliberately prevent extreme extrapolation. This is a scenario,
    not an executable quote or a prediction of the supplier's private floor.
    """
    target = supplier_utility(case, current, hypothesis, standard)
    lo, hi = current["unit_price"] * 0.7, current["unit_price"] * 1.3
    for _ in range(22):
        mid = (lo + hi) / 2
        if supplier_utility(case, {**proposed, "unit_price": mid}, hypothesis, standard) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
