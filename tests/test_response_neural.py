import copy

import pytest

from aeiou.response_learning import FEATURES, TARGETS
from aeiou.response_neural import conversation_text, design, fit_neural


def rows():
    return [
        {
            "text": "common supplier quotation good" if i % 2 else "common supplier quotation difficult",
            "x": [float(i % 2)] + [0.0] * (len(FEATURES) - 1),
            "y": {target: i % 2 for target in TARGETS},
            "session_id": f"session-{i}",
            "group": f"group-{i // 8}",
        }
        for i in range(24)
    ]


def test_text_input_ends_at_outgoing_message():
    events = [
        {"from": "supplier", "body": "Our proposal is pending."},
        {"from": "buyer", "body": "We propose payment in thirty days."},
        {"from": "supplier", "body": "FUTURE ACCEPTANCE AND SECRET REWARD"},
    ]
    text = conversation_text(events, 1)
    modified = copy.deepcopy(events)
    modified[2]["body"] = "FUTURE REJECTION"
    assert conversation_text(modified, 1) == text
    assert "FUTURE" not in text and "thirty days" in text


def test_identifiers_emails_credentials_and_numeric_prices_removed_from_text():
    text = conversation_text(
        [{"from": "buyer", "body": "Contact john@example.org case:P01 sup:70110 for EUR 134.50. sk-ant-secret-value"}],
        0,
    )
    assert "john@example.org" not in text and "P01" not in text and "70110" not in text
    assert "134.50" not in text and "sk-ant" not in text


def test_vocabulary_and_scaling_fit_training_data_only():
    pytest.importorskip("sklearn")
    train = rows()
    test = [dict(train[0], text="heldoutonlyword", x=[99.0] * len(FEATURES))]
    _, _, vectorizer, scaler = design(train, test)
    assert "heldoutonlyword" not in vectorizer.vocabulary_
    assert scaler.mean_[0] == pytest.approx(0.5)
    assert scaler.mean_[1] == pytest.approx(0.0)


def test_neural_multioutput_shape_and_trusted_artifact_roundtrip(tmp_path):
    pytest.importorskip("sklearn")
    import joblib
    import numpy as np
    from threadpoolctl import threadpool_limits

    train = rows()
    with threadpool_limits(limits=2):
        probabilities, artifact, info, _, test_matrix, _ = fit_neural(train, train[:3], epochs=2)
    assert probabilities.shape == (3, 2)
    assert np.isfinite(probabilities).all()
    assert ((probabilities >= 0) & (probabilities <= 1)).all()
    assert info["parameters"] > 16000
    path = tmp_path / "trusted-generated-model.joblib"
    joblib.dump(artifact, path)
    restored = joblib.load(path)
    assert restored["deployment"] == "offline_only"
    np.testing.assert_allclose(restored["model"].predict_proba(test_matrix), probabilities)
