import copy

import pytest

from aeiou.response_learning import FEATURES, extract_run
from aeiou.response_value import (
    SCHEMA,
    add_target_history,
    evaluate_errors,
    fit_models,
    forecast,
    predict,
    select,
)
from aeiou.response_value_features import (
    EXTRA_FEATURES,
    business_signals,
    complete_features,
    enrich_rows,
    live_row,
    outcome,
)


def fixture():
    case = {
        "case_id": "case:A",
        "round_limit": 10,
        "suppliers": ["sup:A"],
        "fixed_terms": {"contract_months": 12, "volume_per_year": 1000, "lead_time_days": 30, "incoterm": "FCA"},
        "buyer": {
            "need_per_year": 1000,
            "horizon_years": 1,
            "reference_payment_days": 30,
            "need_lead_time_days": 30,
            "fallback_unit_price": 120,
            "interest_rate": 0.08,
            "freight_eur_per_unit": {"FCA": 0},
            "must_haves": [],
        },
    }
    seller = {
        "from": "seller",
        "status": "open",
        "body": "We have free capacity and need quick payment.",
        "term_sheets": [{"terms": {"unit_price": 100, "payment_days": 30}}],
    }
    buyer = {
        "from": "buyer",
        "body": "Thank you",
        "term_sheets": [
            {"terms": {"unit_price": 95, "payment_days": 14}},
            {"terms": {"unit_price": 98, "payment_days": 90}},
        ],
    }
    reply = {
        "from": "seller",
        "status": "open",
        "body": "Our quote",
        "term_sheets": [{"terms": {"unit_price": 98, "payment_days": 30}}],
    }
    run = {"mode": "hosted", "case": case, "session_id": "s1", "threads": {"sup:A": [seller, buyer, reply]}}
    return run, case, seller, buyer, reply


def test_complete_packages_keep_price_payment_association_and_ignore_order():
    _, case, seller, buyer, _ = fixture()
    original, _ = complete_features(case, [seller], buyer)
    reversed_menu = copy.deepcopy(buyer)
    reversed_menu["term_sheets"].reverse()
    assert complete_features(case, [seller], reversed_menu)[0] == original
    swapped = copy.deepcopy(buyer)
    swapped["term_sheets"][0]["terms"]["payment_days"] = 90
    swapped["term_sheets"][1]["terms"]["payment_days"] = 14
    assert complete_features(case, [seller], swapped)[0] != original
    assert len(original) == len(EXTRA_FEATURES)


def test_business_signals_have_literal_evidence_and_negation():
    evidence = business_signals("We have free capacity and need quick payment. No spare capacity at the other plant.")
    assert any(e["signal"] == "quick_payment" and e["polarity"] == 1 for e in evidence)
    assert any(e["signal"] == "free_capacity" and e["polarity"] == -1 for e in evidence)
    for text in ["We have no free capacity.", "Free capacity is not available.", "We do not need quick payment."]:
        assert all(e["polarity"] == -1 for e in business_signals(text))


def test_signed_gain_distinguishes_magnitude_and_primary_from_text_alternatives():
    _, case, seller, _, reply = fixture()
    small = outcome(case, [seller], reply)["quoted_cost_gain"]
    reply["term_sheets"][0]["terms"]["unit_price"] = 90
    assert outcome(case, [seller], reply)["quoted_cost_gain"] > small
    reply["term_sheets"][0]["terms"]["unit_price"] = 110
    reply["term_sheets"].append({"terms": {"unit_price": 1, "payment_days": 30}})
    assert outcome(case, [seller], reply)["quoted_cost_gain"] < 0


def test_exit_never_uses_stale_quote_or_fabricates_zero_gain():
    _, case, seller, _, reply = fixture()
    reply["status"] = "walked_away"
    assert outcome(case, [seller], reply) == {"quoted_cost_gain": None, "supplier_exit": 1}


