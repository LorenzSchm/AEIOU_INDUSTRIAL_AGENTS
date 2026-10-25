"""Nested group validation of conservative response models with prefix feedback.

All selection occurs inside each outer training fold. Prior response labels are
allowed only after that response was already visible in the conversation prefix.
This is a diagnostic experiment on previously inspected challenges, not a fresh
prospective policy evaluation. No active agent imports this module.
"""

import argparse
import copy
import hashlib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from .response_learning import FEATURES, TARGETS
from .response_training import _weights, metrics, render_report

HISTORY_FEATURES = (
    "past_responses",
    "past_progress_rate",
    "last_progress",
    "last_two_progress",
    "stagnation_streak",
    "past_match_rate",
    "last_match",
    "no_prior_feedback",
)
MODEL_NAMES = ("base_rate", "history", "ridge", "boosted", "forest")


def enrich_history(rows):
    """Replay observable prefix outcomes; never consult the current/future label."""
    output = copy.deepcopy(rows)
    threads = defaultdict(list)
    for row in output:
        threads[(row["session_id"], row["supplier_id"])].append(row)
    for events in threads.values():
        events.sort(key=lambda r: r["turn"])
        for i, row in enumerate(events):
            past = [r for r in events[:i] if r["turn"] + 1 < row["turn"]]
            progress = [r["y"]["direct_cost_improved"] for r in past]
            matches = [r["y"]["package_matched"] for r in past]
            streak = 0
            for label in reversed(progress):
                if label:
                    break
                streak += 1
            row["history"] = {
                "count": len(past),
                "direct_cost_improved": sum(progress),
                "package_matched": sum(matches),
            }
            row["z"] = row["x"] + [
                min(len(past) / 10, 1),
                (sum(progress) + 1) / (len(past) + 2),
                progress[-1] if progress else 0.5,
                mean(progress[-2:]) if progress else 0.5,
                min(streak / 5, 1),
                (sum(matches) + 1) / (len(past) + 2),
                matches[-1] if matches else 0.5,
                float(not past),
            ]
    return output


def group_splits(rows, folds=3):
    """Deterministic whole-group splits; balance number of groups, not labels."""
    groups = sorted({r["group"] for r in rows}, key=lambda g: hashlib.sha256(g.encode()).hexdigest())
    if len(groups) < 3:
        raise ValueError("Need at least three groups for inner selection")
    for index in range(folds):
        held_out = set(groups[index::folds])
        train = [i for i, r in enumerate(rows) if r["group"] not in held_out]
        test = [i for i, r in enumerate(rows) if r["group"] in held_out]
        yield train, test


def fit_candidates(rows, target):
    import numpy as np
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression

    weights = np.array(_weights(rows)) * len(rows)
    labels = np.array([r["y"][target] for r in rows])
    prior = float(np.clip(np.average(labels, weights=weights), 0.01, 0.99))
    artifact = {"prior": prior, "target": target, "models": {}, "schema": list(FEATURES + HISTORY_FEATURES)}
    if len(set(labels)) < 2:
        return artifact
    x = np.array([r["z"] for r in rows])
    # Explicit centered variance avoids negative roundoff for constant features
    # in the library's incremental weighted-scaler calculation.
    center = np.average(x, axis=0, weights=weights)
    variance = np.average((x - center) ** 2, axis=0, weights=weights)
    scale = np.sqrt(variance)
    scale[scale < 1e-12] = 1.0
    artifact["normalization"] = {"center": center, "scale": scale}
    ridge = LogisticRegression(C=0.1, max_iter=500, random_state=741)
    ridge.fit((x - center) / scale, labels, sample_weight=weights)
    boosted = HistGradientBoostingClassifier(
        max_iter=100,
        max_depth=2,
        max_leaf_nodes=4,
        learning_rate=0.04,
        min_samples_leaf=20,
        l2_regularization=10,
        early_stopping=False,
        random_state=741,
    )
    boosted.fit(x, labels, sample_weight=weights)
    forest = ExtraTreesClassifier(
        n_estimators=128, max_depth=5, min_samples_leaf=12, max_features=0.8, n_jobs=2, random_state=741
    )
    forest.fit(x, labels, sample_weight=weights)
    artifact["models"] = {"ridge": ridge, "boosted": boosted, "forest": forest}
    return artifact


