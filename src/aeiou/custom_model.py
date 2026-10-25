"""Train and serve a local P10/P18 response/value specialist. No model or Arena API calls.

Training uses scikit-learn; inference reads a portable JSON forest/ridge artifact
with the standard library. It predicts responses, never authorizes an action.
"""

import argparse
import json
import math
import struct
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .config import state_root
from .custom_model_data import CASES, SCHEMA, TARGETS, build, digest, live_features
from .general_context import CONTEXT_FEATURES, context_features
from .response_training import _weights
from .tactical_features import TACTIC_SCHEMA, tactic_features

VERSION = "p10-p18-response-v2"
METHODS = ("prior", "history", "forest", "ridge")


def label_support(rows, target):
    values = [r["y"][target] for r in rows if r["y"][target] is not None]
    return {
        "examples": len(values),
        "label_counts": dict(Counter(str(v) for v in values)) if target != "quoted_cost_gain" else None,
        "discrimination_evaluable": len(set(values)) > 1,
    }


def split_sessions(rows, runs, case_ids=CASES):
    """Per-case chronological 60/20/20; identical trajectories cannot cross splits."""
    sessions = {r["session_id"] for r in rows}
    assignment, cutoffs = {}, {}
    for cid in case_ids:
        ordered = sorted(
            (s for s in sessions if runs[s]["case"]["case_id"] == cid and runs[s]["created_at"]),
            key=lambda s: (runs[s]["created_at"], s),
        )
        if len(ordered) < 10:
            raise ValueError(f"Need at least ten timestamped sessions for {cid}")
        first, second = int(len(ordered) * 0.6), int(len(ordered) * 0.8)
        cutoffs[cid] = {
            "validation_starts": runs[ordered[first]]["created_at"],
            "test_starts": runs[ordered[second]]["created_at"],
        }
        for i, sid in enumerate(ordered):
            assignment[sid] = "train" if i < first else "validation" if i < second else "test"
    clusters = defaultdict(list)
    for sid in sessions:
        canonical = {
            s: [
                {k: m.get(k) for k in ("body", "status", "term_sheets", "refs", "action")}
                | {"role": "buyer" if m.get("from") == "buyer" else "supplier"}
                for m in events
            ]
            for s, events in runs[sid]["threads"].items()
        }
        # Offer and message identifiers are provenance, not independent evidence.
        canonical = json.loads(json.dumps(canonical))
        for events in canonical.values():
            for msg in events:
                if msg.get("term_sheets"):
                    msg["term_sheets"] = [s.get("terms", {}) for s in msg["term_sheets"]]
        clusters[digest(canonical)].append(sid)
        assignment.setdefault(sid, "refit_only")
    crossing = []
    for ids in clusters.values():
        if len({assignment[s] for s in ids}) > 1:
            crossing.append(sorted(ids))
            for sid in ids:
                assignment[sid] = "refit_only"
    splits = {
        name: [r for r in rows if assignment[r["session_id"]] == name]
        for name in ("train", "validation", "test", "refit_only")
    }
    if any(not splits[k] for k in ("train", "validation", "test")):
        raise ValueError("Insufficient disjoint chronological data")
    return splits, {
        "method": "whole-session chronological 60/20/20 within each case",
        "scope": "later attempts on these same suppliers; not unseen-supplier generalization",
        "session_assignments": assignment,
        "cutoffs": cutoffs,
        "cross_boundary_duplicate_groups": crossing,
        "counts": {k: {"examples": len(v), "sessions": len({r["session_id"] for r in v})} for k, v in splits.items()},
    }


def fit(rows, target):
    import numpy as np
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.linear_model import Ridge

    valid = [r for r in rows if r["y"][target] is not None]
    if not valid:
        raise ValueError(f"No labels for {target}")
    weights = np.asarray(_weights(valid)) * len(valid)
    x = np.asarray([r["v"] for r in valid])
    y = np.asarray([r["y"][target] for r in valid])
    center = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - center) ** 2, axis=0, weights=weights))
    scale[scale < 1e-10] = 1
    forest = ExtraTreesRegressor(
        n_estimators=128, max_depth=4, min_samples_leaf=6, max_features=0.8, random_state=741, n_jobs=2
    ).fit(x, y, sample_weight=weights)
    ridge = Ridge(alpha=20).fit((x - center) / scale, y, sample_weight=weights)
    return {
        "target": target,
        "prior": float(np.average(y, weights=weights)),
        "center": center.tolist(),
        "scale": scale.tolist(),
        "forest": forest,
        "ridge": ridge,
        "train_examples": len(valid),
        "label_counts": dict(Counter(str(r["y"][target]) for r in valid)) if target != "quoted_cost_gain" else None,
    }


