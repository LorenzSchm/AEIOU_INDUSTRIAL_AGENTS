"""Read the detached final runner's local evidence without calling the arena."""

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime

from .config import state_root


def runner_alive(pid):
    # Check the command as well as the PID: a finished runner's PID can be reused.
    if os.name != "posix":
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=1
        )
        return bool(re.search(r"(?:^|\s)(?:\S*/)?aeiou\s+final(?:\s|$)", result.stdout))
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def final_runner_status():
    root = state_root()
    manifests = sorted(root.glob("final-launch-*/launch.json"), reverse=True)
    if not manifests:
        return {"state": "not_started", "message": "No live-final runner has been started."}
    folder = manifests[0].parent
    try:
        launch = json.loads(manifests[0].read_text())
        started = datetime.fromisoformat(launch["started_at"]).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return {"state": "unknown", "message": "The final runner's launch record could not be read."}
    command = launch.get("command", [])

    def option(name):
        try:
            return int(command[command.index(name) + 1])
        except (ValueError, IndexError, TypeError):
            return None

    result = {
        "started_at": launch["started_at"],
        "model": launch.get("model_settings", {}).get("model"),
        "prompt_revision": launch.get("prompt", {}).get("strategy_revision"),
        "sunday_release": launch.get("sunday_release", False),
        "parallel": option("--parallel"),
        "waves": option("--waves"),
        "last_activity_at": None,
    }
    alive = runner_alive(launch.get("pid"))
    log = folder / "runner.log"
    try:
        modified = log.stat().st_mtime
        with log.open("rb") as source:
            source.seek(max(0, log.stat().st_size - 65536))
            tail = source.read().decode("utf-8", errors="replace")
        result["last_activity_at"] = datetime.fromtimestamp(modified, UTC).isoformat()
    except OSError:
        modified, tail = started, ""
    polls = re.findall(r'GET https?://\S+/cases\?set=final "HTTP/[^ ]+ (\d{3})', tail)
    code = int(polls[-1]) if polls else None
    finished = root / "final-results.json"
    if alive is not True and finished.exists() and finished.stat().st_mtime >= started:
        try:
            outcomes = json.loads(finished.read_text())
            failed = any(not isinstance(value, dict) for value in outcomes.values())
            state = "error" if failed else "completed"
            message = "Final runner finished with case errors." if failed else "All final waves have finished."
        except (OSError, ValueError, AttributeError):
            state, message = "unknown", "The final results could not be read."
    elif alive is False:
        state, message = "stopped", "The final runner has stopped before reporting completion."
    elif time.time() - modified > 30:
        state, message = "unknown", "No recent runner activity. The waiting state could not be confirmed."
    elif code == 403:
        state, message = "waiting", "Waiting for Arena to open the final. No final cases are available yet."
    elif code == 200:
        state, message = "running", "The runner is handling available final cases and watching for the next wave."
    elif code is not None and code >= 400:
        state, message = "error", f"Arena's latest final-case check returned HTTP {code}."
    else:
        state, message = "starting", "The final runner is starting. Waiting for its first Arena response."
    return {**result, "state": state, "message": message}
