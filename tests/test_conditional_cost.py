import copy

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms
from test_custom_model import recorded

from aeiou.conditional_cost import forecast_conditional_cost
from aeiou.custom_model_data import live_features
from aeiou.tactical_features import tactic_features


def fixture():
    _, case, seller, buyer, _ = recorded()
    prefix = [seller]
    vector = live_features(case, "sup:A", prefix, buyer)["v"] + tactic_features(prefix, buyer)
    head = {
        "case_id": case["case_id"],
        "eligible": True,
        "feature_min": vector,
        "feature_max": vector,
        "validation_mae": 0.02,
        "model": {"target": "terminal_gain", "prior": 0.1, "selection": {"method": "prior", "strength": 0}},
    }
    return case, prefix, buyer, {"conditional_final_cost": head}


def test_conditional_estimate_uses_actual_opening_and_preserves_inputs():
    case, prefix, proposal, artifact = fixture()
    before = copy.deepcopy((prefix, proposal))
    result = forecast_conditional_cost(artifact, case, "sup:A", prefix, proposal)
    opening = total_cost(case, prefix[0]["term_sheets"][0]["terms"], standard_terms(prefix))
    assert result["expected_final_cost_given_agreement"] == round(opening * 0.9, 2)
    assert result["validation_mae_eur"] == round(opening * 0.02, 2)
    assert "Not acceptance probability" in result["meaning"]
    assert (prefix, proposal) == before


def test_conditional_head_withholds_unsupported_case_unqualified_model_and_edited_terms():
    case, prefix, proposal, artifact = fixture()
    assert forecast_conditional_cost({}, case, "sup:A", prefix, proposal)["status"] == "unavailable"
    wrong_case = {**case, "case_id": "case:other"}
    assert forecast_conditional_cost(artifact, wrong_case, "sup:A", prefix, proposal)["status"] == "unavailable"
    artifact["conditional_final_cost"]["eligible"] = False
    assert forecast_conditional_cost(artifact, case, "sup:A", prefix, proposal)["status"] == "unavailable"
    artifact["conditional_final_cost"]["eligible"] = True
    proposal["term_sheets"][0]["terms"]["unit_price"] *= 2
    assert forecast_conditional_cost(artifact, case, "sup:A", prefix, proposal)["status"] == "abstained"
