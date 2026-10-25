"""Compare broader negotiation history and frozen business context without API calls."""

import argparse
import copy
import json
from pathlib import Path

from .custom_model import fit, metrics, portable, predict_one, register, select_model, split_sessions, train
from .custom_model_data import CASES, TARGETS, build, digest
from .general_context import CONTEXT_FEATURES, context_features, snapshot_context
from .research import package, research

GENERAL_VERSION = "p10-p18-response-v3-general"


def temporal_background(rows, cutoff):
    # Missing timestamps cannot establish availability before a held-out decision.
    return [r for r in rows if r["case_id"] not in CASES and r.get("created_at") and r["created_at"] < cutoff]


def vector_rows(rows, context_enabled):
    return [{**r, "v": r["v"] + r["context_vector"] if context_enabled else r["v"]} for r in rows]


def promotion_gate(chosen, variants, usable):
    """A held-out regression can reject promotion, never select another tuned variant."""
    if chosen == "focused":
        return "focused", "no_validation_gain"
    if not usable:
        return "focused", "insufficient_target_support"
    candidate, baseline = variants[chosen]["test"], variants["focused"]["test"]
    if candidate["macro_mse"] >= baseline["macro_mse"] or candidate["macro_mae"] > baseline["macro_mae"]:
        return "focused", "heldout_regression"
    return chosen, "passed_development_gate"


def relative_improvement(baseline, value):
    if baseline <= 1e-12:
        return 0.0 if value <= 1e-12 else None
    return 1 - value / baseline


