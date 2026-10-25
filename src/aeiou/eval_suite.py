"""Prompt evaluations against Tacto's hosted sandbox/practice suppliers and official scores."""

import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
from datetime import UTC, datetime
from statistics import mean

from arena_kit import ArenaClient
from arena_kit.play import hosted, play

from .action_policy import NegotiationBlocked
from .agent import BUYER_DECISION_BUDGET_S, make_backend
from .arena_sessions import AdmittedClient, cleanup_abandoned, close_owned_session
from .config import MODELS, model_settings, provider_key, state_root
from .execution_policy import load_execution_policies, validate_policy
from .prompt_protocol import PromptLabAgent, PromptSpec
from .research import package
from .runner import RecordingTable, finish_run
from .store import clean, new_run, save_run

KIT_REVISION = "75eecd19bf42c4f4b5a93f01139fa2a757ec5c07"
SUITE_ID = "tacto-hosted-sandbox-v1"
PRACTICE_SUITE_ID = "tacto-hosted-practice-v1"
ARENA_URL = "https://arena.tacto.ai"
SOURCE_URL = f"{ARENA_URL}/faq"
REPEATS = 3
VARIANTS = {"a": "Prompt A", "b": "Prompt B"}
SANDBOX_CASES = {
    "case:D01": "Pump housing PH-100: the foundry wants +8.8 %",
    "case:D02": "New stainless pump shaft SH-220: award one of two suppliers",
}
PARALLEL_RUNS = len(SANDBOX_CASES) * REPEATS
MAX_PARALLEL_RUNS = 10
# Separate from the service's batch pool: batch workers wait for these case workers.
RUN_POOL = ThreadPoolExecutor(max_workers=MAX_PARALLEL_RUNS, thread_name_prefix="sandbox-run")
REPORT_LOCK = threading.RLock()
RATE_LOCK = threading.Lock()
NEXT_REQUEST = 0.0
ACTIVE_EVALUATIONS = {}


class EvaluationCancelled(Exception):
    pass


class EvaluationNotStarted(Exception):
    pass


def check_cancelled(cancel):
    if cancel.is_set():
        raise EvaluationCancelled("Stopped at your request")


class CancellableBackend:
    def __init__(self, backend, cancel):
        self.backend, self.cancel = backend, cancel
        if hasattr(backend, "set_cancel_event"):
            backend.set_cancel_event(cancel)

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def complete(self, *args, **kwargs):
        check_cancelled(self.cancel)
        result = self.backend.complete(*args, **kwargs)
        check_cancelled(self.cancel)
        return result

    def complete_before(self, system, user, deadline):
        check_cancelled(self.cancel)
        if hasattr(self.backend, "complete_before"):
            result = self.backend.complete_before(system, user, deadline)
        else:
            result = self.backend.complete(system, user)
        check_cancelled(self.cancel)
        return result


def stop_evaluation(evaluation_id):
    if not evaluation_id.isalnum():
        raise FileNotFoundError(evaluation_id)
    with REPORT_LOCK:
        active = ACTIVE_EVALUATIONS.get(evaluation_id)
        if active is None:
            report = json.loads((state_root() / "eval-suite" / f"{evaluation_id}.json").read_text())
            if report["status"] == "running":
                raise ValueError("This batch belongs to a different server process")
            return {"id": evaluation_id, "status": report["status"]}
        report, cancel = active
        cancel.set()
        report.update(stop_requested=True, stop_reason="Stopping at your request; waiting for in-flight requests.")
        for row in report["rows"]:
            if row["status"] == "queued":
                row["status"] = "not_started"
        save_evaluation(report)
        return {"id": evaluation_id, "status": "stopping"}


class SandboxClient(ArenaClient):
    def _pace(self):
        """One request budget across both panels, including retries and attachments."""
        global NEXT_REQUEST
        with RATE_LOCK:
            wait = NEXT_REQUEST - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            NEXT_REQUEST = time.monotonic() + 1 / 8


def validate_hosted_config():
    if not os.environ.get("ARENA_API_KEY"):
        raise ValueError("Set ARENA_API_KEY in .env for hosted evaluations")
    if os.environ.get("ARENA_URL", ARENA_URL).rstrip("/") != ARENA_URL:
        raise ValueError(f"Set ARENA_URL={ARENA_URL} for hosted evaluations")


def sandbox_client():
    validate_hosted_config()
    return SandboxClient(base_url=ARENA_URL)


def cached_practice_catalog():
    path = state_root() / "practice-catalog.json"
    return json.loads(path.read_text()) if path.exists() else {"cases": [], "quota": None}