def portable(fitted, selection):
    trees = []
    for estimator in fitted["forest"].estimators_:
        tree = estimator.tree_
        trees.append(
            {
                "left": tree.children_left.tolist(),
                "right": tree.children_right.tolist(),
                "feature": tree.feature.tolist(),
                "threshold": tree.threshold.tolist(),
                "value": tree.value[:, 0, 0].tolist(),
            }
        )
    return {k: v for k, v in fitted.items() if k not in {"forest", "ridge"}} | {
        "selection": selection,
        "forest": trees,
        "ridge": {"coefficient": fitted["ridge"].coef_.tolist(), "intercept": float(fitted["ridge"].intercept_)},
    }


def _clip(value, target):
    return min(1, max(0, value)) if target != "quoted_cost_gain" else value


def _base(model, row):
    target = model["target"]
    if target == "package_matched":
        total, count = row["history"]["package_matched"], row["history"]["count"]
    else:
        total, count = row["past_targets"][target]["sum"], row["past_targets"][target]["count"]
    return {"prior": model["prior"], "history": (total + 2 * model["prior"]) / (count + 2)}


def predict_one(model, row, method=None, strength=None):
    method = method or model["selection"]["method"]
    strength = model["selection"]["strength"] if strength is None else strength
    bases = _base(model, row)
    if method in bases:
        return bases[method]
    if method == "ridge":
        raw = model["ridge"]["intercept"] + sum(
            c * (x - m) / s
            for c, x, m, s in zip(model["ridge"]["coefficient"], row["v"], model["center"], model["scale"], strict=True)
        )
    elif method == "forest":
        values = [struct.unpack("f", struct.pack("f", x))[0] for x in row["v"]]
        leaves = []
        for tree in model["forest"]:
            node = 0
            while tree["left"][node] != -1:
                node = (
                    tree["left"][node]
                    if values[tree["feature"][node]] <= tree["threshold"][node]
                    else tree["right"][node]
                )
            leaves.append(tree["value"][node])
        raw = mean(leaves)
    else:
        raise ValueError("Unknown estimator")
    return _clip(model["prior"] + strength * (raw - model["prior"]), model["target"])


def metrics(rows, values, target):
    groups = defaultdict(lambda: defaultdict(list))
    for row, value in zip(rows, values, strict=True):
        groups[row["case_id"]][row["session_id"]].append(float(value) - row["y"][target])
    by_case = {
        cid: {
            "mse": mean(mean(e * e for e in errors) for errors in sessions.values()),
            "mae": mean(mean(abs(e) for e in errors) for errors in sessions.values()),
            "sessions": len(sessions),
            "examples": sum(map(len, sessions.values())),
        }
        for cid, sessions in groups.items()
    }
    return {
        "macro_mse": mean(v["mse"] for v in by_case.values()),
        "macro_mae": mean(v["mae"] for v in by_case.values()),
        "by_case": by_case,
    }


def select_model(fitting, validation, target):
    """Select using validation only, including simple baselines as real options."""
    model = portable(fit(fitting, target), {"method": "prior", "strength": 0.0})
    choices = []
    for method in METHODS:
        for strength in (0.0,) if method in {"prior", "history"} else (0.25, 0.5, 0.75, 1.0):
            score = metrics(validation, [predict_one(model, r, method, strength) for r in validation], target)
            choices.append({"method": method, "strength": strength, "validation_mse": score["macro_mse"]})
    choice = min(choices, key=lambda c: (c["validation_mse"], c["strength"], METHODS.index(c["method"])))
    model["selection"] = choice
    return model


def case_support(rows):
    return {
        cid: {
            "examples": len(part := [r for r in rows if r["case_id"] == cid]),
            "sessions": len({r["session_id"] for r in part}),
            "suppliers": sorted({r["supplier_id"] for r in part}),
            "feature_min": [min(r["v"][i] for r in part) for i in range(len(SCHEMA))],
            "feature_max": [max(r["v"][i] for r in part) for i in range(len(SCHEMA))],
        }
        for cid in CASES
    }


