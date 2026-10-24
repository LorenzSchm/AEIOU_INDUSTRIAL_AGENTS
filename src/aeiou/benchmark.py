import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from statistics import mean

from .config import ROOT
from .research import package
from .simulation import OPPONENTS, local_cases, local_play
from .store import clean


def benchmark(agent="adaptive", seeds=3, label="local", seed_start=0, parallel=1):
    logging.getLogger("arena").setLevel(logging.WARNING)
    pkg, rows = package(), []

    def one(task):
        opponent, case_id, seed = task
        baseline = local_play(case_id, "baseline", opponent, seed, pkg=pkg)
        challenger = local_play(case_id, agent, opponent, seed, pkg=pkg)
        b, c = baseline.get("result", {}), challenger.get("result", {})
        return {
            "case_id": case_id,
            "opponent": opponent,
            "seed": seed,
            "baseline_score": b.get("score", 0),
            "score": c.get("score", 0),
            "baseline_cost": b.get("c_deal") or b.get("c_alt"),
            "cost": c.get("c_deal") or c.get("c_alt"),
            "deal": c.get("deal", False),
            "must_haves_ok": c.get("must_haves_ok"),
            "status": challenger["status"],
            "run_id": challenger["id"],
            "baseline_run_id": baseline["id"],
            "metrics": challenger.get("metrics", {}),
        }

    tasks = [
        (opponent, case_id, seed)
        for opponent in OPPONENTS
        for case_id in local_cases()
        for seed in range(seed_start, seed_start + seeds)
    ]
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 6))) as pool:
        rows = list(pool.map(one, tasks))
    groups = defaultdict(list)
    for row in rows:
        groups[row["opponent"]].append(row)
    opponents = []
    for opponent, values in groups.items():
        base, tested = mean(r["baseline_score"] for r in values), mean(r["score"] for r in values)
        opponents.append(
            {
                "id": opponent,
                **OPPONENTS[opponent],
                "baseline_score": round(base, 4),
                "score": round(tested, 4),
                "delta": round(tested - base, 4),
                "wins": sum(r["score"] > r["baseline_score"] + 0.00001 for r in values),
                "ties": sum(abs(r["score"] - r["baseline_score"]) <= 0.00001 for r in values),
                "losses": sum(r["score"] < r["baseline_score"] - 0.00001 for r in values),
                "beaten": tested > base + 0.00001,
                "sessions": len(values),
            }
        )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "agent": agent,
        "label": label,
        "scope": "local synthetic stress suite + unmodified upstream contract simulator",
        "limitation": "Does not establish performance against hosted suppliers or hidden final agents.",
        "seed_start": seed_start,
        "seeds": seeds,
        "matches": len(rows),
        "mean_score": mean(r["score"] for r in rows),
        "baseline_score": mean(r["baseline_score"] for r in rows),
        "all_opponents_beaten": all(r["beaten"] for r in opponents),
        "failures": sum(r["status"] != "completed" for r in rows),
        "must_have_violations": sum(r["deal"] and r["must_haves_ok"] is False for r in rows),
        "llm_calls": sum(r["metrics"].get("calls", 0) for r in rows),
        "llm_errors": sum(r["metrics"].get("errors", 0) for r in rows),
        "fallbacks": sum(r["metrics"].get("fallbacks", 0) for r in rows),
        "opponents": opponents,
        "rows": rows,
    }
    folder = ROOT / "reports"
    folder.mkdir(exist_ok=True)
    filename = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{label}.json"
    (folder / filename).write_text(json.dumps(clean(report), indent=2))
    (folder / "latest.json").write_text(json.dumps(clean(report), indent=2))
    return {k: v for k, v in report.items() if k != "rows"}