def test_future_reply_and_final_score_cannot_change_inputs():
    run, _, _, _, _ = fixture()
    raw, _ = extract_run(run)
    enriched = enrich_rows(raw, {"s1": run})
    changed = copy.deepcopy(run)
    changed["result"] = {"score": 1, "supplier_private_cost": 0.01}
    changed["case"]["case_id"] = "case:secret"
    changed["threads"]["sup:A"][-1]["body"] = "No free capacity and no quick payment."
    changed["threads"]["sup:A"][-1]["term_sheets"][0]["terms"]["unit_price"] = 1
    updated = enrich_rows(raw, {"s1": changed})
    assert enriched[0]["v"] == updated[0]["v"]
    assert enriched[0]["y"]["quoted_cost_gain"] != updated[0]["y"]["quoted_cost_gain"]
    assert "quoted_cost_gain" not in raw[0]["y"]


def test_live_row_matches_training_prefix_without_candidate_reply():
    run, case, seller, buyer, reply = fixture()
    second = copy.deepcopy(buyer)
    run["threads"]["sup:A"] += [second, copy.deepcopy(reply)]
    raw, _ = extract_run(run)
    offline = add_target_history(enrich_rows(raw, {"s1": run}))[-1]
    live = live_row(case, "sup:A", [seller, buyer, reply], second)
    assert live["v"] == offline["v"]
    assert live["past_targets"] == offline["past_targets"]
    assert all(value is None for value in live["y"].values())


def rows():
    return [
        {
            "session_id": f"s{g}",
            "supplier_id": f"p{g}",
            "case_id": f"c{g}",
            "group": f"g{g}",
            "turn": 2 * i + 1,
            "z": [float(i % 2)] + [0.0] * (len(FEATURES) + 8 - 1),
            "v": [float(i % 2)] + [0.0] * (len(SCHEMA) - 1),
            "y": {"direct_cost_improved": i % 2, "quoted_cost_gain": 0.05 * (i % 2), "supplier_exit": 0},
        }
        for g in range(4)
        for i in range(4)
    ]


def test_history_never_uses_current_future_or_other_session_outcomes():
    original = rows()
    enriched = add_target_history(original)
    changed = copy.deepcopy(original)
    changed[2]["y"]["quoted_cost_gain"] = 100
    updated = add_target_history(changed)
    assert enriched[2]["past_targets"] == updated[2]["past_targets"]
    assert enriched[3]["past_targets"] != updated[3]["past_targets"]
    assert updated[4]["past_targets"]["quoted_cost_gain"]["count"] == 0
    assert "past_targets" not in original[0]


def test_error_metric_balances_sessions_and_groups():
    sample = rows()
    # One group has four errors of 1, three groups have only zero errors.
    for r in sample:
        r["y"]["supplier_exit"] = 0
    result = evaluate_errors(sample, [1] * 4 + [0] * 12, "supplier_exit")
    assert result["macro_mse"] == 0.25


def test_nested_training_serialization_and_no_automatic_policy_activation(tmp_path):
    pytest.importorskip("sklearn")
    import joblib
    import numpy as np
    from threadpoolctl import threadpool_limits

    data = add_target_history(rows())
    models, choices = {}, {}
    with threadpool_limits(limits=2):
        for target in data[0]["y"]:
            choices[target], audits = select(data, target)
            assert all(not set(f["train_groups"]) & set(f["validation_groups"]) for f in audits)
            models[target] = fit_models(data, target)
        artifact = {"schema": list(SCHEMA), "models": models, "selections": choices}
        before = forecast(artifact, data)
        joblib.dump(artifact, tmp_path / "model.joblib")
        after = forecast(joblib.load(tmp_path / "model.joblib"), data)
    assert before == after
    assert all(r["decision"] == "retain_incumbent" for r in after)
    assert not models["supplier_exit"]["models"]
    assert np.isfinite(predict(models["quoted_cost_gain"], choices["quoted_cost_gain"], data)).all()