def train(output, roots):
    from threadpoolctl import threadpool_limits

    from .research import package

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    runs, rows, audit = build(roots, package().cases)
    splits, split_audit = split_sessions(rows, runs)
    for name, value in [("dataset", rows), ("corpus-audit", audit), ("split-audit", split_audit)]:
        (output / f"{name}.json").write_text(json.dumps(value, indent=2))
    run_dir = output / "runs"
    run_dir.mkdir()
    for sid, run in runs.items():
        (run_dir / f"{sid}.json").write_text(json.dumps(run, indent=2))
    selected, holdout_models, scores, predictions = {}, {}, {}, []
    routes = {cid: {} for cid in CASES}
    specialists = {cid: {} for cid in CASES}
    holdout_specialists = {cid: {} for cid in CASES}
    reliability = {cid: {} for cid in CASES}
    with threadpool_limits(limits=2):
        for target in TARGETS:
            fitting = [r for r in splits["train"] if r["y"][target] is not None]
            validation = [r for r in splits["validation"] if r["y"][target] is not None]
            testing = [r for r in splits["test"] if r["y"][target] is not None]
            if not validation or not testing:
                raise ValueError(f"Missing validation/test target {target}")
            model = select_model(fitting, validation, target)
            choice = model["selection"]
            selected[target] = choice
            for cid in CASES:
                case_train = [r for r in fitting if r["case_id"] == cid]
                case_valid = [r for r in validation if r["case_id"] == cid]
                if not case_valid:
                    routes[cid][target] = {"scope": "pooled", **choice}
                    reliability[cid][target] = {"usable_for_comparison": False, "reason": "no_validation_examples"}
                    continue
                pooled_score = metrics(case_valid, [predict_one(model, r) for r in case_valid], target)
                chosen, scope, score = model, "pooled", pooled_score
                if len(case_train) >= 20 and len({r["session_id"] for r in case_valid}) >= 3:
                    specialist = select_model(case_train, case_valid, target)
                    specialist_score = metrics(case_valid, [predict_one(specialist, r) for r in case_valid], target)
                    # Require a useful validation margin before adding case-specific weights.
                    if specialist_score["macro_mse"] < pooled_score["macro_mse"] * 0.95:
                        chosen, scope, score = specialist, "specialist", specialist_score
                        refit = [r for r in splits["train"] + splits["validation"] if r["case_id"] == cid]
                        holdout_specialists[cid][target] = portable(fit(refit, target), chosen["selection"])
                        specialists[cid][target] = portable(
                            fit([r for r in rows if r["case_id"] == cid], target), chosen["selection"]
                        )
                routes[cid][target] = {"scope": scope, **chosen["selection"]}
                support = label_support(case_valid, target)
                class_sessions = (
                    {
                        str(label): len({r["session_id"] for r in case_valid if r["y"][target] == label})
                        for label in (0, 1)
                    }
                    if target != "quoted_cost_gain"
                    else None
                )
                reliability[cid][target] = {
                    "validation_support": support,
                    "validation_mae": score["macro_mae"],
                    "validation_sessions": len({r["session_id"] for r in case_valid}),
                    "validation_class_sessions": class_sessions,
                    "usable_for_comparison": support["discrimination_evaluable"]
                    and len({r["session_id"] for r in case_valid}) >= 3
                    and (class_sessions is None or min(class_sessions.values()) >= 3),
                    "calibrated_probability": False,
                }
            # Refit on train+validation after selection; test remains unopened.
            holdout = portable(fit(splits["train"] + splits["validation"], target), choice)
            estimates = {
                method: [
                    predict_one(
                        holdout_specialists[r["case_id"]].get(target, holdout) if method == "learned" else holdout,
                        r,
                        method if method != "learned" else None,
                    )
                    for r in testing
                ]
                for method in ("prior", "history", "learned")
            }
            result = {method: metrics(testing, values, target) for method, values in estimates.items()}
            best = min(("prior", "history"), key=lambda name: result[name]["macro_mse"])
            improvement = 1 - result["learned"]["macro_mse"] / max(result[best]["macro_mse"], 1e-12)
            scores[target] = {
                "selection": choice,
                "test": result,
                "best_baseline": best,
                "relative_mse_improvement": improvement,
                "evaluation": "diagnostic holdout of later sessions on the same two suppliers",
                "policy_gain_proven": False,
                "test_support": label_support(testing, target),
                "test_support_by_case": {
                    cid: label_support([r for r in testing if r["case_id"] == cid], target) for cid in CASES
                },
            }
            holdout_models[target] = holdout
            for row, learned, prior, history in zip(
                testing, estimates["learned"], estimates["prior"], estimates["history"], strict=True
            ):
                predictions.append(
                    {k: row[k] for k in ("case_id", "session_id", "turn")}
                    | {
                        "target": target,
                        "observed": row["y"][target],
                        "learned": learned,
                        "prior": prior,
                        "history": history,
                    }
                )
            print(
                json.dumps(
                    {
                        "event": "target_evaluated",
                        "target": target,
                        "selection": choice,
                        "relative_mse_improvement": improvement,
                    }
                ),
                flush=True,
            )
        final = {target: portable(fit(rows, target), selected[target]) for target in TARGETS}
    artifact = {
        "version": VERSION,
        "schema": list(SCHEMA),
        "cases": list(CASES),
        "targets": list(TARGETS),
        "models": final,
        "specialists": specialists,
        "routes": routes,
        "case_support": case_support(rows),
        "reliability": reliability,
        "dataset_sha256": audit["sha256"],
        "training_examples": len(rows),
        "training_sessions": audit["usable_sessions"],
        "deployment": "built_in_advisory",
        "refit": "all usable P10/P18 examples; held-out metrics refer to separate evaluation weights",
        "test_metrics": scores,
        "policy_gain_proven": False,
    }
    (output / "model.json").write_text(json.dumps(artifact, separators=(",", ":")))
    (output / "evaluation-model.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "schema": list(SCHEMA),
                "cases": list(CASES),
                "targets": list(TARGETS),
                "models": holdout_models,
                "specialists": holdout_specialists,
                "routes": routes,
            },
            separators=(",", ":"),
        )
    )
    (output / "test-predictions.json").write_text(json.dumps(predictions, indent=2))
    report = {
        "version": VERSION,
        "corpus": {k: v for k, v in audit.items() if k != "sources"},
        "splits": split_audit,
        "targets": scores,
        "routes": routes,
        "reliability": reliability,
        "model_path": str(output / "model.json"),
        "model_sha256": digest(artifact),
        "new_arena_sessions": 0,
        "model_api_calls": 0,
        "limitations": [
            "Two known suppliers only; no unseen-supplier performance claim.",
            "Repeatedly inspected historical cases; this is development evidence, not a prospective trial.",
            "Predicted next-quote gain excludes unknown relationship penalties and is not a final Arena score.",
            "Outcome prediction does not prove that ranking different actions improves negotiation outcomes.",
            "Supplier exit is learned only from clean observed replies; technical stops are not commercial refusals.",
            "Inspect test_support: a holdout with only one class cannot evaluate discrimination for that target.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2))
    return report


class CustomResponseModel:
    def __init__(self, path=None):
        if path is None:
            registry = json.loads((state_root() / "custom-model.json").read_text())
            path = Path(registry["path"])
        else:
            registry = None
            path = Path(path)
        self.artifact = json.loads(path.read_text())
        if not isinstance(self.artifact, dict):
            raise ValueError("Custom model must be an object")
        if self.artifact.get("version") not in {
            VERSION,
            "p10-p18-response-v1",
            "p10-p18-response-v3-general",
            "p10-p18-response-v4-tactics",
        } or self.artifact.get("schema") != list(SCHEMA):
            raise ValueError("Custom model/schema mismatch")
        if self.artifact.get("context_schema") not in (None, list(CONTEXT_FEATURES)):
            raise ValueError("Custom model business-context schema mismatch")
        if self.artifact.get("tactic_schema") not in (None, list(TACTIC_SCHEMA)):
            raise ValueError("Custom model tactic schema mismatch")
        if registry and digest(self.artifact) != registry["sha256"]:
            raise ValueError("Custom model artifact changed since registration")
        self.path = path
        self.sha256 = digest(self.artifact)

    def forecast(self, case, supplier_id, prefix, proposal, *, context=None):
        row = live_features(case, supplier_id, prefix, proposal)
        support = self.artifact.get("case_support", {}).get(case["case_id"])
        reliability = self.artifact.get("reliability", {}).get(case["case_id"], {})
        reasons, outside = [], []
        if not support:
            reasons.append("training_support_unavailable")
        elif supplier_id not in support["suppliers"]:
            reasons.append("unseen_supplier")
        else:
            outside = [
                name
                for name, value, lo, hi in zip(
                    SCHEMA, row["v"], support["feature_min"], support["feature_max"], strict=True
                )
                if value < lo - 1e-6 or value > hi + 1e-6
            ]
            if outside:
                reasons.append("outside_observed_feature_ranges")
        specialists = self.artifact.get("specialists", {}).get(case["case_id"], {})
        unsupported = [t for t in TARGETS if not reliability.get(t, {}).get("usable_for_comparison")]
        selected = {t: specialists.get(t, self.artifact["models"][t]) for t in TARGETS if t not in unsupported}
        extra = []
        needs_context = any(m.get("context_enabled") for m in selected.values())
        if needs_context:
            if context is None:
                reasons.append("business_context_unavailable")
            else:
                extra = context_features(case, prefix, proposal, context)
                bounds = self.artifact["context_support"][case["case_id"]]
                context_outside = [
                    name
                    for name, value, lo, hi in zip(
                        CONTEXT_FEATURES, extra, bounds["feature_min"], bounds["feature_max"], strict=True
                    )
                    if value < lo - 1e-6 or value > hi + 1e-6
                ]
                outside.extend(context_outside)
                if context_outside:
                    reasons.append("outside_observed_context_ranges")
        tactics = tactic_features(prefix, proposal) if any(m.get("tactics_enabled") for m in selected.values()) else []
        if tactics and self.artifact.get("tactic_support"):
            bounds = self.artifact["tactic_support"][case["case_id"]]
            tactic_outside = [
                name
                for name, value, lo, hi in zip(
                    TACTIC_SCHEMA, tactics, bounds["feature_min"], bounds["feature_max"], strict=True
                )
                if value < lo - 1e-6 or value > hi + 1e-6
            ]
            outside.extend(tactic_outside)
            if tactic_outside:
                reasons.append("outside_observed_tactic_ranges")
        predictions = (
            {
                t: predict_one(
                    model,
                    {
                        **row,
                        "v": row["v"]
                        + (extra if model.get("context_enabled") else [])
                        + (tactics if model.get("tactics_enabled") else []),
                    },
                )
                for t, model in selected.items()
            }
            if not reasons
            else {}
        )
        if not all(math.isfinite(v) for v in predictions.values()):
            raise ValueError("Non-finite model forecast")
        return {
            "model": self.artifact["version"],
            "model_sha256": self.sha256,
            # Do not expose unsupported probabilities as apparent decision evidence.
            "predictions": {t: p for t, p in predictions.items() if t not in unsupported} if not reasons else {},
            "unsupported_targets": unsupported,
            "reliability": reliability,
            "domain": {"supported": not reasons, "reasons": reasons, "out_of_range_features": outside},
            "business_context": {
                "used_by_supported_targets": needs_context,
                "sha256": context.get("sha256") if context else None,
                "cutoff": context.get("cutoff") if context else None,
                "source_count": context.get("source_count", 0) if context else 0,
            },
            "signal_evidence": row["signal_evidence"],
            "status": "abstained" if reasons else "advisory_only",
            "meaning": "Next observed response to this whole menu. Package match is not binding acceptance; "
            "cost gain is a fraction of the prior direct cost, not final score.",
            "policy_gain_proven": False,
        }


def register(path):
    model = CustomResponseModel(path)
    registry = {"path": str(model.path.resolve()), "sha256": model.sha256, "version": model.artifact["version"]}
    pending = state_root() / "custom-model.json.tmp"
    pending.write_text(json.dumps(registry, indent=2))
    pending.replace(state_root() / "custom-model.json")
    return registry


def model_status():
    try:
        model = CustomResponseModel()
        return {
            "available": True,
            "version": model.artifact["version"],
            "sha256": model.sha256,
            "cases": list(CASES),
            "training_examples": model.artifact["training_examples"],
            "training_sessions": model.artifact["training_sessions"],
            "general_training_examples": model.artifact.get("general_training_examples"),
            "general_training_sessions": model.artifact.get("general_training_sessions"),
            "role": "Shadow evaluation by default; live advice only in the experimental custom buyer",
            "built_in": True,
            "default_mode": "shadow",
            "experimental_agent": "custom-p10-p18",
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False, "cases": list(CASES)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sources", nargs="+", default=["data/local", "reports"])
    parser.add_argument("--register", action="store_true", help="Select the saved artifact for the custom buyer")
    args = parser.parse_args()
    result = train(args.output, args.sources)
    if args.register:
        register(result["model_path"])
    print(json.dumps({k: result[k] for k in ("version", "model_path", "model_sha256")}, indent=2))
