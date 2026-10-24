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
    )


def learn_beliefs(case, events, enabled=True):
    logs, observations, trace = [0.0] * len(HYPOTHESES), 0, []
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
        if not varied or varied - {"payment_days", "contract_months", "incoterm"}:
            continue
        matches = [i for i, p in enumerate(complete) if all(p.get(f) == reply.get(f) for f in fields)]
        if len(matches) != 1:
            continue
        selected = matches[0]
        for i, hypothesis in enumerate(HYPOTHESES):
            utilities = [supplier_utility(case, p, hypothesis, std) for p in complete]
            high = max(utilities)
            likelihood = math.exp((utilities[selected] - high) * 30) / sum(math.exp((u - high) * 30) for u in utilities)
            # Counters may copy package terms strategically; temper their evidence.
            logs[i] += (1.0 if message.get("status") == "agreed" else 0.35) * math.log(max(likelihood, 1e-9))
        observations += 1
        trace.append({"message_index": index, "offer_id": replies[0].get("term_sheet_id")})
    high = max(logs)
    weights = [math.exp(v - high) for v in logs]
    total = sum(weights)
    posterior = [{**h, "weight": w / total} for h, w in zip(HYPOTHESES, weights, strict=True)]
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
    return {
        "hypotheses": posterior,
        "observations": observations,
        "evidence": trace[-12:],
        "entropy": -sum(h["weight"] * math.log(h["weight"]) for h in posterior),
        "observed_price_step": median(changes[-3:]) if changes else 0,
        "limitation": "Assumption-based posterior over package preferences, not calibrated acceptance probabilities.",
    }


def predicted_choices(case, packages, belief, standard=None):
    scores = [0.0] * len(packages)
    for hypothesis in belief["hypotheses"]:
        values = [supplier_utility(case, p["terms"], hypothesis, standard) for p in packages]
        high = max(values, default=0)
        weights = [math.exp((v - high) * 30) for v in values]
        for i, value in enumerate(weights):
            scores[i] += hypothesis["weight"] * value / sum(weights)
    return scores
