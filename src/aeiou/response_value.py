"""Offline multi-target value prediction with nested supplier-group validation.

Fixed model family and shrinkage grid; no Arena or model API calls. The previous
frozen corpus is used for comparison. Better prediction is not a policy-gain test.
"""

import argparse
import hashlib
import html
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from .response_learning import FEATURES
from .response_refinement import HISTORY_FEATURES, group_splits
from .response_training import _weights
from .response_value_features import EXTRA_FEATURES, enrich_rows, read_runs

TARGETS = ("direct_cost_improved", "quoted_cost_gain", "supplier_exit")
METHODS = ("prior", "history", "legacy_forest", "rich_forest", "rich_boosted")
SCHEMA = FEATURES + HISTORY_FEATURES + EXTRA_FEATURES


def fit_models(rows, target):
    import numpy as np
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor

    y = np.array([r["y"][target] for r in rows])
    weights = np.array(_weights(rows)) * len(rows)
    artifact = {"target": target, "prior": float(np.average(y, weights=weights)), "models": {}}
    if len(set(y)) < 2:
        return artifact
    for name, field in (("legacy_forest", "z"), ("rich_forest", "v"), ("rich_boosted", "v")):
        model = (
            HistGradientBoostingRegressor(
                max_iter=80,
                max_depth=2,
                max_leaf_nodes=4,
                min_samples_leaf=20,
                learning_rate=0.04,
                l2_regularization=10,
                early_stopping=False,
                random_state=741,
            )
            if name == "rich_boosted"
            else ExtraTreesRegressor(
                n_estimators=96, max_depth=5, min_samples_leaf=12, max_features=0.8, random_state=741, n_jobs=2
            )
        )
        model.fit([r[field] for r in rows], y, sample_weight=weights)
        artifact["models"][name] = model
    return artifact


def predictions(artifact, rows):
    import numpy as np

    target, prior = artifact["target"], artifact["prior"]
    out = {
        "prior": np.full(len(rows), prior),
        "history": np.array(
            [(r["past_targets"][target]["sum"] + 2 * prior) / (r["past_targets"][target]["count"] + 2) for r in rows]
        ),
    }
    for name in METHODS[2:]:
        model = artifact["models"].get(name)
        out[name] = (
            model.predict([r["z" if name == "legacy_forest" else "v"] for r in rows]) if model else out["prior"].copy()
        )
        if target != "quoted_cost_gain":
            out[name] = np.clip(out[name], 0, 1)
    return out


def add_target_history(rows):
    """Past labels are observable only after their reply; inputs remain unmodified."""
    import copy

    output = copy.deepcopy(rows)
    threads = defaultdict(list)
    for row in output:
        threads[(row["session_id"], row["supplier_id"])].append(row)
    for thread in threads.values():
        thread.sort(key=lambda r: r["turn"])
        for i, row in enumerate(thread):
            past = [r for r in thread[:i] if r["turn"] + 1 < row["turn"]]
            row["past_targets"] = {}
            for target in TARGETS:
                values = [r["y"][target] for r in past if r["y"][target] is not None]
                row["past_targets"][target] = {"sum": sum(values), "count": len(values)}
    return output


def select(train, target):
    import numpy as np

    out = {name: np.full(len(train), np.nan) for name in METHODS}
    audits = []
    for ti, vi in group_splits(train):
        fitting, validation = [train[i] for i in ti], [train[i] for i in vi]
        probabilities = predictions(fit_models(fitting, target), validation)
        for name in METHODS:
            out[name][vi] = probabilities[name]
        audits.append(
            {
                "train_groups": sorted({r["group"] for r in fitting}),
                "validation_groups": sorted({r["group"] for r in validation}),
            }
        )
    assert all(np.isfinite(p).all() for p in out.values())
    y, w = np.array([r["y"][target] for r in train]), _weights(train)
    choices = []
    for name in METHODS:
        for strength in (0.0,) if name == "prior" else (0.25, 0.5, 0.75, 1.0):
            p = out["prior"] + strength * (out[name] - out["prior"])
            choices.append(dict(model=name, strength=strength, inner_mse=float(np.average((y - p) ** 2, weights=w))))
    choice = min(choices, key=lambda c: (c["inner_mse"], c["strength"], METHODS.index(c["model"])))
    p = out["prior"] + choice["strength"] * (out[choice["model"]] - out["prior"])
    # Empirical residual reference, not a guaranteed confidence bound on actions.
    choice["inner_abs_error_p90"] = float(np.quantile(abs(y - p), 0.9))
    return choice, audits


def predict(artifact, selection, rows):
    p = predictions(artifact, rows)
    return p["prior"] + selection["strength"] * (p[selection["model"]] - p["prior"])


