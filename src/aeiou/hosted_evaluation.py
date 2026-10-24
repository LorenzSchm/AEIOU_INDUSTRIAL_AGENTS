"""Compare observed hosted practice outcomes without exposing post-run costs to the agent."""

from collections import defaultdict
from statistics import mean

from .store import list_runs


def hosted_evaluation():
    groups = defaultdict(lambda: {"claude": [], "baseline": []})
    for run in list_runs():
        if run["mode"] == "hosted" and run["agent"] in {"claude", "baseline"}:
            groups[run["case_id"]][run["agent"]].append(run)
    rows = []
    for case_id, agents in sorted(groups.items()):
        selected = {}
        for name, runs in agents.items():
            completed = [r for r in runs if r["status"] == "completed" and "score" in r.get("result", {})]
            selected[name] = max(completed, key=lambda r: r["result"]["score"], default=None)
        a, b = selected["claude"], selected["baseline"]
        rows.append(
            {
                "case_id": case_id,
                "score": a["result"]["score"] if a else None,
                "baseline_score": b["result"]["score"] if b else None,
                "delta": a["result"]["score"] - b["result"]["score"] if a and b else None,
                "run_id": a["id"] if a else None,
                "baseline_run_id": b["id"] if b else None,
                "deal": a["result"].get("deal") if a else None,
                "must_haves_ok": a["result"].get("must_haves_ok") if a else None,
                "claude_attempts": len(agents["claude"]),
                "baseline_attempts": len(agents["baseline"]),
                "claude_failures": sum(r["status"] == "failed" for r in agents["claude"]),
            }
        )
    paired = [r for r in rows if r["delta"] is not None]
    return {
        "scope": "hosted practice",
        "selection": "Best observed score per agent per case; attempts may differ.",
        "limitation": "Adaptive practice results, not a blinded or statistically conclusive final evaluation.",
        "cases": len(rows),
        "paired_cases": len(paired),
        "mean_score": mean(r["score"] for r in paired) if paired else None,
        "baseline_score": mean(r["baseline_score"] for r in paired) if paired else None,
        "wins": sum(r["delta"] > 0.00001 for r in paired),
        "ties": sum(abs(r["delta"]) <= 0.00001 for r in paired),
        "losses": sum(r["delta"] < -0.00001 for r in paired),
        "all_cases_beat_baseline": bool(paired)
        and len(paired) == len(rows)
        and all(r["delta"] > 0.00001 for r in paired),
        "rows": rows,
    }