def train_general(output, roots):
    from threadpoolctl import threadpool_limits

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # Reproduce the existing focused baseline on exactly the same chronological split.
    focused_report = train(output / "focused", roots)
    focused = json.loads((output / "focused/model.json").read_text())
    focused_eval = json.loads((output / "focused/evaluation-model.json").read_text())
    pkg = package()
    runs, rows, corpus = build(roots, pkg.cases, case_ids=None)
    contexts, research_cache = {}, {}
    for row in rows:
        run = runs[row["session_id"]]
        case, sid = run["case"], row["supplier_id"]
        case_key = digest(case)
        key = f"{case_key}:{sid}"
        if key not in contexts:
            if case_key not in research_cache:
                research_cache[case_key] = research(case, pkg)
            contexts[key] = snapshot_context(case, sid, research_cache[case_key])
        events, turn = run["threads"][sid], row["turn"]
        row["context_vector"] = context_features(case, events[:turn], events[turn], contexts[key])
        row["context_key"] = key
    target_rows = [r for r in rows if r["case_id"] in CASES]
    splits, split_audit = split_sessions(target_rows, runs)
    assert split_audit["session_assignments"] == focused_report["splits"]["session_assignments"]
    validation_cutoff = min(c["validation_starts"] for c in split_audit["cutoffs"].values())
    test_cutoff = min(c["test_starts"] for c in split_audit["cutoffs"].values())
    background_train = temporal_background(rows, validation_cutoff)
    background_refit = temporal_background(rows, test_cutoff)
    background_all = [r for r in rows if r["case_id"] not in CASES]
    temporal_audit = {
        "validation_cutoff": validation_cutoff,
        "test_cutoff": test_cutoff,
        "selection_sessions": sorted({r["session_id"] for r in background_train}),
        "evaluation_refit_sessions": sorted({r["session_id"] for r in background_refit}),
        "deployment_background_sessions": sorted({r["session_id"] for r in background_all}),
        "rule": "General-history rows must predate the earliest target validation/test boundary; "
        "final deployment refits may use all observations. Test labels never choose a variant.",
    }
    artifact = copy.deepcopy(focused)
    artifact.update(
        version=GENERAL_VERSION,
        context_schema=list(CONTEXT_FEATURES),
        general_corpus=corpus["by_case"],
        training_examples=len(rows),
        training_sessions=corpus["usable_sessions"],
        dataset_sha256=digest(rows),
        focused_training_examples=len(target_rows),
    )
    artifact["context_support"] = {
        cid: {
            "feature_min": [
                min(r["context_vector"][i] for r in target_rows if r["case_id"] == cid)
                for i in range(len(CONTEXT_FEATURES))
            ],
            "feature_max": [
                max(r["context_vector"][i] for r in target_rows if r["case_id"] == cid)
                for i in range(len(CONTEXT_FEATURES))
            ],
        }
        for cid in CASES
    }
    evaluation = copy.deepcopy(focused_eval)
    evaluation.update(version=GENERAL_VERSION, context_schema=list(CONTEXT_FEATURES))
    comparisons, predictions = {}, []
    candidate_models = {cid: {} for cid in CASES}
    with threadpool_limits(limits=2):
        for cid in CASES:
            comparisons[cid] = {}
            for target in TARGETS:
                valid = [r for r in splits["validation"] if r["case_id"] == cid and r["y"][target] is not None]
                test = [r for r in splits["test"] if r["case_id"] == cid and r["y"][target] is not None]
                route = focused["routes"][cid][target]
                baseline_train = [r for r in splits["train"] if route["scope"] != "specialist" or r["case_id"] == cid]
                baseline = portable(fit(baseline_train, target), route)
                baseline_metrics = metrics(valid, [predict_one(baseline, r) for r in valid], target)
                baseline_eval = focused_eval["specialists"].get(cid, {}).get(target, focused_eval["models"][target])
                baseline_final = focused["specialists"].get(cid, {}).get(target, focused["models"][target])
                variants = {
                    "focused": {
                        "validation": baseline_metrics,
                        "evaluation_model": baseline_eval,
                        "final_model": baseline_final,
                        "context_enabled": False,
                    }
                }
                for name, use_context, only_case, use_general in (
                    ("focused_context", True, False, False),
                    ("specialist_context", True, True, False),
                    ("general_history", False, False, True),
                    ("general_history_context", True, False, True),
                ):
                    fitting = splits["train"] + (background_train if use_general else [])
                    refit = splits["train"] + splits["validation"] + (background_refit if use_general else [])
                    final = target_rows + (background_all if use_general else [])
                    if only_case:
                        fitting, refit, final = (
                            [r for r in part if r["case_id"] == cid] for part in (fitting, refit, final)
                        )
                    fitted = select_model(vector_rows(fitting, use_context), vector_rows(valid, use_context), target)
                    selection = fitted["selection"]
                    result = metrics(valid, [predict_one(fitted, r) for r in vector_rows(valid, use_context)], target)
                    variants[name] = {
                        "validation": result,
                        "context_enabled": use_context,
                        "selection": selection,
                        "evaluation_model": portable(fit(vector_rows(refit, use_context), target), selection),
                        "final_model": portable(fit(vector_rows(final, use_context), target), selection),
                    }
                # Fix selection before measuring held-out performance. Prefer focused on small differences.
                candidate = min(variants, key=lambda k: variants[k]["validation"]["macro_mse"])
                chosen = (
                    candidate
                    if variants[candidate]["validation"]["macro_mse"] < baseline_metrics["macro_mse"] * 0.95
                    else "focused"
                )
                for name, info in variants.items():
                    values = [
                        predict_one(info["evaluation_model"], r) for r in vector_rows(test, info["context_enabled"])
                    ]
                    info["test"] = metrics(test, values, target)
                    for row, value in zip(test, values, strict=True):
                        predictions.append(
                            {
                                "case_id": cid,
                                "session_id": row["session_id"],
                                "turn": row["turn"],
                                "target": target,
                                "variant": name,
                                "observed": row["y"][target],
                                "prediction": value,
                            }
                        )
                selected = variants[chosen]
                candidate_models[cid][target] = {
                    **selected["final_model"],
                    "context_enabled": selected["context_enabled"],
                    "variant": chosen,
                }
                active, reason = promotion_gate(
                    chosen, variants, artifact["reliability"][cid][target]["usable_for_comparison"]
                )
                deployed = variants[active]
                artifact["specialists"][cid][target] = {
                    **deployed["final_model"],
                    "context_enabled": deployed["context_enabled"],
                }
                evaluation["specialists"][cid][target] = {
                    **selected["evaluation_model"],
                    "context_enabled": selected["context_enabled"],
                }
                artifact["routes"][cid][target] = {"scope": active, **deployed["final_model"]["selection"]}
                artifact["reliability"][cid][target]["validation_mae"] = deployed["validation"]["macro_mae"]
                comparisons[cid][target] = {
                    "selected": chosen,
                    "active": active,
                    "promotion_reason": reason,
                    "variants": {
                        name: {k: v for k, v in info.items() if k not in {"final_model", "evaluation_model"}}
                        for name, info in variants.items()
                    },
                    "heldout_mse_improvement": relative_improvement(
                        variants["focused"]["test"]["macro_mse"], selected["test"]["macro_mse"]
                    ),
                }
                print(
                    json.dumps(
                        {
                            "case": cid,
                            "target": target,
                            "selected": chosen,
                            "active": active,
                            "promotion_reason": reason,
                            "heldout_mse_improvement": comparisons[cid][target]["heldout_mse_improvement"],
                        }
                    ),
                    flush=True,
                )
    artifact["general_data_comparison"] = comparisons
    artifact["general_training_examples"] = len(rows)
    artifact["general_training_sessions"] = corpus["usable_sessions"]
    if not any(r["active"].startswith("general_") for targets in comparisons.values() for r in targets.values()):
        artifact["training_examples"] = focused["training_examples"]
        artifact["training_sessions"] = focused["training_sessions"]
    evaluation["routes"] = {
        cid: {t: {"scope": c["selected"]} for t, c in targets.items()} for cid, targets in comparisons.items()
    }
    # Keep the focused report explicitly labeled; selected-variant results are in this comparison.
    artifact["focused_test_metrics"] = artifact.pop("test_metrics")
    report = {
        "version": GENERAL_VERSION,
        "corpus": {k: v for k, v in corpus.items() if k != "sources"},
        "comparisons": comparisons,
        "temporal_audit": temporal_audit,
        "splits": split_audit,
        "model_path": str(output / "model.json"),
        "model_sha256": digest(artifact),
        "new_arena_sessions": 0,
        "model_api_calls": 0,
        "limitations": [
            "Observed next-quote prediction, not proof of better deals or higher scores.",
            "Historical data already studied; a prospective comparison is still needed.",
            "Business records are largely constant per supplier/case, so apparent benefit may reflect identity.",
            "Only dated numeric record features enter weights; full documents remain retrievable evidence.",
            "Demo and practice histories are balanced by case and session; synthetic local sessions are excluded.",
        ],
    }
    for name, value in (
        ("model", artifact),
        ("evaluation-model", evaluation),
        ("candidate-models", candidate_models),
        ("report", report),
        ("business-context", contexts),
        ("dataset", rows),
        ("corpus-audit", corpus),
        ("test-predictions", predictions),
        ("split-audit", split_audit),
    ):
        (output / f"{name}.json").write_text(json.dumps(value, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sources", nargs="+", default=["data/local", "reports"])
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args()
    report = train_general(args.output, args.sources)
    if args.register:
        register(report["model_path"])
    print(json.dumps({k: report[k] for k in ("version", "model_path", "model_sha256")}, indent=2))
