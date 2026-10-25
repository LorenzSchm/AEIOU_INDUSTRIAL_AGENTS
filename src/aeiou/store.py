import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import state_root

LOCK = threading.RLock()


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def new_run(case_id, mode, agent):
    return {
        "id": uuid.uuid4().hex,
        "case_id": case_id,
        "mode": mode,
        "agent": agent,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "threads": {},
        "decisions": [],
    }


def save_run(run):
    folder = state_root() / "runs"
    folder.mkdir(exist_ok=True)
    with LOCK:
        target = folder / f"{run['id']}.json"
        tmp = target.with_name(f"{run['id']}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(clean(run), ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.05 * (attempt + 1))
        # Windows readers/antivirus can deny replacing an existing file. Preserve
        # the completed snapshot at a fresh name rather than aborting a live move.
        recovery = folder / "recovery"
        recovery.mkdir(exist_ok=True)
        tmp.rename(recovery / f"{run['id']}.{uuid.uuid4().hex}.json")
        logging.getLogger("aeiou").warning("Run snapshot stored in recovery after a Windows file lock")


def get_run(run_id):
    if not run_id.isalnum():
        raise FileNotFoundError(run_id)
    folder = state_root() / "runs"
    paths = [p for p in [folder / f"{run_id}.json", *(folder / "recovery").glob(f"{run_id}.*.json")] if p.exists()]
    if paths:
        return json.loads(max(paths, key=lambda p: p.stat().st_mtime_ns).read_text(encoding="utf-8"))
    # Only cached hosted replays are available from the original reset archive.
    marker = state_root() / "history-reset.json"
    if marker.exists():
        folder = state_root() / "history-archive" / Path(json.loads(marker.read_text())["archive"]).name
        archived = folder / "runs" / f"{run_id}.json"
        if archived.exists():
            run = json.loads(archived.read_text())
            if run.get("mode") == "hosted":
                return {**run, "history": {"retired": True, "reason": "Hosted history before the local reset"}}
    raise FileNotFoundError(run_id)


def list_runs(limit=200):
    folder = state_root() / "runs"
    out = []
    local_count = 0
    seen = set()
    paths = [*folder.glob("*.json"), *(folder / "recovery").glob("*.json")]
    for path in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        r = json.loads(path.read_text(encoding="utf-8"))
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        if r.get("mode") == "local":
            local_count += 1
            if local_count > limit:
                continue
        out.append(
            {
                k: r[k]
                for k in (
                    "id",
                    "case_id",
                    "mode",
                    "agent",
                    "created_at",
                    "status",
                    "result",
                    "error",
                    "metrics",
                    "opponent",
                    "model",
                    "prompt",
                    "seed",
                    "supplier_version",
                    "supplier_model",
                    "supplier_metrics",
                    "failure_origin",
                    "history",
                )
                if k in r
            }
        )
    return sorted(out, key=lambda r: (r["created_at"], r["id"]), reverse=True)