def candidate_predictions(artifact, rows):
    import numpy as np

    prior, target = artifact["prior"], artifact["target"]
    probabilities = {
        "base_rate": np.full(len(rows), prior),
        "history": np.array([(r["history"][target] + 2 * prior) / (r["history"]["count"] + 2) for r in rows]),
    }
    for name in ("ridge", "boosted", "forest"):
        model = artifact["models"].get(name)
        if model is None:
            probabilities[name] = probabilities["base_rate"].copy()
            continue
        x = np.array([r["z"] for r in rows])
        if name == "ridge" and "normalization" in artifact:
            normalization = artifact["normalization"]
            x = (x - normalization["center"]) / normalization["scale"]
        probabilities[name] = model.predict_proba(x)[:, 1]
    return probabilities


def choose_from_inner(rows, target, predictions):
    """Choose estimator and probability shrinkage solely from inner out-of-group predictions."""
    import numpy as np

    weights = np.array(_weights(rows))
    labels = np.array([r["y"][target] for r in rows])
    candidates = []
    for name in MODEL_NAMES:
        for strength in (0.0,) if name == "base_rate" else (0.25, 0.5, 0.75, 1.0):
            p = strength * predictions[name] + (1 - strength) * predictions["base_rate"]
            error = float(np.average((p - labels) ** 2, weights=weights))
            candidates.append({"model": name, "strength": strength, "inner_brier": error})
    # Ties prefer a smaller departure from the prior, then the fixed model ordering.
    return min(candidates, key=lambda r: (r["inner_brier"], r["strength"], MODEL_NAMES.index(r["model"])))


def select_nested(train, target):
    import numpy as np

    predictions = {name: np.full(len(train), np.nan) for name in MODEL_NAMES}
    splits = []
    for train_indices, validation_indices in group_splits(train):
        fitting = [train[i] for i in train_indices]
        validation = [train[i] for i in validation_indices]
        assert not {r["group"] for r in fitting} & {r["group"] for r in validation}
        artifact = fit_candidates(fitting, target)
        probabilities = candidate_predictions(artifact, validation)
        for name in MODEL_NAMES:
            predictions[name][validation_indices] = probabilities[name]
        splits.append(
            {
                "train_groups": sorted({r["group"] for r in fitting}),
                "validation_groups": sorted({r["group"] for r in validation}),
            }
        )
    assert all(np.isfinite(p).all() for p in predictions.values())
    return choose_from_inner(train, target, predictions), splits


def predict_selected(artifact, selection, rows):
    p = candidate_predictions(artifact, rows)
    return selection["strength"] * p[selection["model"]] + (1 - selection["strength"]) * p["base_rate"]


def reliability(rows, probabilities, target):
    """Descriptive reliability bins, not an additional training signal."""
    weights = _weights(rows)
    bins = []
    for lo, hi in ((0, 0.05), (0.05, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)):
        ids = [i for i, p in enumerate(probabilities) if lo <= p < hi]
        if not ids:
            continue
        total = sum(weights[i] for i in ids)
        bins.append(
            {
                "range": [lo, min(hi, 1)],
                "examples": len(ids),
                "mean_prediction": sum(probabilities[i] * weights[i] for i in ids) / total,
                "observed_frequency": sum(rows[i]["y"][target] * weights[i] for i in ids) / total,
            }
        )
    return bins


