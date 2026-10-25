"""All-attempt hosted benchmarks, with pre-reset history explicitly separated."""

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .config import state_root
from .evaluation_data import stats
from .store import list_runs


@lru_cache(maxsize=8)
def archived_runs(folder):
    result = []
    for path in Path(folder).glob("runs/*.json"):
        run = json.loads(path.read_text())
        if run.get("mode") == "hosted":
            result.append(
                {
                    k: run[k]
                    for k in (
                        "id",
                        "case_id",
                        "agent",
                        "model",
                        "prompt",
                        "created_at",
                        "status",
                        "result",
                        "metrics",
                        "error",
                        "session_id",
                    )
                    if k in run
                }
            )
    return result


def hosted_benchmarks(include_archive=False):
    runs = [r for r in list_runs(limit=10000) if r["mode"] == "hosted"]
    historical = []
    marker = state_root() / "history-reset.json"
    if marker.exists():
        folder = state_root() / "history-archive" / Path(json.loads(marker.read_text())["archive"]).name
        historical = archived_runs(str(folder))
    selected = historical if include_archive else runs
    grouped = defaultdict(list)
    for run in selected:
        prompt = run.get("prompt", {})
        key = (run["case_id"], run["agent"], run.get("model", "unrecorded"), prompt.get("system_sha256", "unrecorded"))
        grouped[key].append(run)
    rows = [
        {
            "case_id": key[0],
            "agent": key[1],
            "model": key[2],
            "prompt_hash": key[3],
            "summary": stats(items),
            "run_ids": [r["id"] for r in items],
        }
        for key, items in sorted(grouped.items())
    ]
    return {
        "scope": "historical" if include_archive else "current",
        "historical_attempts": len(historical),
        "current_attempts": len(runs),
        "cases": len({r["case_id"] for r in selected}),
        "summary": stats(selected),
        "rows": rows,
        "attempts": selected,
        "selection": (
            "All attempts, grouped by case, buyer agent, model and recorded prompt hash. Mean and best are separate."
        ),
        "limitations": (
            "Historical runs used earlier policies and unequal repeat counts. They do not measure "
            "the current prompt. Local synthetic scores are not directly comparable."
        ),
        "available_data": [
            "Case briefing, supplier opening and full email transcript",
            "Binding offers, accepted terms and supplier status",
            "Arena score, deal/no-deal, must-have checks",
            "Opening, deal, alternative and best-attainable costs; cost breakdown",
            "Claim checks, returned attachments, session and replay identifiers",
            "Client-recorded model, tokens, runtime and decision audit",
        ],
        "unavailable_data": (
            "Hidden supplier prompts, reservation prices and internal decision state are not "
            "supplied. Post-run best cost is not a live offer."
        ),
    }