def refresh_practice_catalog():
    client = sandbox_client()
    try:
        catalog = {
            "cases": [
                {"case_id": case["case_id"], "title": case["title"]} for case in hosted(client.cases("practice"))
            ],
            "quota": client.me().get("practice"),
            "checked_at": datetime.now(UTC).isoformat(),
        }
    finally:
        client.http.close()
    with REPORT_LOCK:
        path = state_root() / "practice-catalog.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
        temp.replace(path)
    return catalog


def error_text(exc):
    detail = str(exc)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ARENA_API_KEY"):
        if key := os.environ.get(name):
            detail = detail.replace(key, "[redacted]")
    if key := provider_key("openai"):
        detail = detail.replace(key, "[redacted]")
    return f"{type(exc).__name__}: {detail[:300]}"


def save_evaluation(report):
    with REPORT_LOCK:
        completed = [row for row in report["rows"] if row["status"] == "completed" and row["score"] is not None]
        report["completed"] = len(completed)
        report["mean_score"] = mean(row["score"] for row in completed) if completed else None
        folder = state_root() / "eval-suite"
        folder.mkdir(exist_ok=True)
        target = folder / f"{report['id']}.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(clean(report), ensure_ascii=False, indent=2))
        temp.replace(target)


def suite():
    reports = sorted((state_root() / "eval-suite").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    latest, draft_prompt, practice = {key: None for key in VARIANTS}, None, None
    for path in reports:
        report = json.loads(path.read_text())
        if draft_prompt is None:
            draft_prompt = report.get("prompt_text")
        variant = report.get("variant_id", "a")
        if report.get("suite_id") == PRACTICE_SUITE_ID:
            if practice is None or report["created_at"] > practice["created_at"]:
                practice = report
        if report.get("suite_id") == SUITE_ID and variant in VARIANTS:
            previous = latest[variant]
            if previous is None or report["created_at"] > previous["created_at"]:
                latest[variant] = report
    return {
        "suite_id": SUITE_ID,
        "source_url": SOURCE_URL,
        "kit_revision": KIT_REVISION,
        "cases": [{"case_id": case_id, "title": title} for case_id, title in SANDBOX_CASES.items()],
        "repeats": REPEATS,
        "planned_runs": len(SANDBOX_CASES) * REPEATS,
        "parallel_runs": PARALLEL_RUNS,
        "decision_budget_s": BUYER_DECISION_BUDGET_S,
        "max_parallel_runs": MAX_PARALLEL_RUNS,
        "draft_prompt": draft_prompt,
        "latest": latest["a"],
        "variants": [{"id": key, "label": label, "latest": latest[key]} for key, label in VARIANTS.items()],
        "practice": {**cached_practice_catalog(), "latest": practice, "parallel_runs": PARALLEL_RUNS},
    }


def prepare_evaluation(
    prompt, model=None, variant_id="a", comparison_id=None, *, case_ids=None, repeats=REPEATS, catalog=None, effort=None
):
    settings = model_settings(model, effort)
    if settings["model"] not in MODELS:
        raise ValueError("Unknown buyer model")
    if not prompt.strip():
        raise ValueError("Enter a prompt before running negotiations")
    if variant_id not in (*VARIANTS, "practice"):
        raise ValueError("Choose Prompt A, Prompt B or hosted practice")
    if not 1 <= repeats <= 3:
        raise ValueError("Choose between one and three repeats")
    practice = variant_id == "practice"
    choices = SANDBOX_CASES
    if practice:
        catalog = catalog if catalog is not None else refresh_practice_catalog()
        choices = {c["case_id"]: c["title"] for c in catalog["cases"]}
    selected = list(choices) if case_ids is None else case_ids
    if not selected or len(set(selected)) != len(selected) or any(cid not in choices for cid in selected):
        raise ValueError("Choose available hosted cases without duplicates")
    if practice and (catalog.get("quota") or {}).get("left", 0) < len(selected) * repeats:
        raise ValueError("Not enough practice quota for this batch; reduce cases or repeats")
    spec = PromptSpec("eval", prompt, revision="eval-prompt-v2", assessment_enabled=True)
    if spec.acceptance_floor and any(cid != spec.acceptance_floor.case_id for cid in selected):
        raise ValueError(
            "The prompt's acceptance_floor is for one case; use a shared prompt without that block "
            "for multiple cases. Saved case limits are applied separately."
        )
    saved_controls = load_execution_policies()
    execution_policies = {
        cid: validate_policy(saved_controls[cid], replace(spec, execution_policy=saved_controls[cid]).acceptance_floor)
        for cid in selected
        if cid in saved_controls
    }
    report = {
        "id": uuid.uuid4().hex,
        "suite_id": PRACTICE_SUITE_ID if practice else SUITE_ID,
        "variant_id": variant_id,
        "comparison_id": comparison_id,
        "arena_url": ARENA_URL,
        "case_set": "practice" if practice else "sandbox",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "prompt_text": spec.strategy,
        "prompt": spec.metadata(),
        "execution_policies": execution_policies,
        **settings,
        "kit_revision": KIT_REVISION,
        "arena_sessions_used": 0,
        "practice_sessions_used": 0,
        "parallel_runs": PARALLEL_RUNS,
        "decision_budget_s": BUYER_DECISION_BUDGET_S,
        "repeats": repeats,
        "quota_at_start": catalog.get("quota") if practice else None,
        "rows": [
            {"case_id": case_id, "title": choices[case_id], "repeat": repeat, "status": "queued", "score": None}
            for repeat in range(1, repeats + 1)
            for case_id in selected
        ],
    }
    with REPORT_LOCK:
        ACTIVE_EVALUATIONS[report["id"]] = report, threading.Event()
        save_evaluation(report)
    return report


class SandboxTable(RecordingTable):
    def __init__(self, client, run, report, row, cancel, stop=None):
        self.report, self.row = report, row
        self.cancel, self.stop = cancel, stop
        super().__init__(AdmittedClient(client, self.check_open, self.wait_for_capacity, cancel), run)

    def check_open(self):
        check_cancelled(self.cancel)
        if self.stop is not None and self.stop.is_set():
            raise EvaluationNotStarted("An earlier execution failed; this queued session was not opened")

    def wait_for_capacity(self):
        with REPORT_LOCK:
            if self.row["status"] != "waiting":
                self.row.update(status="waiting", wait_reason="Waiting for an available arena session")
                save_evaluation(self.report)

    def open(self, case_id):
        self.check_open()
        if case_id not in {row["case_id"] for row in self.report["rows"]}:
            raise ValueError("The case is not in this evaluation's saved plan")
        try:
            return super().open(case_id)
        finally:
            if self.session_id:
                self.run["session_id"] = self.session_id
                save_run(self.run)
                with REPORT_LOCK:
                    self.row["session_id"] = self.session_id
                    self.row["status"] = "running"
                    self.row.pop("wait_reason", None)
                    self.report["arena_sessions_used"] += 1
                    if self.report["case_set"] == "practice":
                        self.report["practice_sessions_used"] += 1
                    save_evaluation(self.report)

    def send(self, supplier_id, message):
        check_cancelled(self.cancel)
        return super().send(supplier_id, message)

    def missed(self, supplier_id, error):
        reply = super().missed(supplier_id, error)
        self.run["threads"][supplier_id].append(reply)
        save_run(self.run)
        return reply


def run_case(report, row, case, spec, pkg, client, backend_factory, stop, cancel):
    """Own one conversation; only the report and HTTP request budget are shared."""
    spec = replace(spec, execution_policy=report.get("execution_policies", {}).get(case["case_id"]))
    with REPORT_LOCK:
        if stop.is_set() or cancel.is_set():
            row["status"] = "not_started"
            save_evaluation(report)
            return
        run = new_run(row["case_id"], "hosted", "prompt-eval")
        run.update(
            model=report["model"],
            provider=report.get("provider", model_settings(report["model"])["provider"]),
            effort=report.get("effort", "low"),
            speed=report.get("speed", "standard"),
            prompt=spec.metadata(),
            supplier_kind=f"tacto-hosted-{report['case_set']}",
            case_set=report["case_set"],
            arena_url=ARENA_URL,
            evaluation_id=report["id"],
            evaluation_variant=report.get("variant_id", "a"),
            decision_budget_s=BUYER_DECISION_BUDGET_S,
        )
        row.update(status="running", title=case["title"], run_id=run["id"])
        save_run(run)
        save_evaluation(report)
    started, agent, backend = time.monotonic(), None, None
    table = SandboxTable(client, run, report, row, cancel, stop)
    try:
        backend = backend_factory(
            model=report["model"],
            max_tokens=12000,
            timeout=BUYER_DECISION_BUDGET_S,
            effort=run["effort"],
            speed=run["speed"],
        )
        agent = PromptLabAgent(pkg, CancellableBackend(backend, cancel), spec)
        # Research the public case before opening the supplier's 60-second clock.
        agent.prepare(case)
        result = play(table, row["case_id"], agent)
        score = result.get("score")
        if (
            isinstance(score, bool)
            or not isinstance(score, (float, int))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("Tacto returned no valid final score; no local score was substituted")
        finish_run(run, agent, result, started)
        with REPORT_LOCK:
            row.update(status="completed", score=score)
    except Exception as exc:
        # Let already-running conversations finish; do not open queued ones after a failure.
        stop.set()
        finish_run(run, agent, {}, started)
        cancelled = isinstance(exc, EvaluationCancelled) or cancel.is_set()
        error = "Stopped at your request" if cancelled else error_text(exc)
        status = "not_started" if isinstance(exc, EvaluationNotStarted) else "cancelled" if cancelled else "failed"
        run.update(status=status, error=error)
        with REPORT_LOCK:
            row.update(status=status, error=error)
            row.pop("wait_reason", None)
        if table.session_id:
            run["termination"] = {
                "origin": "user" if cancelled else "protocol" if isinstance(exc, NegotiationBlocked) else "execution",
                "reason": getattr(exc, "reason", error),
                "buyer_action_sent": False,
                "remote_session_may_remain_open": True,
            }
    finally:
        if run.get("status") in ("cancelled", "failed") and table.session_id:
            close_owned_session(client, run)
            if run.get("remote_cleanup", {}).get("error"):
                with REPORT_LOCK:
                    row["cleanup_error"] = "Remote closure is unconfirmed; this session may still occupy a slot"
        save_run(run)
        save_evaluation(report)
        if backend is not None and getattr(backend, "client", None) is not None:
            backend.client.close()


def run_evaluation(report, backend_factory=make_backend, client_factory=sandbox_client):
    """Launch the saved plan with at most six workers per batch and official scores only."""
    client = None
    futures, stop = [], threading.Event()
    with REPORT_LOCK:
        _, cancel = ACTIVE_EVALUATIONS.setdefault(report["id"], (report, threading.Event()))
    spec = PromptSpec(
        report["prompt"]["variant"],
        report["prompt_text"],
        revision=report["prompt"]["strategy_revision"],
        assessment_enabled=bool(report["prompt"].get("assessment_version")),
    )
    try:
        check_cancelled(cancel)
        practice = report.get("case_set") == "practice"
        if report.get("suite_id") != (PRACTICE_SUITE_ID if practice else SUITE_ID):
            raise ValueError("This evaluation is not a supported hosted batch")
        if not practice and any(row["case_id"] not in SANDBOX_CASES for row in report["rows"]):
            raise ValueError("Only sandbox cases belong in a sandbox batch")
        if spec.metadata() != report["prompt"]:
            raise ValueError("The saved controller differs from the current one; start a fresh evaluation")
        client = client_factory()
        cases = {case["case_id"]: case for case in hosted(client.cases(report["case_set"]))}
        selected = list(dict.fromkeys(row["case_id"] for row in report["rows"]))
        if any(cid not in cases or (not practice and cid not in SANDBOX_CASES) for cid in selected):
            raise ValueError("Tacto did not return the selected hosted cases; no sessions started")
        if practice and client.me().get("practice", {}).get("left", 0) < len(report["rows"]):
            raise ValueError("Practice quota changed; reduce the number of runs")
        cleanup_abandoned(client)
        check_cancelled(cancel)
        report["cases"] = [cases[case_id] for case_id in selected]
        save_evaluation(report)
        pkg = package()
        remaining = iter(report["rows"])

        def submit_next():
            row = next(remaining, None)
            if row is None:
                return None
            future = RUN_POOL.submit(
                run_case,
                report,
                row,
                cases[row["case_id"]],
                spec,
                pkg,
                client,
                backend_factory,
                stop,
                cancel,
            )
            futures.append(future)
            return future

        pending = {
            f
            for _ in range(min(PARALLEL_RUNS, report.get("parallel_runs", PARALLEL_RUNS)))
            if (f := submit_next()) is not None
        }
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    future.result()
                except Exception as exc:
                    stop.set()
                    with REPORT_LOCK:
                        report["stop_reason"] = error_text(exc)
                if not stop.is_set() and not cancel.is_set() and (next_future := submit_next()) is not None:
                    pending.add(next_future)
        for row in remaining:
            row["status"] = "not_started"
        failures = [row for row in report["rows"] if row["status"] == "failed"]
        report["status"] = "stopped" if stop.is_set() or cancel.is_set() else "completed"
        if failures:
            report["stop_reason"] = (
                f"{len(failures)} run(s) failed. {failures[0]['error']}. "
                "Other active runs finished; queued runs were not started."
            )
    except Exception as exc:
        stop.set()
        # Even setup/scheduling failures must not close a client another worker is using.
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
        report.update(status="stopped", stop_reason=f"{error_text(exc)}. Remaining runs were not started.")
        for row in report["rows"]:
            if row["status"] == "queued":
                row["status"] = "not_started"
    finally:
        if client is not None:
            client.http.close()
    with REPORT_LOCK:
        if cancel.is_set():
            report.update(status="stopped", stop_reason="Stopped at your request. Completed results were retained.")
        report["completed_at"] = datetime.now(UTC).isoformat()
        save_evaluation(report)
        ACTIVE_EVALUATIONS.pop(report["id"], None)
    return report
