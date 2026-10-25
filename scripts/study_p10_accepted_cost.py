"""P10 final-cost prediction conditional on agreement; never an acceptance probability."""

import argparse
import json
from pathlib import Path
from statistics import mean

from aeiou.custom_model import fit, metrics, portable, predict_one, select_model, split_sessions
from aeiou.custom_model_data import gather
from aeiou.research import package
from aeiou.terminal_learning import terminal_rows


def study(output):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=False)
    runs, _ = gather(["data/local", "reports"], package().cases, case_ids=("case:P10",))
    rows = []
    for run in runs.values():
        part, _ = terminal_rows(run)
        rows.extend(r for r in part if r["y"]["terminal_deal"] == 1)
    splits, audit = split_sessions(rows, runs, case_ids=("case:P10",))
    train, valid, test = (splits[k] for k in ("train", "validation", "test"))
    target = "terminal_gain"
    selected = select_model(train, valid, target)
    prior = {**selected, "selection": {"method": "prior", "strength": 0}}
    vm = metrics(valid, [predict_one(selected, r) for r in valid], target)
    vb = metrics(valid, [predict_one(prior, r) for r in valid], target)
    evaluated = portable(fit(train + valid, target), selected["selection"])
    test_prior = {**evaluated, "selection": {"method": "prior", "strength": 0}}
    tm = metrics(test, [predict_one(evaluated, r) for r in test], target)
    tb = metrics(test, [predict_one(test_prior, r) for r in test], target)
    unique = {r["session_id"]: r for r in train + valid}
    recent = sorted(unique.values(), key=lambda r: (r["created_at"], r["session_id"]))[-5:]
    recent_value = mean(r["y"][target] for r in recent)
    recent_metrics = metrics(test, [recent_value] * len(test), target)
    passed = (
        selected["selection"]["method"] not in {"prior", "history"}
        and vm["macro_mse"] < 0.95 * vb["macro_mse"]
        and tm["macro_mse"] < tb["macro_mse"]
        and tm["macro_mae"] <= tb["macro_mae"]
        and tm["macro_mse"] < recent_metrics["macro_mse"]
        and tm["macro_mae"] <= recent_metrics["macro_mae"]
    )
    report = {
        "case_id": "case:P10",
        "conditioning": "Observed valid agreement under historical continuation policy",
        "selection": selected["selection"],
        "validation": vm,
        "validation_prior": vb,
        "test": tm,
        "test_prior": tb,
        "test_recent_five_baseline": recent_metrics,
        "eligible_for_prospective_test": passed,
        "split_audit": audit,
        "runtime_enabled": False,
        "limitation": "Accepted-only selection bias. No estimate of agreement likelihood or causal strategy effect.",
    }
    for name, data in (("report", report), ("dataset", rows), ("evaluation-model", evaluated)):
        (folder / (name + ".json")).write_text(json.dumps(data, indent=2))
    final = {
        "case_id": "case:P10",
        "eligible": passed,
        "model": portable(fit(rows, target), selected["selection"]),
        "feature_min": [min(r["v"][i] for r in rows) for i in range(len(rows[0]["v"]))],
        "feature_max": [max(r["v"][i] for r in rows) for i in range(len(rows[0]["v"]))],
        "validation_mae": vm["macro_mae"],
        "test_mae": tm["macro_mae"],
        "training_sessions": sorted({r["session_id"] for r in rows}),
        "limitation": report["limitation"],
    }
    (folder / "conditional-model.json").write_text(json.dumps(final, separators=(",", ":")))
    print(json.dumps({k: v for k, v in report.items() if k != "split_audit"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    study(parser.parse_args().output)
