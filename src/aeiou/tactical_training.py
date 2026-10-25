"""Refit response models with actual evidence use; freeze before prospective trials."""

import argparse
import json
from pathlib import Path

from .custom_model import fit, metrics, portable, predict_one, select_model
from .custom_model_data import CASES, TARGETS, digest
from .general_training import temporal_background, train_general
from .tactical_features import EXPERIENCE_STATE_SCHEMA, TACTIC_SCHEMA, experience_state, tactic_features


def tactical_gate(variants, incumbent, usable):
    """Compare to the deployed context baseline, not a weaker historical model."""
    chosen = min(variants, key=lambda name: variants[name]["validation"]["macro_mse"])
    baseline = variants[incumbent]
    if chosen in {"focused", incumbent} or (
        variants[chosen]["validation"]["macro_mse"] >= baseline["validation"]["macro_mse"] * 0.95
    ):
        return incumbent, "no_validation_gain_over_incumbent"
    if not usable:
        return incumbent, "insufficient_target_support"
    candidate, previous = variants[chosen]["test"], baseline["test"]
    if candidate["macro_mse"] >= previous["macro_mse"] or candidate["macro_mae"] > previous["macro_mae"]:
        return incumbent, "heldout_regression_against_incumbent"
    return chosen, "passed_development_gate"


