"""Prepared final runtime. Inspection is offline; only explicit --start plays."""

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from arena_kit.client import MOVE_ID_HEADER, RETRY_STATUS, ArenaClient, ArenaError
from arena_kit.play import CLOSE_BEFORE_CAP_S, play_waves

from . import config
from .research import package
from .runner import RecordingTable, make_agent
from .store import new_run, save_run

log = logging.getLogger(__name__)


def retry_delay(value, attempt):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return min(10.0, 0.5 * 2**attempt)


class FinalClient(ArenaClient):
    """Shared rate limiter, >=180s reads, stable move IDs and final-only starts."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", httpx.Timeout(180.0, connect=10.0))
        super().__init__(*args, **kwargs)
        self.final_case_ids = set()

    def cases(self, set_="final"):
        if set_ != "final":
            raise ValueError("The final runner may only discover final cases")
        cases = super().cases("final")
        self.final_case_ids.update(c["case_id"] for c in cases)
        return cases

    def _call(self, method, path, body=None, params=None, raw=False):
        if path != "/cases" and path != "/sessions" and not path.startswith("/sessions/"):
            raise ValueError("Endpoint is outside final case/session execution")
        if path == "/cases" and (params or {}).get("set") != "final":
            raise ValueError("Final-only case discovery")
        if method == "POST" and path == "/sessions" and (body or {}).get("case_id") not in self.final_case_ids:
            raise ValueError("Only automatically discovered final cases may be started")
        headers = {MOVE_ID_HEADER: uuid.uuid4().hex} if method == "POST" else {}
        for attempt in range(self.retries + 1):
            self._pace()
            try:
                response = self.http.request(method, path, json=body, params=params, headers=headers)
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
                time.sleep(retry_delay(None, attempt))
                continue
            if response.status_code in RETRY_STATUS and attempt < self.retries:
                time.sleep(retry_delay(response.headers.get("Retry-After"), attempt))
                continue
            if response.status_code >= 400:
                try:
                    detail = response.json().get("detail", response.text)
                except (ValueError, AttributeError):
                    detail = response.text
                raise ArenaError(response.status_code, detail)
            return response.content if raw else response.json()
        raise RuntimeError("Final HTTP retry loop exhausted")


@contextmanager
def runner_lock(path):
    """OS-held lock releases on exit/crash; a leftover file is not a stale lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A final runner already holds the local lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def inspect_setup(agent="strategic", parallel=10, waves=5, rehearsal=False):
    """No HTTP requests, provider calls, sessions, polling or subprocess runner."""
    url = urlsplit(os.environ.get("ARENA_URL", ""))
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append("Python 3.12 is required")
    if not 1 <= parallel <= 10 or waves != (2 if rehearsal else 5):
        errors.append("Use parallel=1..10 and five final waves (two for rehearsal)")
    if rehearsal:
        if url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            errors.append("Rehearsal requires a loopback simulator URL")
    elif url.scheme != "https" or url.hostname != "arena.tacto.ai":
        errors.append("Live final requires https://arena.tacto.ai")
    if url.username or url.password or url.query or url.fragment:
        errors.append("Arena URL must not contain credentials, query or fragment")
    arena_key = bool(os.environ.get("ARENA_API_KEY"))
    model_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not arena_key:
        errors.append("ARENA_API_KEY is missing")
    if agent in {"claude", "strategic"} and not model_key:
        errors.append("ANTHROPIC_API_KEY is missing")
    pkg = package()
    if not rehearsal and not pkg.release:
        errors.append("Sunday release is missing")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, text=True).strip()
    dirty = subprocess.check_output(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "src",
            "vendor",
            "scripts",
            "pyproject.toml",
            "uv.lock",
            "data/package",
            "data/release-sunday",
        ],
        cwd=config.ROOT,
        text=True,
    ).strip()
    if dirty:
        errors.append("Runtime sources/data have uncommitted changes; commit before the final")
    report = {
        "status": "ready_for_explicit_start" if not errors else "needs_attention",
        "started": False,
        "network_requests": 0,
        "agent": agent,
        "model": config.model_name(),
        "parallel": parallel,
        "waves": waves,
        "rehearsal": rehearsal,
        "arena_host": url.hostname,
        "arena_key_present": arena_key,
        "model_key_present": model_key,
        "http_timeout_seconds": 180,
        "close_before_cap_seconds": CLOSE_BEFORE_CAP_S,
        "commit": commit,
        "facts": len(pkg.facts),
        "sunday_release_loaded": bool(pkg.release),
        "errors": errors,
    }
    if pkg.db:
        pkg.db.close()
    return report


def run_final(agent="strategic", parallel=10, waves=5, rehearsal=False):
    """Called only by the explicit start action. The kit chooses all cases/waves."""
    report = inspect_setup(agent, parallel, waves, rehearsal)
    if report["errors"]:
        raise RuntimeError("; ".join(report["errors"]))
    root = config.state_root()
    with runner_lock(config.ROOT / "data/local/final-runner.lock"):
        client = FinalClient()
        shared_pkg = None
        try:
            # Do not close another worker's sessions or consume a practice attempt.
            active = [s for s in client.sessions("practice") if s.get("status") not in {"ended", "closed"}]
            if active:
                raise RuntimeError(f"Stop practice loops and finish their sessions first: {len(active)} still open")
            shared_pkg = package()
            if agent in {"strategic", "strategic-policy"}:
                shared_pkg.evidence_index()  # Prepare once before opening any final session.
            folder = root / "final" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            handler = logging.FileHandler(folder / "runner.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logging.getLogger().addHandler(handler)
            try:

                class FinalTable(RecordingTable):
                    def __init__(self, c, case=None):
                        super().__init__(c, new_run(case["case_id"], "final", agent), case)
                        save_run(self.run)

                log.info(
                    "Final runner started: agent=%s waves=%s parallel=%s commit=%s",
                    agent,
                    waves,
                    parallel,
                    report["commit"],
                )
                while True:
                    try:
                        results = play_waves(
                            client,
                            lambda: make_agent(agent, shared_pkg),
                            waves=waves,
                            parallel=parallel,
                            table_factory=FinalTable,
                        )
                        break
                    except (httpx.TransportError, ArenaError) as exc:
                        if isinstance(exc, ArenaError) and exc.status not in RETRY_STATUS:
                            raise
                        # Same final cases resume via 409; never choose replacement cases.
                        log.warning("Wave polling interrupted (%s); resume in 5 seconds", type(exc).__name__)
                        time.sleep(5)
                output = folder / "results.json"
                output.write_text(json.dumps(results, default=str, indent=2), encoding="utf-8")
                return output
            finally:
                logging.getLogger().removeHandler(handler)
                handler.close()
        finally:
            client.http.close()
            if shared_pkg is not None and shared_pkg.db:
                shared_pkg.db.close()
