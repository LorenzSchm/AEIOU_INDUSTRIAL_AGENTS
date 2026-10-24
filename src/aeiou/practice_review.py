"""Offline audit of saved Arena results and deterministic historical-prefix replay.

Never connects to the Arena or a language provider. Replayed actions do NOT
predict a new score: the supplier's response to a changed action is unknown.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
from statistics import mean

from .optimizer import evaluate
from .research import package
from .strategic import StrategicNegotiator


def sheets(message):
    return [s["terms"] for s in message.get("term_sheets", [])]


def audit(folder, replay=False):
    rows, unscored = [], []
    for path in sorted(Path(folder).glob("P*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if not run.get("case"):
            continue
        if run.get("result", {}).get("score") is None:
            unscored.append({"case_id": run.get("case_id"), "status": run.get("status"), "error": run.get("error")})
            continue
        result = run["result"]
        agent = StrategicNegotiator(package())
        agent.prepare(run["case"])
        final = [
            r
            for r in evaluate(run["case"], run["threads"], agent.intelligence)
            if r["supplier_id"] == result.get("supplier_id")
        ]
        traces = [
            s["planner"]
            for d in run.get("decisions", [])
            for s in d.get("decision_support", {}).values()
            if s.get("planner")
        ]
        row = {
            "file": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "case_id": run["case_id"],
            "deal": result.get("deal"),
            "score": result["score"] * 100,
            "actual_savings_eur": result["c_open"] - result["c_deal"] if result.get("c_deal") is not None else None,
            "benchmark_gap_eur": result["c_deal"] - result["c_best"] if result.get("c_deal") is not None else None,
            "actual_cost_breakdown": result.get("cost_breakdown"),
            "production_estimated_final_cost": min((r["cost"] for r in final), default=None),
            "actual_final_cost": result["c_deal"],
            "proposals_submitted": sum(len(t.get("submitted", [])) for t in traces),
            "proposals_admitted": sum(len(t.get("accepted", [])) for t in traces),
            "proposals_selected": sum(bool(t.get("selected")) for t in traces),
            "proposal_rejections": [reason for t in traces for reason in t.get("rejected", [])],
            "suppliers_contacted": sum(
                any(m.get("from") == "buyer" for m in events) for events in run["threads"].values()
            ),
            "suppliers_available": len(run["threads"]),
            "repeated_buyer_portfolios": 0,
            "replay": [],
        }
        for sid, events in run["threads"].items():
            previous = None
            for i, message in enumerate(events):
                if message.get("from") != "buyer" or not sheets(message):
                    continue
                signature = sorted(json.dumps(t, sort_keys=True) for t in sheets(message))
                row["repeated_buyer_portfolios"] += signature == previous
                previous = signature
                if not replay or len(run["threads"]) != 1:
                    continue
                # Only the prefix visible at decision time enters the agent.
                # No result, benchmark, later messages or other team's winner.
                candidate = StrategicNegotiator(package())
                decision = candidate.decide(copy.deepcopy(run["case"]), {sid: copy.deepcopy(events[:i])})
                row["replay"].append(
                    {
                        "message_index": i,
                        "observed_action": sheets(message),
                        "revised_policy_action": sheets(decision["message"]),
                        "revised_action_type": decision["message"].get("action", {}).get("type", "send"),
                    }
                )
        rows.append(row)
    return {
        "rows": rows,
        "unscored_runs": unscored,
        "scored_runs": len(rows),
        "mean_score": mean(r["score"] for r in rows) if rows else None,
        "arena_sessions_used": 0,
        "llm_calls": 0,
        "limitation": "Historical observations and code-only prefix replay; no new supplier replies or Arena scores. "
        "Older traces omit rejected proposal values, so those proposals cannot be replayed exactly.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    report = audit(args.folder, args.replay)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
