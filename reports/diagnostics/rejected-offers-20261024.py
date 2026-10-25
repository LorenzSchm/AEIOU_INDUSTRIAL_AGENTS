"""Reproduce this batch's conditional score estimate from saved evidence, without API calls.

Run with `uv run python reports/diagnostics/rejected-offers-20261024.py`.
Add --attach to annotate the saved batch. The UI displays recorded scores only; official results do not change.
"""

import argparse
import json
import math
from pathlib import Path
from statistics import mean

from arena_kit.cost import closing_terms, cost_breakdown, must_haves_ok, score, total_cost
from arena_kit.docs import standard_terms

ROOT = Path(__file__).resolve().parents[2]
BATCH_ID = "558b6914917f4995984041a26b963afc"
D01 = "6b42c218620c48898ae18c38fe580e15"
D02 = "a99a1db60a734144bc0b2be64b9307c0"
CALIBRATION = "c7fac6f0820946f2b58087a8cc195271"
D02_ANCHORS = "0097cf1423184476b52e3d0d0798eada"
ASSUMPTION = (
    "Assumes both rejected offers were confirmed and accepted on unchanged terms. "
    "D01 includes a supplier-health penalty calibrated from completed runs."
)


def read(path):
    return json.loads((ROOT / path).read_text())


def run(run_id):
    return read(f"data/local/runs/{run_id}.json")


def standard(record, sid):
    events = record["threads"][sid]
    return closing_terms(record["case"], events[0]["term_sheets"][0]["terms"], standard_terms(events))


def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def main(attach):
    batch_path = ROOT / f"data/local/eval-suite/{BATCH_ID}.json"
    batch = read(batch_path)
    assert batch["status"] != "running", "Never annotate an active batch"
    calibrated = run(CALIBRATION)
    case, result = calibrated["case"], calibrated["result"]
    terms = result["terms"]
    std = standard(calibrated, result["supplier_id"])
    assert terms["payment_days"] == std["payment_days"] == 30
    assert terms["effective_date"] == "2026-12-01"
    years = terms["contract_months"] / 12
    # Invert the published quadratic relationship-cost formula at this observed deal.
    gap = math.sqrt(result["cost_breakdown"]["relationship"] * 10
                    / (case["buyer"]["relationship_eur_per_point"] * years))
    assert 0 < gap <= 5
    margin = case["buyer"]["sustainable_margin_pct"] - gap
    full_cost = terms["unit_price"] * (1 - margin / 100)
    supplier = {"standard": std, "full_cost": full_cost, "interest_rate": 0}
    # All evaluated packages pay at the supplier's standard 30 days: its unknown rate cancels.
    validations = []
    for rid in [CALIBRATION, "e828c112cc3d44a6b4cad6483d3e1d23",
                "e81849a1dd494e15b2bec8b61474cf6e", "f3a906985dc149388f54faf64ca2081d"]:
        observed = run(rid)
        assert observed["case"]["buyer"] == case["buyer"]
        actual = observed["result"]
        assert actual["terms"]["payment_days"] == 30
        predicted = cost_breakdown(observed["case"], actual["terms"], std, supplier)["relationship"]
        assert abs(predicted - actual["cost_breakdown"]["relationship"]) <= 0.01
        validations.append({"run_id": rid, "predicted_relationship": predicted,
                            "official_relationship": actual["cost_breakdown"]["relationship"]})

    d01_remote = read(f"tmp/{D01}-remote-result.json")
    d02_reference = run(D02_ANCHORS)
    other_d02 = run("38d68901fae94980b95158f457b1ca97")
    for key in ("c_open", "c_best"):
        assert other_d02["result"][key] == d02_reference["result"][key]
    estimates = []
    for row in batch["rows"]:
        rid = row["run_id"]
        if rid not in (D01, D02):
            continue
        record = run(rid)
        sid, action = next((sid, events[-1]["action"]) for sid, events in record["threads"].items()
                           if events[-1].get("action", {}).get("type") == "accept")
        offer = next(sheet for event in record["threads"][sid] if event.get("from") != "buyer"
                     for sheet in event.get("term_sheets", [])
                     if sheet["term_sheet_id"] == action["term_sheet_id"])
        std = standard(record, sid)
        assert must_haves_ok(record["case"], offer["terms"], std)
        economics = supplier if rid == D01 else None
        if rid == D02:
            assert record["case"]["buyer"] == d02_reference["case"]["buyer"]
            assert record["case"]["fixed_terms"] == d02_reference["case"]["fixed_terms"]
        anchors = d01_remote["result"] if rid == D01 else d02_reference["result"]
        cost = total_cost(record["case"], offer["terms"], std, economics)
        estimate = {
            "run_id": rid, "case_id": row["case_id"], "repeat": row["repeat"],
            "session_id": record["session_id"], "supplier_id": sid,
            "offer_id": action["term_sheet_id"], "quoted_terms": offer["terms"],
            "closing_terms": closing_terms(record["case"], offer["terms"], std),
            "cost": cost, "score": score(anchors["c_open"], cost, anchors["c_best"]),
            "c_open": anchors["c_open"], "c_best": anchors["c_best"],
            "cost_breakdown": cost_breakdown(record["case"], offer["terms"], std, economics),
            "anchor_source": record["session_id"] if rid == D01 else D02_ANCHORS,
            "assumption": ASSUMPTION,
        }
        row["counterfactual"] = estimate
        estimates.append(estimate)
    assert len(estimates) == 2
    values = [row["score"] if row["status"] == "completed" else row["counterfactual"]["score"]
              for row in batch["rows"]]
    case_means = {cid: mean(v for row, v in zip(batch["rows"], values, strict=True) if row["case_id"] == cid)
                  for cid in ("case:D01", "case:D02")}
    summary = {"mean_score": mean(values), "case_means": case_means,
               "estimated_runs": len(estimates), "assumption": ASSUMPTION}
    analysis = {
        "batch_id": BATCH_ID, "kind": "conditional_counterfactual_not_official_result",
        "recorded_mean_score": batch["mean_score"], "completed_runs": batch["completed"],
        **summary, "estimated_runs_detail": estimates,
        "d01_official_outcome": {"score": d01_remote["result"]["score"],
                                 "end_reason": d01_remote["end_reason"]},
        "calibration": {"effective_full_cost": full_cost, "source_run": CALIBRATION,
                        "assumption": "Same supplier economics across these D01 sessions; standard payment terms.",
                        "crosschecks": validations},
        "formula": "clip((C_open - C_deal) / (C_open - C_best), 0, 1)",
    }
    write(Path(__file__).with_suffix(".json"), analysis)
    if attach:
        batch["counterfactual"] = summary
        write(batch_path, batch)
    print(json.dumps({"recorded_mean": batch["mean_score"], **summary,
                      "estimates": [{k: e[k] for k in ("case_id", "repeat", "cost", "score")}
                                    for e in estimates]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attach", action="store_true")
    main(parser.parse_args().attach)