def train_tactical(output, roots):
    from threadpoolctl import threadpool_limits

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    baseline_report = train_general(output / "baseline", roots)
    base = output / "baseline"
    rows = json.loads((base / "dataset.json").read_text())
    target_runs = {p.stem: json.loads(p.read_text()) for p in (base / "focused/runs").glob("*.json")}
    # Broad histories are already deduplicated and stripped to public messages.
    from .custom_model_data import gather
    from .research import package

    runs, _ = gather(roots, package().cases, case_ids=None)
    for row in rows:
        events = runs[row["session_id"]]["threads"][row["supplier_id"]]
        row["tactic_vector"] = tactic_features(events[: row["turn"]], events[row["turn"]])
    artifact = json.loads((base / "model.json").read_text())
    focused = json.loads((base / "focused/model.json").read_text())
    focused_eval = json.loads((base / "focused/evaluation-model.json").read_text())
    assignments = baseline_report["splits"]["session_assignments"]
    splits = {
        name: [r for r in rows if assignments.get(r["session_id"]) == name] for name in ("train", "validation", "test")
    }
    cutoffs = baseline_report["temporal_audit"]
    background_train = temporal_background(rows, cutoffs["validation_cutoff"])
    background_refit = temporal_background(rows, cutoffs["test_cutoff"])
    target = [r for r in rows if r["case_id"] in CASES]
    comparisons, eval_models = {}, {}

    def vector(part):
        return [{**r, "v": r["v"] + r["context_vector"] + r["tactic_vector"]} for r in part]

    with threadpool_limits(limits=2):
        for cid in CASES:
            comparisons[cid], eval_models[cid] = {}, {}
            for label in TARGETS:
                valid = [r for r in splits["validation"] if r["case_id"] == cid and r["y"][label] is not None]
                testing = [r for r in splits["test"] if r["case_id"] == cid and r["y"][label] is not None]
                route = focused["routes"][cid][label]
                fitting = [r for r in splits["train"] if route["scope"] != "specialist" or r["case_id"] == cid]
                bm = portable(fit(fitting, label), route)
                be = focused_eval["specialists"].get(cid, {}).get(label, focused_eval["models"][label])
                variants = {
                    "focused": {
                        "validation": metrics(valid, [predict_one(bm, r) for r in valid], label),
                        "test": metrics(testing, [predict_one(be, r) for r in testing], label),
                    }
                }
                incumbent_comparison = baseline_report["comparisons"][cid][label]
                incumbent = "incumbent"
                variants[incumbent] = incumbent_comparison["variants"][incumbent_comparison["active"]]
                candidates = {}
                for name, general, specialist in (
                    ("case_tactics", False, True),
                    ("pooled_tactics", False, False),
                    ("general_tactics", True, False),
                ):
                    train = splits["train"] + (background_train if general else [])
                    refit = splits["train"] + splits["validation"] + (background_refit if general else [])
                    final = rows if general else target
                    if specialist:
                        train, refit, final = ([r for r in p if r["case_id"] == cid] for p in (train, refit, final))
                    selected = select_model(vector(train), vector(valid), label)
                    evaluated = portable(fit(vector(refit), label), selected["selection"])
                    variants[name] = {
                        "validation": metrics(valid, [predict_one(selected, r) for r in vector(valid)], label),
                        "test": metrics(testing, [predict_one(evaluated, r) for r in vector(testing)], label),
                        "selection": selected["selection"],
                    }
                    candidates[name] = portable(fit(vector(final), label), selected["selection"])
                    candidates[name].update(context_enabled=True, tactics_enabled=True)
                    evaluated.update(context_enabled=True, tactics_enabled=True)
                    eval_models[cid][label + ":" + name] = evaluated
                active, reason = tactical_gate(
                    variants, incumbent, artifact["reliability"][cid][label]["usable_for_comparison"]
                )
                if active != incumbent:
                    artifact["specialists"].setdefault(cid, {})[label] = candidates[active]
                    artifact["reliability"][cid][label]["validation_mae"] = variants[active]["validation"]["macro_mae"]
                    artifact["routes"][cid][label] = {"scope": active, **candidates[active]["selection"]}
                comparisons[cid][label] = {
                    "incumbent_route": incumbent_comparison["active"],
                    "active_tactical": active,
                    "promotion_reason": reason,
                    "variants": variants,
                }
                print(
                    json.dumps({"case": cid, "target": label, "tactical_route": active, "reason": reason}), flush=True
                )

    # Store representative public experiences for an independently selectable support path.
    # No final score, private cost, supplier reasoning, or future user records are inputs.
    state_names = list(EXPERIENCE_STATE_SCHEMA)
    experiences = []
    for row in target:
        events = target_runs[row["session_id"]]["threads"][row["supplier_id"]]
        turn = row["turn"]
        experiences.append(
            {
                "case_id": row["case_id"],
                "session_id": row["session_id"],
                "created_at": row.get("created_at"),
                "turn": turn,
                "state": experience_state(target_runs[row["session_id"]]["case"], events[:turn]),
                "proposal": {k: events[turn][k] for k in ("body", "term_sheets", "refs") if k in events[turn]},
                "source_attachment_present": bool(events[turn].get("attachments")),
                "response_status": events[turn + 1].get("status"),
                "response_terms": events[turn + 1].get("term_sheets", []),
                "observed": row["y"],
            }
        )
    artifact.update(
        version="p10-p18-response-v4-tactics",
        tactic_schema=list(TACTIC_SCHEMA),
        tactic_comparison=comparisons,
        experience_state_schema=state_names,
        experiences=experiences,
        training_cutoff=max(r["created_at"] for r in rows if r.get("created_at")),
        policy_gain_proven=False,
        dataset_sha256=digest(rows),
    )
    artifact["tactic_support"] = {
        cid: {
            "feature_min": [
                min(r["tactic_vector"][i] for r in target if r["case_id"] == cid) for i in range(len(TACTIC_SCHEMA))
            ],
            "feature_max": [
                max(r["tactic_vector"][i] for r in target if r["case_id"] == cid) for i in range(len(TACTIC_SCHEMA))
            ],
        }
        for cid in CASES
    }
    if any(
        route["scope"].startswith("general_") for routes in artifact["routes"].values() for route in routes.values()
    ):
        artifact.update(training_examples=len(rows), training_sessions=baseline_report["corpus"]["usable_sessions"])
    report = {
        "version": artifact["version"],
        "corpus": baseline_report["corpus"],
        "comparisons": comparisons,
        "model_path": str(output / "model.json"),
        "model_sha256": digest(artifact),
        "limitation": "Historical predictive development only; prospective outcome trials remain required.",
    }
    for name, value in (("model", artifact), ("report", report), ("tactical-evaluation-models", eval_models)):
        (output / f"{name}.json").write_text(json.dumps(value, separators=(",", ":")))
    (output / "tactical-dataset.json").write_text(json.dumps(rows, separators=(",", ":")))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sources", nargs="+", default=["data/local", "reports"])
    args = parser.parse_args()
    print(json.dumps(train_tactical(args.output, args.sources)["model_path"]))