def evaluate_errors(rows, values, target):
    groups = defaultdict(lambda: defaultdict(list))
    for row, value in zip(rows, values, strict=True):
        error = float(value) - row["y"][target]
        groups[row["group"]][row["session_id"]].append(error)
    stats = {
        g: {
            "mse": mean(mean(e * e for e in es) for es in sessions.values()),
            "mae": mean(mean(abs(e) for e in es) for es in sessions.values()),
        }
        for g, sessions in groups.items()
    }
    return {
        "macro_mse": mean(v["mse"] for v in stats.values()),
        "macro_mae": mean(v["mae"] for v in stats.values()),
        "groups": stats,
    }


def forecast(artifact, rows):
    """Auditable forecasts only. Never authorizes a live offer or an acceptance.

    Novel actions require evidence the archive does not contain; the caller must
    retain the incumbent strategy. Target gains omit private relationship cost.
    """
    if artifact["schema"] != list(SCHEMA):
        raise ValueError("Feature schema mismatch")
    results = []
    values = {t: predict(artifact["models"][t], artifact["selections"][t], rows) for t in TARGETS}
    for i, row in enumerate(rows):
        results.append(
            {
                "predictions": {t: float(values[t][i]) for t in TARGETS},
                "empirical_abs_error_p90": {t: artifact["selections"][t]["inner_abs_error_p90"] for t in TARGETS},
                "signal_evidence": row.get("signal_evidence", []),
                "decision": "retain_incumbent",
                "reason": "forecast_only_no_counterfactual_policy_validation",
                "predictive_gates": artifact.get("predictive_gates", {t: False for t in TARGETS}),
            }
        )
    return results