def experiment(dataset_path, previous_predictions_path, output):
    import joblib
    from threadpoolctl import threadpool_limits

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists():
        raise ValueError("Use a new output directory")
    raw = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    rows = enrich_history(raw)
    previous = json.loads(Path(previous_predictions_path).read_text(encoding="utf-8"))

    def key(r):
        return r["session_id"], r["supplier_id"], r["turn"]

    old = {key(r): r for r in previous}
    assert set(old) == {key(r) for r in rows}, "Comparison must use exactly the same examples"
    assert all(old[key(r)]["x"] == r["x"] and old[key(r)]["y"] == r["y"] for r in rows)
    groups = sorted({r["group"] for r in rows})
    results, folds = [], []
    with threadpool_limits(limits=2):
        for group in groups:
            train, test = [r for r in rows if r["group"] != group], [r for r in rows if r["group"] == group]
            assert not {r["case_id"] for r in train} & {r["case_id"] for r in test}
            assert not {r["supplier_id"] for r in train} & {r["supplier_id"] for r in test}
            selections, artifacts, audits = {}, {}, {}
            for target in TARGETS:
                selections[target], audits[target] = select_nested(train, target)
                artifacts[target] = fit_candidates(train, target)
            new = {t: predict_selected(artifacts[t], selections[t], test) for t in TARGETS}
            history = {t: candidate_predictions(artifacts[t], test)["history"] for t in TARGETS}
            for i, row in enumerate(test):
                entry = {k: v for k, v in row.items() if k != "text"}
                entry["predictions"] = {}
                for target in TARGETS:
                    entry["predictions"][target] = {
                        "learned": float(new[target][i]),
                        "history": float(history[target][i]),
                        "previous_neural": old[key(row)]["predictions"][target]["learned"],
                        **{
                            m: old[key(row)]["predictions"][target][m]
                            for m in ("small_linear", "base_rate", "retrieval")
                        },
                    }
                results.append(entry)
            fold = {
                "held_out_group": group,
                "selections": selections,
                "inner_splits": audits,
                "train_examples": len(train),
                "test_examples": len(test),
            }
            folds.append(fold)
            (output / "progress.json").write_text(json.dumps(folds, indent=2), encoding="utf-8")
            print(json.dumps({"event": "outer_fold_complete", "group": group, "selections": selections}), flush=True)
        final_selections = {t: select_nested(rows, t)[0] for t in TARGETS}
        final_artifacts = {t: fit_candidates(rows, t) for t in TARGETS}
    targets = {}
    for target in TARGETS:
        methods = {
            m: metrics(results, [r["predictions"][target][m] for r in results], target)
            for m in ("learned", "history", "previous_neural", "small_linear", "base_rate", "retrieval")
        }
        best = min(("history", "small_linear", "base_rate", "retrieval"), key=lambda m: methods[m]["macro_brier"])
        diff = [methods[best]["groups"][g]["brier"] - methods["learned"]["groups"][g]["brier"] for g in groups]
        rng = random.Random(741)
        boot = sorted(mean(rng.choices(diff, k=len(groups))) for _ in range(2000))
        improvement = 1 - methods["learned"]["macro_brier"] / methods[best]["macro_brier"]
        positive_groups = sum(any(r["y"][target] for r in rows if r["group"] == g) for g in groups)
        targets[target] = {
            "methods": methods,
            "best_baseline": best,
            "relative_brier_improvement": improvement,
            "relative_improvement_over_previous_neural": 1
            - methods["learned"]["macro_brier"] / methods["previous_neural"]["macro_brier"],
            "group_bootstrap_delta_interval": [boot[50], boot[1949]],
            "passes_predictive_gate": bool(
                len(groups) >= 8
                and positive_groups >= 5
                and improvement >= 0.05
                and boot[50] > 0
                and methods["learned"]["macro_log_loss"] < methods[best]["macro_log_loss"]
            ),
            "reliability": reliability(results, [r["predictions"][target]["learned"] for r in results], target),
        }
    report = {
        "targets": targets,
        "folds": folds,
        "deployment": "offline_only",
        "arena_sessions_used": 0,
        "model_api_calls": 0,
        "final_selections": final_selections,
        "dataset_sha256": hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest(),
        "findings": [
            f"{len(rows)} identische Beispiele; {len(groups)} äußere und jeweils 3 innere Gruppenprüfungen.",
            "Zusätzliche Merkmale verwenden ausschließlich bereits beobachtete Lieferantenreaktionen.",
            "Modellauswahl und Rücknahme extremer Wahrscheinlichkeiten ausschließlich in inneren Trainingsprüfungen.",
            "Vorhersageprüfung bestanden: "
            + (", ".join(t for t, r in targets.items() if r["passes_predictive_gate"]) or "keine Zielgröße"),
            "Diagnostische Auswertung auf bereits untersuchten Challenges; kein Nachweis eines höheren Arena-Scores.",
        ],
        "limitations": [
            "No fresh prospective holdout: feature design followed earlier error inspection.",
            "Nested grouping prevents outer test labels from selecting candidates or shrinkage strength.",
            "Prior outcomes from the current conversation are online-observable features, not hidden labels.",
            "Only logged menu responses are predicted; counterfactual offer effectiveness is unmeasured.",
        ],
    }
    report["findings"].extend(
        f"{target}: {result['relative_improvement_over_previous_neural'] * 100:.1f}% weniger Brier-Fehler "
        "gegenüber dem bisherigen neuronalen Modell."
        for target, result in targets.items()
    )
    artifact = {"models": final_artifacts, "selections": final_selections, "deployment": "offline_only"}
    joblib.dump(artifact, output / "model.joblib", compress=3)
    for name, value in (
        ("report.json", report),
        ("predictions.json", results),
        ("model.json", {"selections": final_selections, "deployment": "offline_only", "artifact": "model.joblib"}),
    ):
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    render_report(report, output / "report.html")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--previous-predictions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dependency-root")
    args = parser.parse_args()
    if args.dependency_root:
        sys.path.insert(0, str(Path(args.dependency_root).resolve()))
    report = experiment(args.dataset, args.previous_predictions, args.output)
    print(
        json.dumps(
            {
                t: {k: v for k, v in r.items() if k not in {"methods", "reliability"}}
                for t, r in report["targets"].items()
            },
            indent=2,
        )
    )
