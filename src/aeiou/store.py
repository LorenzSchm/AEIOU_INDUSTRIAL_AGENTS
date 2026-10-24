import json
import math
import os
import threading
import uuid
from datetime import UTC, datetime

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
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean(run), ensure_ascii=False, indent=2))
        os.replace(tmp, target)


def get_run(run_id):
    if not run_id.isalnum():
        raise FileNotFoundError(run_id)
    return json.loads((state_root() / "runs" / f"{run_id}.json").read_text())


def list_runs(limit=200):
    folder = state_root() / "runs"
    out = []
    local_count = 0
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = json.loads(path.read_text())
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
                )
                if k in r
            }
        )
    return out
