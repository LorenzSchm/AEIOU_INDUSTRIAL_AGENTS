"""Offline higher-capacity text+terms experiment. No live agent imports this module."""

import argparse
import hashlib
import json
import random
import re
import sys
import warnings
from pathlib import Path
from statistics import mean

from .response_learning import FEATURES, TARGETS, load_dataset
from .response_training import _weights, fit, metrics, predict, render_report, retrieve_prediction


def conversation_text(events, turn):
    """Input stops at the outgoing offer: no reply, result, future attachment or ID."""
    chunks = []
    for message in events[: turn + 1]:
        text = message.get("body", "")
        text = re.sub(r"sk-[\w-]+", " credential ", text)
        text = re.sub(r"[\w.+-]+@[\w.-]+", " email ", text)
        text = re.sub(r"(?:case|sup|part|offer|doc|idx|quote|ctr|sc):[\w@:.\-/]+", " reference ", text)
        text = re.sub(r"(?im)^\s*(?:dear|guten tag|hello|hallo)\b[^\n]*", " greeting ", text)
        text = re.split(r"(?i)kind regards|best regards|viele gr[uü]ße|mit freundlichen", text)[0]
        text = re.sub(r"\d+(?:[.,]\d+)*", " number ", text)
        role = "buyer" if message.get("from") == "buyer" else "supplier"
        chunks.append(role + " " + text)
    return "\n".join(chunks)


