"""Train small response predictors locally; grouped evaluation never enables live bidding.

Fixed regularization and epochs are chosen before evaluation. Leave-one-connected-
case/supplier-group-out folds test unseen groups. Re-running/tuning on this report
turns it into development evidence, not a fresh holdout.
"""

import argparse
import html
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .response_learning import FEATURES, TARGETS, load_dataset


def _weights(rows):
    session_size = Counter(r["session_id"] for r in rows)
    sessions = defaultdict(set)
    for row in rows:
        sessions[row["group"]].add(row["session_id"])
    raw = [1 / (session_size[r["session_id"]] * len(sessions[r["group"]])) for r in rows]
    return [w / sum(raw) for w in raw]


def _sigmoid(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def fit(rows, target, epochs=250):
    if not rows:
        raise ValueError("Training requires examples")
    weights = _weights(rows)
    center = [sum(w * r["x"][j] for w, r in zip(weights, rows, strict=True)) for j in range(len(FEATURES))]
    scale = [
        max(0.01, math.sqrt(sum(w * (r["x"][j] - center[j]) ** 2 for w, r in zip(weights, rows, strict=True))))
        for j in range(len(FEATURES))
    ]
    x = [[1.0] + [(v - c) / s for v, c, s in zip(r["x"], center, scale, strict=True)] for r in rows]
    y = [r["y"][target] for r in rows]
    prior = max(0.01, min(0.99, sum(w * value for w, value in zip(weights, y, strict=True))))
    beta = [math.log(prior / (1 - prior))] + [0.0] * len(FEATURES)
    for _ in range(epochs):
        gradient = [0.0] + [0.03 * b for b in beta[1:]]
        for vector, label, weight in zip(x, y, weights, strict=True):
            error = (_sigmoid(sum(b * v for b, v in zip(beta, vector, strict=True))) - label) * weight
            for j, value in enumerate(vector):
                gradient[j] += error * value
        beta = [b - 0.15 * g for b, g in zip(beta, gradient, strict=True)]
    return {
        "features": list(FEATURES),
        "target": target,
        "center": center,
        "scale": scale,
        "coefficients": beta,
        "prior": prior,
        "regularization": 0.03,
        "epochs": epochs,
    }


def predict(model, vector):
    if model["features"] != list(FEATURES) or len(vector) != len(FEATURES):
        raise ValueError("Model/feature schema mismatch")
    z = [(v - c) / s for v, c, s in zip(vector, model["center"], model["scale"], strict=True)]
    return _sigmoid(model["coefficients"][0] + sum(b * v for b, v in zip(model["coefficients"][1:], z, strict=True)))


def retrieve_prediction(train, row, model, neighbors=15):
    """Nearest historical states; at most one neighbor from each training session."""
    distances = []
    for candidate in train:
        distance = sum(((a - b) / s) ** 2 for a, b, s in zip(row["x"], candidate["x"], model["scale"], strict=True))
        distances.append((distance, candidate["session_id"], candidate))
    chosen, sessions = [], set()
    for _, sid, candidate in sorted(distances, key=lambda item: (item[0], item[1])):
        if sid not in sessions:
            chosen.append(candidate["y"][model["target"]])
            sessions.add(sid)
        if len(chosen) == neighbors:
            break
    return (sum(chosen) + 2 * model["prior"]) / (len(chosen) + 2)


def rule_prediction(row, target):
    f = dict(zip(FEATURES, row["x"], strict=True))
    if target == "package_matched":
        return 0.6 if f["max_price_gap"] >= -0.005 and f["min_payment_delta"] <= 0 else 0.08
    return 0.7 if f["min_price_gap"] < 0 and not f["supplier_closing_warning"] else 0.2


def metrics(rows, probabilities, target):
    groups = defaultdict(lambda: defaultdict(list))
    for row, p in zip(rows, probabilities, strict=True):
        y = row["y"][target]
        p = min(1 - 1e-6, max(1e-6, p))
        groups[row["group"]][row["session_id"]].append(((p - y) ** 2, -y * math.log(p) - (1 - y) * math.log(1 - p)))
    group_metrics = {}
    for group, sessions in groups.items():
        group_metrics[group] = {
            "brier": mean(mean(v[0] for v in values) for values in sessions.values()),
            "log_loss": mean(mean(v[1] for v in values) for values in sessions.values()),
        }
    return {
        "macro_brier": mean(v["brier"] for v in group_metrics.values()),
        "macro_log_loss": mean(v["log_loss"] for v in group_metrics.values()),
        "groups": group_metrics,
    }


def evaluate(rows):
    groups = sorted({r["group"] for r in rows})
    if len(groups) < 3:
        raise ValueError("At least three disjoint case/supplier groups are required")
    predictions, folds = [], []
    for group in groups:
        train = [r for r in rows if r["group"] != group]
        test = [r for r in rows if r["group"] == group]
        assert not {r["case_id"] for r in train} & {r["case_id"] for r in test}
        assert not {r["supplier_id"] for r in train} & {r["supplier_id"] for r in test}
        assert not {r["session_id"] for r in train} & {r["session_id"] for r in test}
        models = {target: fit(train, target) for target in TARGETS}
        folds.append(
            {
                "held_out_group": group,
                "held_out_cases": sorted({r["case_id"] for r in test}),
                "train_examples": len(train),
                "test_examples": len(test),
            }
        )
        for row in test:
            entry = {**row, "predictions": {}}
            for target, model in models.items():
                entry["predictions"][target] = {
                    "learned": predict(model, row["x"]),
                    "base_rate": model["prior"],
                    "retrieval": retrieve_prediction(train, row, model),
                    "rule": rule_prediction(row, target),
                }
            predictions.append(entry)
    results = {}
    for target in TARGETS:
        methods = {
            name: metrics(predictions, [r["predictions"][target][name] for r in predictions], target)
            for name in ("learned", "base_rate", "retrieval", "rule")
        }
        best = min(("base_rate", "retrieval", "rule"), key=lambda name: methods[name]["macro_brier"])
        differences = [methods[best]["groups"][g]["brier"] - methods["learned"]["groups"][g]["brier"] for g in groups]
        rng = random.Random(741)
        bootstrap = sorted(mean(rng.choices(differences, k=len(differences))) for _ in range(2000))
        interval = [bootstrap[50], bootstrap[1949]]
        positive_groups = sum(any(r["y"][target] for r in rows if r["group"] == g) for g in groups)
        relative = 1 - methods["learned"]["macro_brier"] / max(methods[best]["macro_brier"], 1e-9)
        passed = (
            len(groups) >= 8
            and positive_groups >= 5
            and relative >= 0.05
            and interval[0] > 0
            and methods["learned"]["macro_log_loss"] < methods[best]["macro_log_loss"]
        )
        results[target] = {
            "methods": methods,
            "best_baseline": best,
            "relative_brier_improvement": relative,
            "group_bootstrap_delta_interval": interval,
            "positive_examples": sum(r["y"][target] for r in rows),
            "positive_groups": positive_groups,
            "passes_predictive_gate": passed,
        }
    return {"folds": folds, "targets": results}, predictions


def render_report(report, destination):
    table = []
    for target, result in report["targets"].items():
        for name, values in result["methods"].items():
            table.append(
                f"<tr><td>{html.escape(target)}</td><td>{name}</td>"
                f"<td>{values['macro_brier']:.4f}</td><td>{values['macro_log_loss']:.4f}</td></tr>"
            )
    findings = "".join(f"<li>{html.escape(item)}</li>" for item in report["findings"])
    destination.write_text(
        """<!doctype html><html lang="de"><meta charset="utf-8">
<title>AEIOU – Lernen aus Verhandlungen</title><style>
body{font:17px/1.6 system-ui;background:#f3f5f8;color:#182533;max-width:1050px;margin:40px auto;padding:24px}
main{background:white;border-radius:16px;padding:32px}h1{line-height:1.2}table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:10px;border-bottom:1px solid #dde3ea}.note{background:#eaf0f8;padding:18px}
</style><main><h1>Lernen aus bisherigen Verhandlungen</h1>
<p>Lokales Training · keine API-Kosten · keine Arena-Sessions</p><ul>"""
        + findings
        + """</ul>
<h2>Zurückgehaltene Challenges und Lieferanten</h2><p>Brier-Fehler und Log-Loss: kleiner ist besser.
Jede Challenge-Gruppe und jede Session innerhalb der Gruppe erhält das gleiche Gewicht.</p>
<table><tr><th>Vorhersage</th><th>Methode</th><th>Brier</th><th>Log-Loss</th></tr>"""
        + "".join(table)
        + """</table>
<p class="note">Diese Messung prüft Vorhersagen zu tatsächlich gesendeten Angeboten. Sie beweist keinen höheren
Verhandlungsscore bei anderen Angeboten. Das trainierte Modell bleibt außerhalb des aktiven Agenten.</p>
<p><a href="report.json">Vollständige Auswertung</a> · <a href="model.json">Trainiertes Modell</a></p></main></html>""",
        encoding="utf-8",
    )


def train_local(source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists():
        raise ValueError("Use a new output directory to preserve prior evaluations")
    rows, audit = load_dataset(source)
    report, predictions = evaluate(rows)
    models = {target: fit(rows, target) for target in TARGETS}
    passed = [target for target, r in report["targets"].items() if r["passes_predictive_gate"]]
    report.update(
        {
            "dataset": audit,
            "features": list(FEATURES),
            "deployment": "offline_only",
            "arena_sessions_used": 0,
            "model_api_calls": 0,
            "findings": [
                f"{audit['hosted_sessions']} gehostete Sessions; {len(rows)} gepaarte Gesprächsschritte; "
                f"{len(report['folds'])} getrennte Challenge-/Lieferantengruppen.",
                f"Prädiktive Freigabekriterien erfüllt: {', '.join(passed) if passed else 'keine Zielgröße'}.",
                "Kein Modell greift in aktive Angebote ein; P2/P5 bleiben unverändert.",
                "Technische Abschlussfehler werden separat markiert und nicht als Verhandlungsstrategie gelernt.",
            ],
            "limitations": [
                "Observational next-response labels do not establish causal benefit of changing actions.",
                "Direct cost excludes unknown supplier relationship penalties; improvement is not final score.",
                "Menu-level matching does not label each unchosen package as rejected.",
                "Historical logs were selected by prior strategies and are correlated within challenges.",
                "Intervals bootstrap few held-out groups and are exploratory, not a deployment guarantee.",
                "Fixed hyperparameters; any tuning after reading these results needs a new sealed evaluation.",
                "Simulated runs excluded. No final score, future reply text, identities or private costs in features.",
            ],
        }
    )
    model_artifact = {"schema": 1, "deployment": "offline_only", "dataset_sha256": audit["sha256"], "models": models}
    for name, obj in (
        ("report.json", report),
        ("model.json", model_artifact),
        ("predictions.json", predictions),
        ("dataset.json", rows),
    ):
        (output / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    render_report(report, output / "report.html")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default="data/local/runs")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = train_local(args.runs, args.output)
    print(
        json.dumps(
            {
                "dataset": report["dataset"],
                "findings": report["findings"],
                "results": {k: {m: v for m, v in r.items() if m != "methods"} for k, r in report["targets"].items()},
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