def experiment(dataset, runs_folder, previous_path, output):
    import joblib
    from threadpoolctl import threadpool_limits

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists():
        raise ValueError("Use a new output directory")
    raw = json.loads(Path(dataset).read_text())
    rows = add_target_history(enrich_rows(raw, read_runs(runs_folder)))
    previous = {(r["session_id"], r["supplier_id"], r["turn"]): r for r in json.loads(Path(previous_path).read_text())}

    def key(r):
        return r["session_id"], r["supplier_id"], r["turn"]

    assert set(previous) == {key(r) for r in rows}
    assert all(
        previous[key(r)]["x"] == r["x"]
        and previous[key(r)]["y"]["direct_cost_improved"] == r["y"]["direct_cost_improved"]
        for r in rows
    )
    (output / "dataset.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    groups, collected, folds = sorted({r["group"] for r in rows}), [], []
    final_models, final_selections, scores = {}, {}, {}
    with threadpool_limits(limits=2):
        for target in TARGETS:
            valid = [r for r in rows if r["y"][target] is not None]
            result = []
            for group in groups:
                train, test = [r for r in valid if r["group"] != group], [r for r in valid if r["group"] == group]
                if not test:
                    continue
                assert not {r["supplier_id"] for r in train} & {r["supplier_id"] for r in test}
                assert not {r["case_id"] for r in train} & {r["case_id"] for r in test}
                choice, audit = select(train, target)
                model = fit_models(train, target)
                estimates = predictions(model, test)
                estimates["selected"] = predict(model, choice, test)
                for i, row in enumerate(test):
                    entry = {k: row[k] for k in ("session_id", "supplier_id", "case_id", "group", "turn", "y")}
                    entry["predictions"] = {name: float(p[i]) for name, p in estimates.items()}
                    if target == "direct_cost_improved":
                        entry["predictions"]["previous_refined"] = previous[key(row)]["predictions"][target]["learned"]
                    result.append(entry)
                folds.append({"target": target, "held_out": group, "selection": choice, "inner_splits": audit})
                (output / "progress.json").write_text(json.dumps(folds, indent=2), encoding="utf-8")
                print(json.dumps({"target": target, "group": group, "selection": choice["model"]}), flush=True)
            methods = {
                name: evaluate_errors(result, [r["predictions"][name] for r in result], target)
                for name in result[0]["predictions"]
            }
            baselines = ["prior", "history", "legacy_forest"] + (
                ["previous_refined"] if target == "direct_cost_improved" else []
            )
            best = min(baselines, key=lambda n: methods[n]["macro_mse"])
            gains = [
                methods[best]["groups"][g]["mse"] - methods["selected"]["groups"][g]["mse"]
                for g in methods["selected"]["groups"]
            ]
            rng = random.Random(741)
            boot = sorted(mean(rng.choices(gains, k=len(gains))) for _ in range(2000))
            improvement = 1 - methods["selected"]["macro_mse"] / max(methods[best]["macro_mse"], 1e-12)
            positive_groups = sum(any(r["y"][target] > 0 for r in valid if r["group"] == g) for g in groups)
            scores[target] = {
                "methods": methods,
                "best_baseline": best,
                "relative_mse_improvement": improvement,
                "group_bootstrap_delta_interval": [boot[50], boot[1949]],
                "examples": len(valid),
                "positive_groups": positive_groups,
                "passes_predictive_gate": bool(
                    len(gains) >= 8 and positive_groups >= 5 and improvement >= 0.05 and boot[50] > 0
                ),
            }
            collected.append({"target": target, "rows": result})
            final_selections[target], _ = select(valid, target)
            final_models[target] = fit_models(valid, target)
    artifact = {
        "schema": list(SCHEMA),
        "models": final_models,
        "selections": final_selections,
        "deployment": "offline_forecast_only",
        "policy_enabled": False,
        "predictive_gates": {t: scores[t]["passes_predictive_gate"] for t in TARGETS},
    }
    report = {
        "targets": scores,
        "folds": folds,
        "final_selections": final_selections,
        "features": len(SCHEMA),
        "examples": len(rows),
        "sessions": len({r["session_id"] for r in rows}),
        "groups": len(groups),
        "model_api_calls": 0,
        "arena_sessions_used": 0,
        "deployment": artifact["deployment"],
        "dataset_sha256": hashlib.sha256(Path(dataset).read_bytes()).hexdigest(),
        "limitations": [
            "Previously inspected groups, not a fresh prospective evaluation.",
            "Direct quoted cost omits unknown private relationship cost.",
            "Gain conditional on next primary quote; missing on exits, not zero.",
            "Rare or absent exits cannot validate withdrawal-risk estimates.",
            "No causal final-score claim or permission to override the incumbent.",
        ],
    }
    joblib.dump(artifact, output / "model.joblib", compress=3)
    for name, value in [
        ("report.json", report),
        ("predictions.json", collected),
        ("model.json", {"schema": list(SCHEMA), "selections": final_selections, "policy_enabled": False}),
    ]:
        (output / name).write_text(json.dumps(value, indent=2), encoding="utf-8")
    table = "".join(
        f"<tr><td>{t}</td><td>{r['methods']['selected']['macro_mse']:.6f}</td>"
        f"<td>{r['best_baseline']}: {r['methods'][r['best_baseline']]['macro_mse']:.6f}</td>"
        f"<td>{100 * r['relative_mse_improvement']:+.1f}%</td><td>{r['passes_predictive_gate']}</td></tr>"
        for t, r in scores.items()
    )
    (output / "report.html").write_text(
        f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Response value model</title>
<style>body{{font:17px system-ui;max-width:1100px;margin:45px auto;padding:24px;
background:#101923;color:#edf5fb;line-height:1.6}}table{{width:100%;border-collapse:collapse}}
th,td{{padding:14px;border-bottom:1px solid #456}}a{{color:#8dd6ff}}</style>
<h1>Learning value, business signals and complete packages</h1>
<p>{len(rows)} frozen examples · {report["sessions"]} sessions · {len(groups)} disjoint outer groups ·
{len(SCHEMA)} features · zero API calls</p>
<table><tr><th>Target</th><th>Selected MSE</th><th>Strongest baseline</th><th>Error reduction</th>
<th>Predictive gate</th></tr>{table}</table>
<p>For binary targets MSE is Brier error; cost gain is a signed fraction of the prior primary quote's direct cost.
Lower is better. Each session and supplier group receives balanced weight.</p>
<p>Estimator and shrinkage are chosen inside three inner group folds. Historic version comparison uses identical
examples and outer groups. No live policy is enabled.</p>
<p>Previous model progress error:
{scores["direct_cost_improved"]["methods"]["previous_refined"]["macro_mse"]:.6f}.
The selected model improves on that version, but not on the stronger old-feature forest baseline shown above.</p>
<p>Capabilities added: signed concession size, complete price/term associations, evidence-backed business signals,
and explicitly separate supplier-exit labels. A smaller predictive error is not proof of a higher Arena score.</p>
<ul>{"".join("<li>" + html.escape(x) + "</li>" for x in report["limitations"])}</ul>
<p><a href="report.json">Full report and fold audits</a></p></html>""",
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("dataset", "runs", "previous", "output"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--dependency-root")
    args = parser.parse_args()
    if args.dependency_root:
        sys.path.insert(0, str(Path(args.dependency_root).resolve()))
    result = experiment(args.dataset, args.runs, args.previous, args.output)
    print(
        json.dumps({t: {k: v for k, v in s.items() if k != "methods"} for t, s in result["targets"].items()}, indent=2)
    )
