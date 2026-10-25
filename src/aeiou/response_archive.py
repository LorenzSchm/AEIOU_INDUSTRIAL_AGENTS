"""Read-only export of existing hosted practice conversations. Never starts a run."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.docs import read_document

from . import config  # noqa: F401 -- load existing local credentials without exposing them


def export_existing(output, local_runs="data/local/runs"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records = output / "runs"
    records.mkdir(exist_ok=True)
    known = set()
    local_count = 0
    for path in Path(local_runs).glob("*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("mode") != "hosted" or not run.get("session_id") or not run.get("case"):
            continue
        known.add(run["session_id"])
        (records / f"{run['session_id']}.json").write_text(json.dumps(run), encoding="utf-8")
        local_count += 1
    client = ArenaClient(retries=2)
    before = client.me()
    cases = {c["case_id"]: c for c in client.cases("practice")}
    sessions = client.sessions("practice")
    missing = [r for r in sessions if r["session_id"] not in known and r.get("status") == "ended"]
    errors = []

    def fetch(row):
        sid = row["session_id"]
        destination = records / f"{sid}.json"
        if destination.exists():
            return "cached"
        if row["case_id"] not in cases:
            raise ValueError("Case absent from public practice catalogue")
        state = client.session(sid)
        threads = {}
        for supplier in state.get("threads", {}):
            events = client.thread(sid, supplier)["messages"]
            for message in events:
                for att in message.get("attachments", []):
                    if att.get("attachment_id") and "text" not in att:
                        att["text"] = read_document(att["name"], client.attachment(sid, att["attachment_id"]))
            threads[supplier] = events
        run = {
            "mode": "hosted",
            "session_id": sid,
            "case_id": row["case_id"],
            "case": cases[row["case_id"]],
            "threads": threads,
            "status": "completed",
            "result": state.get("result"),
            "provenance": "Read-only existing Arena session export; no simulation or new session",
        }
        destination.write_text(json.dumps(run), encoding="utf-8")
        return "fetched"

    counts = {"local": local_count, "fetched": 0, "cached": 0}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch, row): row for row in missing}
        for future in as_completed(futures):
            row = futures[future]
            try:
                counts[future.result()] += 1
            except Exception as exc:
                errors.append({"session_id": row["session_id"], "error_type": type(exc).__name__})
            if (counts["fetched"] + counts["cached"] + len(errors)) % 20 == 0:
                print(json.dumps({"event": "archive_progress", **counts, "errors": len(errors)}), flush=True)
    audit = {
        "counts": counts,
        "errors": errors,
        "existing_practice_sessions": len(sessions),
        "practice_before": before.get("practice"),
        "practice_after": client.me().get("practice"),
        "new_sessions_started": 0,
        "method": "GET requests only; no training uploads",
    }
    (output / "archive-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export_existing(args.output), indent=2))