def add_text(rows, folder):
    runs = {}
    for path in Path(folder).glob("*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("session_id"):
            runs[run["session_id"]] = run
    for row in rows:
        events = runs[row["session_id"]]["threads"][row["supplier_id"]]
        row["text"] = conversation_text(events, row["turn"])
    return rows


def design(train, test):
    import numpy as np
    from scipy.sparse import csr_matrix, hstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import StandardScaler

    vectorizer = TfidfVectorizer(max_features=2048, ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    text_train = vectorizer.fit_transform(r["text"] for r in train)
    text_test = vectorizer.transform(r["text"] for r in test)
    scaler = StandardScaler().fit([r["x"] for r in train], sample_weight=_weights(train))
    x_train = hstack((text_train * 3, csr_matrix(scaler.transform([r["x"] for r in train])))).astype(np.float64).tocsr()
    x_test = hstack((text_test * 3, csr_matrix(scaler.transform([r["x"] for r in test])))).astype(np.float64).tocsr()
    return x_train, x_test, vectorizer, scaler


def fit_neural(train, test, epochs=60):
    import numpy as np
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.neural_network import MLPClassifier

    x_train, x_test, vectorizer, scaler = design(train, test)
    y = np.array([[r["y"][t] for t in TARGETS] for r in train])
    model = MLPClassifier(
        hidden_layer_sizes=(256, 64),
        activation="relu",
        alpha=1.0,
        batch_size=min(128, len(train)),
        learning_rate_init=0.001,
        max_iter=epochs,
        n_iter_no_change=epochs + 1,
        tol=0.0,
        early_stopping=False,
        random_state=741,
    )
    weights = np.array(_weights(train)) * len(train)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)  # fixed epoch budget, recorded below
        model.fit(x_train, y, sample_weight=weights)
    probabilities = model.predict_proba(x_test)
    assert probabilities.shape == (len(test), len(TARGETS))
    artifact = {
        "model": model,
        "vectorizer": vectorizer,
        "scaler": scaler,
        "features": FEATURES,
        "targets": TARGETS,
        "deployment": "offline_only",
    }
    info = {
        "parameters": sum(w.size for w in model.coefs_) + sum(b.size for b in model.intercepts_),
        "epochs": model.n_iter_,
        "training_loss": model.loss_,
        "vocabulary_size": len(vectorizer.vocabulary_),
    }
    return probabilities, artifact, info, x_train, x_test, weights


def assess(rows, output):
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    predictions, folds = [], []
    groups = sorted({r["group"] for r in rows})
    for group in groups:
        train, test = [r for r in rows if r["group"] != group], [r for r in rows if r["group"] == group]
        assert not {r["case_id"] for r in train} & {r["case_id"] for r in test}
        assert not {r["supplier_id"] for r in train} & {r["supplier_id"] for r in test}
        neural, _, info, x_train, x_test, weights = fit_neural(train, test)
        baseline = {t: fit(train, t) for t in TARGETS}
        text_models = {}
        for target in TARGETS:
            labels = np.array([r["y"][target] for r in train])
            text_models[target] = LogisticRegression(C=1, max_iter=500, random_state=741).fit(
                x_train, labels, sample_weight=weights
            )
        for i, row in enumerate(test):
            prediction = {k: v for k, v in row.items() if k != "text"}
            prediction["predictions"] = {}
            for j, target in enumerate(TARGETS):
                prediction["predictions"][target] = {
                    "learned": float(neural[i, j]),
                    "small_linear": predict(baseline[target], row["x"]),
                    "base_rate": baseline[target]["prior"],
                    "retrieval": retrieve_prediction(train, row, baseline[target]),
                    "text_linear": float(text_models[target].predict_proba(x_test[i])[0, 1]),
                }
            predictions.append(prediction)
        folds.append(
            {
                "held_out_group": group,
                "held_out_cases": sorted({r["case_id"] for r in test}),
                "train_examples": len(train),
                "test_examples": len(test),
                **info,
            }
        )
        print(json.dumps({"event": "fold_complete", **folds[-1]}), flush=True)
        (output / "progress.json").write_text(json.dumps(folds, indent=2), encoding="utf-8")
    results = {}
    for target in TARGETS:
        methods = {
            name: metrics(predictions, [r["predictions"][target][name] for r in predictions], target)
            for name in ("learned", "small_linear", "base_rate", "retrieval", "text_linear")
        }
        best = min((m for m in methods if m != "learned"), key=lambda m: methods[m]["macro_brier"])
        differences = [methods[best]["groups"][g]["brier"] - methods["learned"]["groups"][g]["brier"] for g in groups]
        rng = random.Random(741)
        boot = sorted(mean(rng.choices(differences, k=len(groups))) for _ in range(2000))
        positive_groups = sum(any(r["y"][target] for r in rows if r["group"] == g) for g in groups)
        relative = 1 - methods["learned"]["macro_brier"] / max(methods[best]["macro_brier"], 1e-9)
        passed = (
            len(groups) >= 8
            and positive_groups >= 5
            and relative >= 0.05
            and boot[50] > 0
            and methods["learned"]["macro_log_loss"] < methods[best]["macro_log_loss"]
        )
        results[target] = {
            "methods": methods,
            "best_baseline": best,
            "relative_brier_improvement": relative,
            "group_bootstrap_delta_interval": [boot[50], boot[1949]],
            "positive_examples": sum(r["y"][target] for r in rows),
            "passes_predictive_gate": passed,
        }
    return {"folds": folds, "targets": results}, predictions


def train_larger(source, output):
    import joblib
    import sklearn
    from threadpoolctl import threadpool_limits

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists():
        raise ValueError("Use a new output directory")
    rows, audit = load_dataset(source)
    audit["sessions_with_examples"] = len({r["session_id"] for r in rows})
    add_text(rows, source)
    text_sha = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    with threadpool_limits(limits=2):
        report, predictions = assess(rows, output)
        _, artifact, info, _, _, _ = fit_neural(rows, rows)
    joblib.dump(artifact, output / "model.joblib", compress=3)
    report.update(
        {
            "dataset": audit,
            "text_dataset_sha256": text_sha,
            "model": info,
            "sklearn_version": sklearn.__version__,
            "deployment": "offline_only",
            "arena_sessions_used": 0,
            "model_api_calls": 0,
            "findings": [
                f"{audit['hosted_sessions']} vorhandene Sessions, {len(rows)} Gesprächsschritte, "
                f"{len(report['folds'])} getrennte Challenge-/Lieferantengruppen.",
                f"Neuronales Netz: {info['parameters']:,} Parameter, 256/64 Neuronen, 60 Trainingsdurchläufe.",
                "Gesprächsverlauf und Angebotsmerkmale; unbekannte Challenges jeweils vollständig zurückgehalten.",
                "Kein Eingriff in den aktiven Agenten. Keine neuen Arena-Versuche und keine Trainings-API.",
                "Vorhersageprüfung bestanden: "
                + (
                    ", ".join(t for t, r in report["targets"].items() if r["passes_predictive_gate"])
                    or "keine Zielgröße"
                ),
            ],
            "limitations": [
                "Exploratory group validation after previous experiments; not a fresh prospective holdout.",
                "Full text is bag-of-unigrams/bigrams, not a pretrained language model or semantic transformer.",
                "One fixed random seed and architecture; no search over test scores.",
                "Vocabulary and scaling fit on training folds only; no future messages or labels in features.",
                "Same raw identities can occur in natural-language bodies; entire supplier groups remain disjoint.",
                "Prediction accuracy on logged actions does not establish causal policy improvement.",
                "Matched package is not final deal; direct cost excludes unknown relationship penalties.",
                "Only load the locally generated trusted model.joblib; pickle-based artifacts execute code.",
            ],
        }
    )
    for name, value in (("report.json", report), ("predictions.json", predictions), ("dataset.json", rows)):
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "model.json").write_text(
        json.dumps(
            {**info, "deployment": "offline_only", "artifact": "model.joblib", "text_dataset_sha256": text_sha},
            indent=2,
        ),
        encoding="utf-8",
    )
    render_report(report, output / "report.html")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dependency-root", help="Optional isolated directory installed with pip --target")
    args = parser.parse_args()
    if args.dependency_root:
        sys.path.insert(0, str(Path(args.dependency_root).resolve()))
    result = train_larger(args.runs, args.output)
    print(
        json.dumps(
            {
                "dataset": result["dataset"],
                "model": result["model"],
                "targets": {t: {k: v for k, v in r.items() if k != "methods"} for t, r in result["targets"].items()},
            },
            indent=2,
        )
    )
