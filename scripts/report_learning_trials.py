"""Read-only official verification of the fixed P10/P18 experiment."""

import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from aeiou.custom_model import CustomResponseModel
from aeiou.eval_suite import sandbox_client


def main():
    folder = Path("reports/p10-p18-target72-v4")
    ledger = json.loads((folder / "trials.json").read_text())
    freeze = json.loads((folder / "freeze.json").read_text())
    trials = ledger["trials"]
    if len(trials) != 12 or any(t["status"] == "reserved" for t in trials):
        raise ValueError("Wait for all twelve authorized trials to finish")
    model = CustomResponseModel(freeze["model_path"])
    dataset = json.loads((model.path.parent / "tactical-dataset.json").read_text())
    trained_sessions = {r["session_id"] for r in dataset}
    validation = [t for t in trials if t["phase"] == "validation"]
    post_training = [t for t in trials if t["phase"] in {"validation", "diagnostic"}]
    if trained_sessions & {t["session_id"] for t in post_training}:
        raise ValueError("Post-training sessions leaked into the frozen training corpus")
    for t in validation:
        if t["candidate_fingerprint"] != freeze["candidates"][t["case_id"]]:
            raise ValueError("Validation configuration differs from the frozen candidate")
    client = sandbox_client()
    verified = []
    try:
        for t in trials:
            remote = client.session(t["session_id"])
            result = remote.get("result") or {}
            if remote.get("status") != "ended" or not isinstance(result.get("score"), (int, float)):
                raise ValueError(f"Official result unavailable for {t['trial']}")
            score = result["score"] * 100
            if t.get("score") is None or abs(t["score"] - score) > 1e-8:
                raise ValueError(f"Ledger/official score mismatch for {t['trial']}")
            verified.append(
                {
                    "trial": t["trial"],
                    "case_id": t["case_id"],
                    "phase": t["phase"],
                    "run_id": t["run_id"],
                    "session_id": t["session_id"],
                    "score": score,
                    "deal": result.get("deal"),
                    "must_haves_ok": result.get("must_haves_ok"),
                    "terms": result.get("terms"),
                    "c_deal": result.get("c_deal"),
                    "target_met": score > 72 and result.get("deal") is True and result.get("must_haves_ok") is True,
                    "status": remote["status"],
                }
            )
        account = client.me()
    finally:
        client.http.close()
    summary = {}
    for cid in ("case:P10", "case:P18"):
        all_rows = [r for r in verified if r["case_id"] == cid]
        rows = [r for r in all_rows if r["phase"] == "validation"]
        diagnostics = [r for r in all_rows if r["phase"] == "diagnostic"]
        expected = (3, 0) if cid == "case:P10" else (2, 1)
        if len(all_rows) != 6 or (len(rows), len(diagnostics)) != expected:
            raise ValueError("Unexpected case allocation")
        summary[cid] = {
            "attempts": len(all_rows),
            "all_attempt_mean": mean(r["score"] for r in all_rows),
            "all_attempt_deals": sum(r["deal"] is True for r in all_rows),
            "validation_attempts": len(rows),
            "validation_scores": [r["score"] for r in rows],
            "validation_mean": mean(r["score"] for r in rows),
            "validation_min": min(r["score"] for r in rows),
            "validation_max": max(r["score"] for r in rows),
            "validation_deals": sum(r["deal"] is True for r in rows),
            "validation_target_hits": sum(r["target_met"] for r in rows),
            "diagnostic_scores": [r["score"] for r in diagnostics],
            "diagnostic_target_hits": sum(r["target_met"] for r in diagnostics),
            "best_new_score": max(r["score"] for r in all_rows),
        }
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "authorized_sessions_used": len(trials),
        "model_sha256": model.sha256,
        "training_validation_overlap": [],
        "frozen_configurations_match": True,
        "by_case": summary,
        "official_results": verified,
        "both_cases_exceeded_72": all(s["validation_target_hits"] > 0 for s in summary.values()),
        "causal_support_benefit_proven": False,
        "limitations": [
            "Three P10 and two P18 frozen validation sessions give limited precision.",
            "The final P18 slot became a changed-strategy keyword diagnostic; it is not frozen validation.",
            "The diagnostic changes package structure, concessions and source cues together; effects are not isolated.",
            "The single development baseline/advisory pair does not isolate a causal benefit.",
            "Historical prediction error and next-quote gains are not negotiation-score improvements.",
            "No-deal results, including policy-stop cleanup, remain in the denominator.",
        ],
        "postflight": {k: account[k] for k in ("practice", "best") if k in account},
    }
    (folder / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"both_cases_exceeded_72": report["both_cases_exceeded_72"], "by_case": summary}, indent=2))


if __name__ == "__main__":
    main()
