"""Optional accepted-deal cost estimate, conditional on historical continuation policies."""

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms

from .custom_model import predict_one
from .custom_model_data import live_features
from .tactical_features import tactic_features


def forecast_conditional_cost(artifact, case, supplier_id, prefix, proposal):
    head = artifact.get("conditional_final_cost")
    if not head or not head.get("eligible") or head["case_id"] != case["case_id"]:
        return {"status": "unavailable", "reason": "no_qualified_conditional_model"}
    row = live_features(case, supplier_id, prefix, proposal)
    row["v"] += tactic_features(prefix, proposal)
    if len(row["v"]) != len(head["feature_min"]) or any(
        value < lo - 1e-6 or value > hi + 1e-6
        for value, lo, hi in zip(row["v"], head["feature_min"], head["feature_max"], strict=True)
    ):
        return {"status": "abstained", "reason": "outside_observed_accepted_deal_features"}
    row["past_targets"]["terminal_gain"] = {"sum": 0, "count": 0}
    gain = predict_one(head["model"], row)
    first = next(e for e in prefix if e.get("from") not in {"buyer", "arena"} and e.get("term_sheets"))
    opening = total_cost(case, first["term_sheets"][0]["terms"], standard_terms([first]))
    return {
        "status": "conditional_advice",
        "expected_final_cost_given_agreement": round(opening * (1 - gain), 2),
        "validation_mae_eur": round(opening * head["validation_mae"], 2),
        "meaning": (
            "Historical estimate conditional on agreement and continuation policy. Not acceptance probability, "
            "not a binding quote, not causal evidence, and not transferable to an edited proposal. "
            "Validation error is descriptive, not a confidence interval."
        ),
    }
