"""Hosted practice contracts: use mock HTTP and mock buyers, never real quota or scores."""

import copy
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from arena_kit import ArenaClient
from arena_kit.client import ArenaError
from assessment_helpers import assessment_fields
from fastapi.testclient import TestClient

from aeiou import arena_sessions, cli, eval_suite, service
from aeiou.store import get_run, new_run, save_run

BASE = json.loads((Path(__file__).parent / "fixtures/sandbox-cases.json").read_text())[0]
WEB = TestClient(service.app)


@pytest.fixture
def arena(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    monkeypatch.setenv("ARENA_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(service, "JOBS", {})
    monkeypatch.setattr(eval_suite, "ACTIVE_EVALUATIONS", {})
    monkeypatch.setattr(arena_sessions, "NEXT_CAPACITY_CHECK", 0.0)
    cases = [{**copy.deepcopy(BASE), "case_id": f"case:P{i:02}", "hosted": True} for i in range(1, 11)]
    state = {"sessions": {}, "left": 100, "systems": [], "closed": []}
    lock = threading.Lock()

    def handler(request):
        with lock:
            path = request.url.path
            if path == "/v1/cases":
                assert request.url.params["set"] == "practice"
                return httpx.Response(200, json=cases + [{**cases[0], "case_id": "case:P99", "hosted": False}])
            if path == "/v1/me":
                return httpx.Response(200, json={"practice": {"left": state["left"], "limit": 200, "used": 100}})
            if path == "/v1/sessions":
                if request.method == "GET":
                    return httpx.Response(200, json=[
                        {"session_id": sid, "status": s["status"]} for sid, s in state["sessions"].items()
                        if request.url.params["set"] == "practice"
                    ])
                case_id = json.loads(request.content)["case_id"]
                case = next(c for c in cases if c["case_id"] == case_id)
                sid = f"test-{len(state['sessions'])}"
                state["sessions"][sid] = {"status": "open", "score": len(state["sessions"]) / 100}
                state["left"] -= 1
                terms = {k: v[0] for k, v in case["term_options"].items()}
                terms["unit_price"] = 45
                return httpx.Response(200, json={"session_id": sid, "case": case, "threads": {
                    supplier: [{"from": supplier, "status": "final_offer", "attachments": [],
                                "term_sheets": [{"term_sheet_id": f"offer:{sid}", "terms": terms}]}]
                    for supplier in case["suppliers"]}})
            sid = path.split("/")[3]
            session = state["sessions"][sid]
            if path.endswith("/walk_away"):
                session["status"] = "ended"
                state["closed"].append(sid)
                return httpx.Response(200, json={"deal": False, "score": 0})
            if request.method == "GET":
                return httpx.Response(200, json={"status": session["status"]})
            assert path.endswith("/messages")
            action = json.loads(request.content)["message"]["action"]
            assert action == {"type": "accept", "term_sheet_id": f"offer:{sid}"}
            session["status"] = "ended"
            return httpx.Response(200, json={"result": {"score": session["score"], "deal": True}})

    def factory():
        return ArenaClient(base_url="https://arena.test", transport=httpx.MockTransport(handler), rate=100000)

    class Buyer:
        calls = errors = input_tokens = output_tokens = 0

        def __init__(self, **kwargs):
            self.model = kwargs["model"]

        def complete(self, system, user):
            state["systems"].append(system)
            self.calls += 1
            payload = json.loads(user)
            suppliers = payload["protocol_state"]["suppliers"]
            supplier, info = next(iter(suppliers.items()))
            return json.dumps({"supplier_id": supplier, "action": "accept",
                               "term_sheet_id": info["eligible_offer_ids"][0],
                               **assessment_fields(payload, supplier)})

    monkeypatch.setattr(eval_suite, "sandbox_client", factory)
    return factory, Buyer, state


def test_practice_ui_api_uses_exact_prompt_and_official_scores(arena, monkeypatch):
    factory, buyer, state = arena
    tasks = []

    class Pool:
        def submit(self, *args):
            tasks.append(args)

    monkeypatch.setattr(service, "POOL", Pool())
    original = eval_suite.run_evaluation
    monkeypatch.setattr(eval_suite, "run_evaluation", lambda report: original(report, buyer, factory))
    catalog = WEB.get("/api/eval-suite/practice/catalog").json()
    assert len(catalog["cases"]) == 10
    prompt = "  The exact edited practice prompt.\nNo hidden offer requests.\n"
    response = WEB.post("/api/eval-suite/practice", json={"prompt": prompt})
    assert response.status_code == 202
    assert WEB.post("/api/eval-suite/practice", json={"prompt": "Other"}).status_code == 409
    fn, job_id, work = tasks[0]
    fn(job_id, work)
    data = WEB.get("/api/eval-suite").json()
    report = data["practice"]["latest"]
    assert data["latest"] is None  # Sandbox results stay separate.
    assert report["status"] == "completed"
    assert report["repeats"] == 1
    assert report["completed"] == report["practice_sessions_used"] == len(state["sessions"]) == 10
    assert report["mean_score"] == pytest.approx(0.045)  # Includes the completed zero.
    assert report["prompt_text"] == prompt
    assert report["prompt"]["required_repeats"] == 0
    assert not report["prompt"]["enforce_reference_tactics"]
    assert all(s.startswith(prompt) and "Please remind me of your offers" not in s for s in state["systems"])
    assert all(get_run(row["run_id"])["case_set"] == "practice" for row in report["rows"])


def test_invalid_plan_and_quota_never_open_sessions(arena, monkeypatch):
    _, _, state = arena
    for payload in [{"prompt": " "}, {"prompt": "x", "repeats": 0},
                    {"prompt": "x", "case_ids": []}, {"prompt": "x", "case_ids": ["case:P99"]},
                    {"prompt": "x", "case_ids": ["case:P01", "case:P01"]}]:
        assert WEB.post("/api/eval-suite/practice", json=payload).status_code in (409, 422)
    state["left"] = 9
    assert WEB.post("/api/eval-suite/practice", json={"prompt": "x"}).status_code == 409
    assert not state["sessions"]


def test_practice_schedules_only_six_and_queues_remaining_four(arena):
    factory, buyer, state = arena
    entered, release = threading.Barrier(7), threading.Event()
    counter_lock, count = threading.Lock(), 0

    class PausedBuyer(buyer):
        def complete(self, system, user):
            nonlocal count
            with counter_lock:
                count += 1
                first_six = count <= 6
            if first_six:
                entered.wait(15)
                assert release.wait(15)
            return super().complete(system, user)

    report = eval_suite.prepare_evaluation("Practice", variant_id="practice", repeats=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(eval_suite.run_evaluation, report, PausedBuyer, factory)
        try:
            entered.wait(15)
            assert len(state["sessions"]) == 6
            assert sum(row["status"] == "queued" for row in report["rows"]) == 4
        finally:
            release.set()
    assert future.result()["completed"] == 10


@pytest.mark.parametrize("racing", [False, True])
def test_full_arena_or_capacity_race_waits_and_can_be_stopped(monkeypatch, racing):
    monkeypatch.setattr(arena_sessions, "NEXT_CAPACITY_CHECK", 0.0)
    cancel, waiting = threading.Event(), threading.Event()
    opened = []

    class Client:
        def sessions(self, kind):
            return [{"status": "open"}] * (10 if kind == "sandbox" and not racing else 0)

        def open_session(self, case_id):
            opened.append(case_id)
            raise ArenaError(429, "at most 10 open sessions per key")

    def check():
        eval_suite.check_cancelled(cancel)

    remote = arena_sessions.AdmittedClient(Client(), check, waiting.set, cancel)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(remote.open_session, "case:P01")
        assert waiting.wait(3)
        cancel.set()
        with pytest.raises(eval_suite.EvaluationCancelled):
            future.result(timeout=3)
    assert len(opened) == int(racing)


def test_abandoned_cleanup_only_closes_our_stopped_tests(arena):
    factory, _, state = arena
    for status, mode in [("cancelled", "practice"), ("failed", "sandbox"), ("running", "practice"),
                         ("completed", "practice"), ("cancelled", "final")]:
        row = new_run("case:P01", "hosted", "eval")
        row.update(status=status, case_set=mode, session_id=row["id"], evaluation_id="test")
        state["sessions"][row["id"]] = {"status": "open"}
        save_run(row)
    remote = factory()
    try:
        arena_sessions.cleanup_abandoned(remote)
        arena_sessions.cleanup_abandoned(remote)
    finally:
        remote.http.close()
    assert len(state["closed"]) == 2
    for sid in state["closed"]:
        assert get_run(sid).get("result") is None  # Cleanup never fabricates a scored run.


def test_cli_requires_explicit_prompt_or_legacy_agent(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aeiou", "practice"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_waiting_session_starts_when_remote_capacity_frees(monkeypatch):
    monkeypatch.setattr(arena_sessions, "NEXT_CAPACITY_CHECK", 0.0)
    waited = threading.Event()

    class Client:
        def sessions(self, kind):
            return [{"status": "open"}] * (10 if kind == "sandbox" and not waited.is_set() else 0)

        def open_session(self, case_id):
            assert waited.is_set()
            return {"session_id": "new-slot"}

    remote = arena_sessions.AdmittedClient(Client(), lambda: None, waited.set, threading.Event())
    assert remote.open_session("case:P01") == {"session_id": "new-slot"}


def test_quota_changed_after_submission_opens_nothing(arena):
    factory, buyer, state = arena
    report = eval_suite.prepare_evaluation("Practice", variant_id="practice", repeats=1)
    state["left"] = 1
    result = eval_suite.run_evaluation(report, buyer, factory)
    assert result["status"] == "stopped" and not state["sessions"]
    assert "quota changed" in result["stop_reason"]


def test_cli_saved_batch_preserves_prompt_and_model_with_neutral_controller(arena, monkeypatch):
    _, _, _ = arena
    source = eval_suite.prepare_evaluation("Saved prompt B\n", model="claude-opus-5-5", variant_id="b")
    captured = []

    def execute(report):
        captured.append(report)
        return {**report, "status": "completed"}

    monkeypatch.setattr(eval_suite, "run_evaluation", execute)
    monkeypatch.setattr(sys, "argv", ["aeiou", "practice", "--eval-batch", source["id"], "--max-sessions", "10"])
    cli.main()
    report = captured[0]
    assert report["prompt_text"] == source["prompt_text"]
    assert (report["model"], report["effort"], report["speed"]) == ("claude-opus-5-5", "medium", "fast")
    assert not report["prompt"]["enforce_reference_tactics"]
    assert len(report["rows"]) == 10
