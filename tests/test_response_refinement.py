import copy

import pytest

from aeiou.response_learning import FEATURES, TARGETS
from aeiou.response_refinement import (
    MODEL_NAMES,
    candidate_predictions,
    choose_from_inner,
    enrich_history,
    fit_candidates,
    group_splits,
    predict_selected,
    select_nested,
)


def examples():
    return [
        {
            "x": [float(i % 2)] + [0.0] * (len(FEATURES) - 1),
            "y": {t: i % 2 for t in TARGETS},
            "session_id": f"s{g}",
            "supplier_id": f"p{g}",
            "case_id": f"c{g}",
            "group": f"g{g}",
            "turn": i * 2 + 1,
        }
        for g in range(3)
        for i in range(4)
    ]


def test_history_uses_past_responses_but_never_current_or_future_labels():
    rows = examples()
    original = enrich_history(rows)
    changed = copy.deepcopy(rows)
    changed[2]["y"] = {t: 1 - changed[2]["y"][t] for t in TARGETS}
    changed[3]["y"] = {t: 1 - changed[3]["y"][t] for t in TARGETS}
    updated = enrich_history(changed)
    assert updated[2]["z"] == original[2]["z"]
    assert original[2]["history"] == {"count": 2, "direct_cost_improved": 1, "package_matched": 1}
    assert updated[3]["z"] != original[3]["z"]  # changed response is now in the past
    assert "z" not in rows[0]  # no mutation of the frozen original dataset


def test_feedback_does_not_cross_sessions_or_suppliers():
    rows = examples()
    enriched = enrich_history(rows)
    assert all(enriched[i]["history"]["count"] == 0 for i in (0, 4, 8))
    rows[1]["supplier_id"] = "different-supplier"
    assert enrich_history(rows)[1]["history"]["count"] == 0


def test_inner_splits_are_whole_groups_and_cover_every_validation_row_once():
    rows = examples()
    seen = []
    for train, test in group_splits(rows):
        assert not {rows[i]["group"] for i in train} & {rows[i]["group"] for i in test}
        seen.extend(test)
    assert sorted(seen) == list(range(len(rows)))


def test_selection_can_reject_every_complex_candidate():
    np = pytest.importorskip("numpy")
    rows = enrich_history(examples())
    for row in rows:
        row["y"]["package_matched"] = 0
    probabilities = {name: np.full(len(rows), 0.9) for name in MODEL_NAMES}
    probabilities["base_rate"] = np.full(len(rows), 0.01)
    selection = choose_from_inner(rows, "package_matched", probabilities)
    assert selection["model"] == "base_rate" and selection["strength"] == 0


def test_one_class_training_is_valid_and_never_creates_nan():
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    rows = enrich_history(examples())
    for row in rows:
        row["y"]["package_matched"] = 0
    artifact = fit_candidates(rows, "package_matched")
    assert not artifact["models"]
    assert all(np.isfinite(p).all() for p in candidate_predictions(artifact, rows).values())


def test_nested_selection_and_serialization_use_only_supplied_training_groups(tmp_path):
    pytest.importorskip("sklearn")
    import joblib
    import numpy as np
    from threadpoolctl import threadpool_limits

    rows = enrich_history(examples())
    with threadpool_limits(limits=2):
        selection, audit = select_nested(rows, "direct_cost_improved")
        artifact = fit_candidates(rows, "direct_cost_improved")
        predicted = predict_selected(artifact, selection, rows)
    assert all(not set(f["train_groups"]) & set(f["validation_groups"]) for f in audit)
    assert np.isfinite(predicted).all()
    path = tmp_path / "trusted-refinement.joblib"
    joblib.dump(artifact, path)
    np.testing.assert_allclose(predicted, predict_selected(joblib.load(path), selection, rows))


def test_constant_features_have_stable_weighted_normalization():
    pytest.importorskip("sklearn")
    import warnings

    import numpy as np
    from threadpoolctl import threadpool_limits

    rows = enrich_history(examples())
    for row in rows:
        row["z"][1] = 0.1
    with threadpool_limits(limits=2), warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        artifact = fit_candidates(rows, "direct_cost_improved")
    assert artifact["normalization"]["scale"][1] == 1
    assert np.isfinite(artifact["normalization"]["scale"]).all()
