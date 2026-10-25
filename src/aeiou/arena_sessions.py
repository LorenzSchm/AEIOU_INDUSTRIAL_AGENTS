"""Admission and cleanup for owned sandbox/practice sessions, never final sessions."""

import json
import threading
import time
from datetime import UTC, datetime

from arena_kit.client import ArenaError

from .config import state_root
from .store import save_run

OPEN_LIMIT = 10
ADMISSION_LOCK = threading.Lock()
NEXT_CAPACITY_CHECK = 0.0
CLEANUP_LOCK = threading.Lock()


def open_sessions(client):
    return [s for kind in ("sandbox", "practice") for s in client.sessions(kind) if s["status"] == "open"]


class AdmittedClient:
    """Only session creation waits. Existing conversations retain their reply time budget."""

    def __init__(self, client, check, waiting, cancel):
        self.client, self.check, self.waiting, self.cancel = client, check, waiting, cancel

    def __getattr__(self, name):
        return getattr(self.client, name)

    def open_session(self, case_id):
        global NEXT_CAPACITY_CHECK
        while True:
            self.check()
            with ADMISSION_LOCK:
                self.check()
                if time.monotonic() >= NEXT_CAPACITY_CHECK:
                    if len(open_sessions(self.client)) < OPEN_LIMIT:
                        try:
                            return self.client.open_session(case_id)
                        except ArenaError as exc:
                            # An external runner can consume a slot between the check and POST.
                            if exc.status != 429 or "open sessions" not in str(exc.detail).lower():
                                raise
                    NEXT_CAPACITY_CHECK = time.monotonic() + 2
            self.waiting()
            self.cancel.wait(0.5)


def close_owned_session(client, run):
    """Cancel an abandoned test at the API level; never count that as a negotiated result."""
    if not run.get("session_id") or run.get("case_set") not in ("sandbox", "practice"):
        return
    cleanup = {"requested_at": datetime.now(UTC).isoformat(), "reason": "evaluation_stopped_or_failed"}
    try:
        state = client.session(run["session_id"])
        if state.get("status") != "ended":
            client.walk_away(run["session_id"])
            state = client.session(run["session_id"])
        cleanup.update(status=state.get("status"), result=state.get("result"))
        if state.get("status") != "ended":
            cleanup["error"] = "Arena did not confirm closure; the session may still occupy a slot"
    except Exception as exc:
        # Do not log raw HTTP exceptions here: URLs may contain sensitive query parameters.
        cleanup.update(status="unconfirmed", error=type(exc).__name__)
    run["remote_cleanup"] = cleanup
    run.setdefault("termination", {})["remote_session_may_remain_open"] = cleanup.get("status") != "ended"
    save_run(run)


def cleanup_abandoned(client):
    """Reconcile only stopped/failed eval sessions owned by this workbench."""
    with CLEANUP_LOCK:
        for path in (state_root() / "runs").glob("*.json"):
            run = json.loads(path.read_text())
            if (run.get("evaluation_id") and run.get("status") in ("cancelled", "failed")
                    and run.get("remote_cleanup", {}).get("status") != "ended"):
                close_owned_session(client, run)
