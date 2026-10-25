"""Offline terminal-outcome study. Censored threads are not invented no-deal labels."""

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from arena_kit.cost import must_haves_ok, total_cost
from arena_kit.docs import standard_terms

from .custom_model import fit, metrics, portable, predict_one, select_model, split_sessions
from .custom_model_data import CASES, extract, gather
from .research import package
from .tactical_features import tactic_features

TARGETS = ("terminal_deal", "terminal_gain")


def terminal_label(case, events):
    replies = [e for e in events if e.get("from") not in {"buyer", "arena"}]
    if not replies:
        return None, "empty"
    last = replies[-1]
    if last.get("status") in {"walked_away", "no_bid"}:
        return {"terminal_deal": 0, "terminal_gain": 0}, "observed_exit"
    if last.get("status") != "agreed":
        return None, "censored"
    accepts = [e for e in events if e.get("from") == "buyer" and e.get("action", {}).get("type") == "accept"]
    sheets = last.get("term_sheets", [])
    if not accepts or len(sheets) != 1:
        return None, "ambiguous_acceptance"
    std = standard_terms(events)
    terms = sheets[0]["terms"]
    if not must_haves_ok(case, terms, std):
        return None, "invalid_agreement"
    opening = total_cost(case, replies[0]["term_sheets"][0]["terms"], standard_terms(events[:1]))
    cost = total_cost(case, terms, std)
    return {"terminal_deal": 1, "terminal_gain": max(0, (opening - cost) / opening)}, "accepted"


def terminal_rows(run):
    rows, _ = extract(run)
    labels, reasons = {}, Counter()
    for sid, events in run["threads"].items():
        labels[sid], reason = terminal_label(run["case"], events)
        reasons[reason] += 1
    output = []
    for original in rows:
        label = labels[original["supplier_id"]]
        if label is None or original["previous_status"] != "open":
            continue
        row = copy.deepcopy(original)
        events = run["threads"][row["supplier_id"]]
        turn = row["turn"]
        row["v"] += tactic_features(events[:turn], events[turn])
        row["y"] = label.copy()
        # Future returns cannot be features or known history within their own session.
        row["past_targets"].update({t: {"sum": 0, "count": 0} for t in TARGETS})
        output.append(row)
    return output, reasons


def study(output, roots):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=False)
    runs, _ = gather(roots, package().cases)
    rows, exclusions = [], Counter()
    for run in runs.values():
        part, reasons = terminal_rows(run)
        rows.extend(part)
        exclusions.update(reasons)
    splits, audit = split_sessions(rows, runs)
    results, models = {}, {}
    for cid in CASES:
        results[cid], models[cid] = {}, {}
        for target in TARGETS:
            train, valid, test = (
                [r for r in splits[k] if r["case_id"] == cid] for k in ("train", "validation", "test")
            )
            chosen = select_model(train, valid, target)
            baseline = {**chosen, "selection": {"method": "prior", "strength": 0}}
            fitted = portable(fit(train + valid, target), chosen["selection"])
            prior = {**fitted, "selection": {"method": "prior", "strength": 0}}
            vm = metrics(valid, [predict_one(chosen, r) for r in valid], target)
            vb = metrics(valid, [predict_one(baseline, r) for r in valid], target)
            tm = metrics(test, [predict_one(fitted, r) for r in test], target)
            tb = metrics(test, [predict_one(prior, r) for r in test], target)
            support = {
                name: dict(Counter(str(r["y"]["terminal_deal"]) for r in {r["session_id"]: r for r in part}.values()))
                for name, part in (("train", train), ("validation", valid), ("test", test))
            }
            adequate = all(min(counts.get("0", 0), counts.get("1", 0)) >= 3 for counts in support.values())
            passed = (
                adequate
                and chosen["selection"]["method"] not in {"prior", "history"}
                and vm["macro_mse"] < vb["macro_mse"] * 0.95
                and tm["macro_mse"] < tb["macro_mse"]
                and tm["macro_mae"] <= tb["macro_mae"]
            )
            results[cid][target] = {
                "selection": chosen["selection"],
                "session_class_support": support,
                "validation": vm,
                "validation_prior": vb,
                "test": tm,
                "test_prior": tb,
                "eligible_for_prospective_test": passed,
            }
            models[cid][target] = fitted
    report = {
        "cases": results,
        "public_terminal_labels": dict(exclusions),
        "split_audit": audit,
        "limitation": (
            "Returns under historical continuation policies, not causal action values. "
            "Censored cases excluded; selection bias remains. No official score or hidden supplier state used."
        ),
        "runtime_enabled": False,
    }
    for name, data in (("report", report), ("dataset", rows), ("evaluation-models", models)):
        (folder / (name + ".json")).write_text(json.dumps(data, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sources", nargs="+", default=["data/local", "reports"])
    args = parser.parse_args()
    report = study(args.output, args.sources)
    print(json.dumps({"labels": report["public_terminal_labels"], "cases": report["cases"]}, indent=2))
