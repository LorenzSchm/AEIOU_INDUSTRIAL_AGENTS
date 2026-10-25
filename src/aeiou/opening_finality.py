"""Offline P10 study of losing negotiation room after the first actual proposal."""

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from .custom_model import fit, metrics, portable, predict_one, select_model, split_sessions
from .custom_model_data import extract, gather
from .research import package
from .tactical_features import tactic_features

TARGET = "opening_terminal_response"
TERMINAL = {"final", "final_offer", "agreed", "walked_away", "no_bid"}


def opening_rows(run):
    candidates, _ = extract(run)
    first = {
        sid: next((i for i, e in enumerate(events) if e.get("from") == "buyer" and e.get("term_sheets")), None)
        for sid, events in run["threads"].items()
    }
    rows = []
    for original in candidates:
        sid, turn = original["supplier_id"], original["turn"]
        if turn != first[sid] or original["previous_status"] != "open":
            continue
        events = run["threads"][sid]
        reply = events[turn + 1]
        if reply.get("status") not in TERMINAL | {"open"}:
            continue
        row = copy.deepcopy(original)
        row["v"] += tactic_features(events[:turn], events[turn])
        row["y"] = {TARGET: int(reply["status"] in TERMINAL)}
        row["past_targets"][TARGET] = {"sum": 0, "count": 0}
        rows.append(row)
    return rows


def study(output):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=False)
    runs, _ = gather(["data/local", "reports"], package().cases, case_ids=("case:P10",))
    rows = [row for run in runs.values() for row in opening_rows(run)]
    splits, audit = split_sessions(rows, runs, case_ids=("case:P10",))
    train, valid, test = (splits[k] for k in ("train", "validation", "test"))
    chosen = select_model(train, valid, TARGET)
    baseline = {**chosen, "selection": {"method": "prior", "strength": 0}}
    evaluated = portable(fit(train + valid, TARGET), chosen["selection"])
    test_baseline = {**evaluated, "selection": {"method": "prior", "strength": 0}}
    vm = metrics(valid, [predict_one(chosen, r) for r in valid], TARGET)
    vb = metrics(valid, [predict_one(baseline, r) for r in valid], TARGET)
    tm = metrics(test, [predict_one(evaluated, r) for r in test], TARGET)
    tb = metrics(test, [predict_one(test_baseline, r) for r in test], TARGET)
    support = {
        name: dict(Counter(str(r["y"][TARGET]) for r in splits[name])) for name in ("train", "validation", "test")
    }
    adequate = all(min(counts.get("0", 0), counts.get("1", 0)) >= 3 for counts in support.values())
    eligible = (
        adequate
        and chosen["selection"]["method"] not in {"prior", "history"}
        and vm["macro_mse"] < 0.95 * vb["macro_mse"]
        and tm["macro_mse"] < tb["macro_mse"]
        and tm["macro_mae"] <= tb["macro_mae"]
    )
    report = {
        "case_id": "case:P10",
        "target": TARGET,
        "selection": chosen["selection"],
        "session_class_support": support,
        "adequate_class_support": adequate,
        "validation": vm,
        "validation_prior": vb,
        "test": tm,
        "test_prior": tb,
        "eligible_for_prospective_test": eligible,
        "runtime_enabled": False,
        "split_audit": audit,
        "limitation": (
            "Observational first-proposal responses only. Price, wording, evidence and chronology are confounded; "
            "no causal effect or calibrated probability claim."
        ),
    }
    final = {
        "target": TARGET,
        "eligible": eligible,
        "model": portable(fit(rows, TARGET), chosen["selection"]),
        "training_sessions": sorted({r["session_id"] for r in rows}),
    }
    for name, value in (("report", report), ("dataset", rows), ("model", final)):
        (folder / (name + ".json")).write_text(json.dumps(value, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "split_audit"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    study(parser.parse_args().output)
