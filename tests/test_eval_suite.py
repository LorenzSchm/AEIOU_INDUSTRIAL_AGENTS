import copy
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean

import httpx
import pytest
from arena_kit import ArenaClient
from assessment_helpers import assessment_fields
from fastapi.testclient import TestClient

from aeiou import eval_suite, service
from aeiou.config import ROOT, set_model
from aeiou.decision_memory import ASSESSMENT_VERSION
from aeiou.store import get_run

client = TestClient(service.app)
CASES = json.loads((Path(__file__).parent / "fixtures/sandbox-cases.json").read_text())


class Buyer:
    calls = errors = input_tokens = output_tokens = 0

    def __init__(self, model=None, **kwargs):
        self.model = model
        self.effort, self.speed = kwargs.get("effort"), kwargs.get("speed")
        self.systems = []

    def complete(self, system, user):
        self.systems.append(system)
        self.calls += 1
        payload = json.loads(user)
        sid, state = next(iter(payload["protocol_state"]["suppliers"].items()))
        offer = state["offers"][0]
        if offer["acceptance_eligible"]:
            action = {"action": "accept", "term_sheet_id": offer["term_sheet_id"]}
        else:
            action = {"action": "send", "body": "Please confirm your terms.", "term_sheets": []}
        return json.dumps({"supplier_id": sid, **action, **assessment_fields(payload, sid)})


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """An HTTP contract fixture only; never used to report negotiation performance."""
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    monkeypatch.setenv("ARENA_URL", eval_suite.ARENA_URL)
    sessions, requests, buyers = {}, [], []
    request_lock = threading.Lock()
    scores = [0.0, 0.27, 0.4, 0.75, 1.0, 0.51]

    def reply(sid, case, n):
        terms = {k: values[0] for k, values in case["term_options"].items()}
        terms["unit_price"] = 45 if case["case_id"] == "case:D01" else 18
        return {
            "from": sid,
            "message_id": f"{sid}-{n}",
            "body": f"HTTP fixture reply {n}",
            "status": "open",
            "term_sheets": [{"term_sheet_id": f"offer:{sid}-{n}", "terms": terms}],
            "attachments": [],
        }

    def handler(request):
        requests.append(request)
        assert request.url.host == "arena.tacto.ai"
        assert request.headers["X-API-Key"] == "test-arena-key"
        if request.url.path == "/v1/cases":
            assert request.url.params["set"] == "sandbox"
            return httpx.Response(200, json=copy.deepcopy(CASES))
        if request.url.path == "/v1/sessions":
            if request.method == "GET":
                return httpx.Response(200, json=[
                    {"session_id": sid, "status": s.get("status", "open")}
                    for sid, s in sessions.items() if request.url.params["set"] == "sandbox"
                ])
            case_id = json.loads(request.content)["case_id"]
            assert case_id in eval_suite.SANDBOX_CASES
            case = next(case for case in CASES if case["case_id"] == case_id)
            session_id = f"sandbox-{len(sessions)}"
            sessions[session_id] = {"case": case, "moves": 0, "score": scores[len(sessions)]}
            return httpx.Response(
                200,
                json={
                    "session_id": session_id,
                    "case": case,
                    "threads": {sid: [reply(sid, case, 0)] for sid in case["suppliers"]},
                },
            )
        if request.method == "GET" and "/sessions/" in request.url.path:
            session = sessions[request.url.path.split("/")[3]]
            return httpx.Response(200, json={"status": session.get("status", "open")})
        if request.url.path.endswith("/walk_away"):
            sessions[request.url.path.split("/")[3]]["status"] = "ended"
            return httpx.Response(200, json={"deal": False, "score": 0.0})
        assert request.method == "POST" and request.url.path.endswith("/messages")
        _, _, _, session_id, _, sid, _ = request.url.path.split("/")
        session = sessions[session_id]
        message = json.loads(request.content)["message"]
        if message.get("action"):
            assert message["action"]["type"] == "accept"
            session["status"] = "ended"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "score": session["score"],
                        "deal": True,
                        "must_haves_ok": True,
                        "replay_url": f"/replay/{session_id}",
                    }
                },
            )
        session["moves"] += 1
        return httpx.Response(200, json=reply(sid, session["case"], session["moves"]))

    def make_client():
        def locked_handler(request):
            with request_lock:
                return handler(request)
        return ArenaClient(
            base_url=eval_suite.ARENA_URL, transport=httpx.MockTransport(locked_handler), rate=100000, retries=0
        )

    def make_buyer(**kwargs):
        buyer = Buyer(**kwargs)
        buyers.append(buyer)
        return buyer

    return make_client, make_buyer, sessions, requests, buyers, scores


def test_hosted_sessions_keep_remote_scores_and_exact_prompt(monkeypatch, sandbox):
    factory, backend, sessions, requests, buyers, scores = sandbox
    original = (ROOT / "SYSTEM_PROMPT.md").read_text()
    prompt = original + "\nUse a concise tone for this evaluation.\n"

    def forbidden(*args, **kwargs):
        raise AssertionError("Hosted eval attempted a local simulation")

    monkeypatch.setattr("arena_kit.sim.Sim.__init__", forbidden)
    report = eval_suite.run_evaluation(eval_suite.prepare_evaluation(prompt), backend, factory)
    assert report["status"] == "completed", report.get("stop_reason")
    assert report["completed"] == report["arena_sessions_used"] == len(sessions) == 6
    assert report["practice_sessions_used"] == 0
    assert sorted(row["score"] for row in report["rows"]) == sorted(scores)
    assert {row["case_id"] for row in report["rows"]} == set(eval_suite.SANDBOX_CASES)
    assert len({row["run_id"] for row in report["rows"]}) == 6
    assert {row["repeat"] for row in report["rows"]} == {1, 2, 3}
    assert buyers and all(b.systems and all(s.startswith(prompt) for s in b.systems) for b in buyers)
    assert (ROOT / "SYSTEM_PROMPT.md").read_text() == original
    for row in report["rows"]:
        run = get_run(row["run_id"])
        assert row["score"] == sessions[row["session_id"]]["score"]
        assert run["mode"] == "hosted"
        assert row["score"] == run["result"]["score"]
        assert run["supplier_kind"] == "tacto-hosted-sandbox"
        assert run["session_id"] == row["session_id"]
        assert run["threads"] and run["decisions"]
        assert run["prompt"]["assessment_version"] == ASSESSMENT_VERSION
        assert run["decisions"][0]["assessment"]["observed_message"]
        assert all("assessment" not in m for events in run["threads"].values() for m in events)
        assert any(m.get("from") == "buyer" for events in run["threads"].values() for m in events)
    assert report["mean_score"] == mean(scores)
    assert eval_suite.suite()["latest"] == report


def test_mean_includes_completed_zero_and_excludes_errors_and_unstarted(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    report = eval_suite.prepare_evaluation("Test prompt")
    assert report["mean_score"] is None
    report["rows"][0].update(status="completed", score=0.0)
    report["rows"][1].update(status="completed", score=0.8)
    report["rows"][2].update(status="failed", score=None)
    report["rows"][3].update(status="not_started", score=None)
    eval_suite.save_evaluation(report)
    assert report["mean_score"] == 0.4
    assert report["completed"] == 2


def test_eval_keeps_opus_settings_when_dashboard_model_changes(sandbox):
    factory, backend, _, _, buyers, _ = sandbox
    set_model("claude-opus-5-5")
    report = eval_suite.prepare_evaluation("Use the saved model settings.")
    set_model("claude-sonnet-5-5")
    report = eval_suite.run_evaluation(report, backend, factory)
    assert report["status"] == "completed", report.get("stop_reason")
    assert (report["model"], report["effort"], report["speed"]) == ("claude-opus-5-5", "medium", "fast")
    assert len(buyers) == 6
    assert all((b.model, b.effort, b.speed) == ("claude-opus-5-5", "medium", "fast") for b in buyers)
    for row in report["rows"]:
        run = get_run(row["run_id"])
        assert (run["model"], run["effort"], run["speed"]) == ("claude-opus-5-5", "medium", "fast")


def test_eval_keeps_openai_effort_and_provider_through_parallel_runs(sandbox):
    factory, backend, _, _, buyers, _ = sandbox
    report = eval_suite.prepare_evaluation("Same buyer strategy.", model="gpt-5.5", effort="high", variant_id="b")
    set_model("claude-sonnet-5-5")
    report = eval_suite.run_evaluation(report, backend, factory)
    assert report["status"] == "completed", report.get("stop_reason")
    assert (report["provider"], report["model"], report["effort"]) == ("openai", "gpt-5.5", "high")
    assert len(buyers) == 6
    assert all((b.model, b.effort, b.speed) == ("gpt-5.5", "high", "standard") for b in buyers)
    for row in report["rows"]:
        run = get_run(row["run_id"])
        assert (run["provider"], run["model"], run["effort"]) == ("openai", "gpt-5.5", "high")


def test_edited_prompt_controls_emails_and_acceptance_without_reference_tactics(monkeypatch, sandbox):
    factory, _, _, requests, _, _ = sandbox
    prompt = "Negotiate the complete package. Accept a satisfactory final offer. Do not request hidden fallback offers."
    email = "Thank you. Please send your revised quotation for all negotiable terms."
    report = eval_suite.prepare_evaluation(prompt, variant_id="b")
    assert report["prompt"]["required_repeats"] == 0
    assert report["prompt"]["enforce_reference_tactics"] is False

    def forbidden():
        raise AssertionError("An edited evaluation must never load another prompt's tactics")

    monkeypatch.setattr("aeiou.prompt_protocol.prompt_specs", forbidden)

    class EditedBuyer(Buyer):
        def complete(self, system, user):
            self.calls += 1
            assert system.startswith(prompt)
            assert "Please remind me of your offers" not in system
            assert "FIVE consecutive" not in system
            assert "reference_confirmation_controller" not in system
            payload = json.loads(user)
            assert payload["protocol_state"]["required_repeats"] == 0
            sid, state = next(iter(payload["protocol_state"]["suppliers"].items()))
            if self.calls == 1:
                return json.dumps({"supplier_id": sid, "action": "send", "body": email, "term_sheets": [],
                                   **assessment_fields(payload, sid)})
            assert "accept" in state["legal_actions"]
            return json.dumps({
                "supplier_id": sid, "action": "accept", "term_sheet_id": state["offers"][0]["term_sheet_id"],
                **assessment_fields(payload, sid),
            })

    result = eval_suite.run_evaluation(report, EditedBuyer, factory)
    assert result["status"] == "completed", result.get("stop_reason")
    sent = [json.loads(r.content)["message"] for r in requests if r.url.path.endswith("/messages")]
    assert len(sent) == 12  # One email then one acceptance in each of the six sessions.
    assert all(m["body"] == email for m in sent if not m.get("action"))
    for row in result["rows"]:
        run = get_run(row["run_id"])
        assert run["audit"]["verbatim_insertions"] == 0
        assert run["audit"]["accepts_before_five_repeats"] == 0
        assert not run["audit"]["verbatim_request_sent"]


def test_controller_snapshot_mismatch_stops_before_opening_sessions(sandbox):
    factory, backend, sessions, requests, _, _ = sandbox
    report = eval_suite.prepare_evaluation("Test edited prompt")
    report["prompt"]["required_repeats"] = 5
    report = eval_suite.run_evaluation(report, backend, factory)
    assert report["status"] == "stopped"
    assert "saved controller differs" in report["stop_reason"]
    assert not sessions and not requests


def test_failure_stops_before_opening_sessions(sandbox):
    factory, _, sessions, _, _, _ = sandbox

    class Unavailable:
        def __init__(self, **kwargs):
            raise RuntimeError("Provider unavailable")

    report = eval_suite.run_evaluation(eval_suite.prepare_evaluation("Test prompt"), Unavailable, factory)
    assert report["status"] == "stopped"
    assert report["completed"] == report["arena_sessions_used"] == len(sessions) == 0
    assert report["mean_score"] is None
    failed = [row for row in report["rows"] if row["status"] == "failed"]
    assert failed
    for row in failed:
        assert "Provider unavailable" in row["error"]
        assert get_run(row["run_id"])["status"] == "failed"
    assert all(row["status"] in {"failed", "not_started"} for row in report["rows"])


def test_protocol_failure_closes_remote_session_without_inventing_negotiated_score(sandbox):
    from aeiou.action_policy import NegotiationBlocked

    factory, _, sessions, requests, _, _ = sandbox

    class Blocked(Buyer):
        def complete(self, system, user):
            raise NegotiationBlocked("NO_LEGAL_ACTION")

    report = eval_suite.run_evaluation(eval_suite.prepare_evaluation("Test prompt"), Blocked, factory)
    assert report["status"] == "stopped"
    assert 1 <= len(sessions) == report["arena_sessions_used"] <= 6
    assert report["mean_score"] is None
    for row in report["rows"]:
        if row["status"] == "not_started":
            continue
        assert row["status"] == "failed" and row["score"] is None
        run = get_run(row["run_id"])
        assert not run["termination"]["remote_session_may_remain_open"]
        assert run["remote_cleanup"]["status"] == "ended"
        assert run["threads"] and row["session_id"] == run["session_id"]
    assert not any(req.url.path.endswith("/messages") for req in requests)


@pytest.mark.parametrize("score", [None, True, -0.1, 1.01])
def test_invalid_remote_score_is_never_replaced_with_a_local_score(sandbox, score):
    factory, backend, sessions, _, _, scores = sandbox
    scores[:] = [score] * 6
    report = eval_suite.run_evaluation(eval_suite.prepare_evaluation("Test prompt"), backend, factory)
    assert report["status"] == "stopped" and report["mean_score"] is None
    assert 1 <= report["arena_sessions_used"] == len(sessions) <= 6
    assert "no valid final score" in report["stop_reason"]


def test_sandbox_unavailable_does_not_fall_back(sandbox):
    _, backend, _, _, buyers, _ = sandbox
    remote = ArenaClient(
        base_url=eval_suite.ARENA_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(503, json={"detail": "Sandbox offline"})),
        retries=0,
    )
    report = eval_suite.run_evaluation(eval_suite.prepare_evaluation("Test prompt"), backend, lambda: remote)
    assert report["status"] == "stopped" and report["arena_sessions_used"] == 0
    assert all(row["status"] == "not_started" for row in report["rows"])
    assert not buyers


def test_non_sandbox_rows_cannot_start_a_session(sandbox):
    factory, backend, sessions, requests, _, _ = sandbox
    report = eval_suite.prepare_evaluation("Test prompt")
    report["rows"][0]["case_id"] = "case:P01"
    report = eval_suite.run_evaluation(report, backend, factory)
    assert report["status"] == "stopped" and not sessions and not requests


def test_legacy_scores_are_not_shown_as_sandbox_results(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    legacy = eval_suite.prepare_evaluation("Previous draft")
    legacy.pop("suite_id")
    legacy["rows"][0].update(case_id="case:S01", status="completed", score=0.99)
    eval_suite.save_evaluation(legacy)
    data = eval_suite.suite()
    assert data["latest"] is None
    assert data["draft_prompt"] == "Previous draft"
    assert data["planned_runs"] == 6


def test_eval_api_validates_and_snapshots_before_scheduling(monkeypatch, sandbox):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(service, "JOBS", {})
    queued = []

    class Pool:
        def submit(self, *args):
            queued.append(args)

    monkeypatch.setattr(service, "POOL", Pool())
    assert client.post("/api/eval-suite", json={"prompt": "   "}).status_code == 422
    assert client.post("/api/eval-suite", json={"prompt": ""}).status_code == 422
    assert client.post("/api/eval-suite", json={"prompt": "x" * 100001}).status_code == 422
    external = client.post("/api/eval-suite", json={"prompt": "Test"}, headers={"Origin": "https://example.org"})
    assert external.status_code == 403
    assert not queued
    monkeypatch.delenv("ARENA_API_KEY")
    assert client.post("/api/eval-suite", json={"prompt": "Test"}).status_code == 409
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    monkeypatch.setenv("ARENA_URL", "http://localhost:8000")
    assert client.post("/api/eval-suite", json={"prompt": "Test"}).status_code == 409
    monkeypatch.setenv("ARENA_URL", eval_suite.ARENA_URL)
    response = client.post("/api/eval-suite", json={"prompt": " Exact prompt\n"})
    assert response.status_code == 202
    data = client.get("/api/eval-suite").json()
    assert data["planned_runs"] == 6
    assert data["latest"]["id"] == response.json()["id"]
    assert data["latest"]["prompt_text"] == " Exact prompt\n"
    assert data["latest"]["mean_score"] is None
    assert len(queued) == 1
    assert client.post("/api/eval-suite", json={"prompt": "Another"}).status_code == 409
    assert len(queued) == 1
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert client.post("/api/eval-suite", json={"prompt": "Test"}).status_code == 409


def test_vendored_simulator_provenance():
    provenance = json.loads((ROOT / "vendor/arena-kit/UPSTREAM.json").read_text())
    assert provenance["revision"] == eval_suite.KIT_REVISION
    blobs = {
        "arena_kit/cost.py": "82fc8c31696fd8c9d038941e099864be708c3eb5",
        "arena_kit/sim.py": "b53f69c05840395511fa0d48759c6432525766f1",
        "arena_kit/sim_cases/S01.json": "a8b02860920d673b8996ee01c5eb577d7a7ac59f",
        "arena_kit/sim_cases/S02.json": "a524112f71f1db276010cdc86805a06dac87486e",
        "arena_kit/sim_cases/S03.json": "f5213ca2483ff7ff7da311f9c92c09f47a32cf09",
    }
    for name, expected in blobs.items():
        data = (ROOT / "vendor/arena-kit" / name).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == expected


def test_existing_batch_stays_in_a_and_b_is_independent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    original = eval_suite.prepare_evaluation("Existing prompt")
    original.pop("variant_id")  # A batch created before the two-panel UI.
    eval_suite.save_evaluation(original)
    second = eval_suite.prepare_evaluation("Different prompt", variant_id="b")
    original["rows"][0].update(status="completed", score=0.3)
    eval_suite.save_evaluation(original)  # A finishes a row after B starts.
    data = eval_suite.suite()
    assert data["latest"]["id"] == original["id"]
    a, b = data["variants"]
    assert a["latest"]["mean_score"] == 0.3
    assert b["latest"]["id"] == second["id"]
    assert b["latest"]["mean_score"] is None
    assert b["latest"]["prompt_text"] == "Different prompt"


def test_start_b_while_a_is_running_and_compare_is_all_or_nothing(monkeypatch, sandbox):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(service, "JOBS", {})
    queued = []

    class Pool:
        def submit(self, *args):
            queued.append(args)

    monkeypatch.setattr(service, "POOL", Pool())
    assert client.post("/api/eval-suite", json={"prompt": "A", "variant_id": "unknown"}).status_code == 422
    assert client.post("/api/eval-suite/compare", json={"prompt_a": "A", "prompt_b": " "}).status_code == 422
    assert not queued
    assert client.post("/api/eval-suite", json={"prompt": "A", "variant_id": "a"}).status_code == 202
    assert client.post("/api/eval-suite/compare", json={"prompt_a": "A", "prompt_b": "B"}).status_code == 409
    assert len(queued) == 1
    assert client.get("/api/eval-suite").json()["variants"][1]["latest"] is None
    assert client.post("/api/eval-suite", json={"prompt": "B", "variant_id": "b"}).status_code == 202
    assert client.post("/api/eval-suite", json={"prompt": "B again", "variant_id": "b"}).status_code == 409
    assert len(queued) == 2
    assert {job["variant_id"] for job in service.JOBS.values()} == {"a", "b"}


@pytest.mark.parametrize("fail_a", [False, True])
def test_comparison_workers_overlap_and_keep_prompts_scores_and_failures_separate(monkeypatch, sandbox, fail_a):
    factory, backend, sessions, _, buyers, scores = sandbox
    scores.extend([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(service, "JOBS", {})
    real_runner = eval_suite.run_evaluation
    entered = {key: threading.Event() for key in ["a", "b"]}
    release = threading.Event()

    def unavailable(**kwargs):
        raise RuntimeError("Test provider failure for A only")

    def run(report):
        entered[report["variant_id"]].set()
        assert release.wait(10)
        selected = unavailable if fail_a and report["variant_id"] == "a" else backend
        return real_runner(report, selected, factory)

    monkeypatch.setattr(eval_suite, "run_evaluation", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(service, "POOL", pool)
        response = client.post("/api/eval-suite/compare", json={"prompt_a": "Exact A\n", "prompt_b": "Exact B\n"})
        assert response.status_code == 202
        try:
            assert all(event.wait(10) for event in entered.values())
            active = client.get("/api/eval-suite").json()["variants"]
            assert all(variant["latest"]["status"] == "running" for variant in active)
            assert {variant["latest"]["prompt_text"] for variant in active} == {"Exact A\n", "Exact B\n"}
            assert len({variant["latest"]["comparison_id"] for variant in active}) == 1
            assert active[0]["latest"]["comparison_id"]
            assert len({variant["latest"]["model"] for variant in active}) == 1
        finally:
            release.set()
    reports = [variant["latest"] for variant in client.get("/api/eval-suite").json()["variants"]]
    assert reports[1]["status"] == "completed"
    assert reports[0]["status"] == ("stopped" if fail_a else "completed")
    run_ids = [row["run_id"] for report in reports for row in report["rows"] if row.get("run_id")]
    assert len(set(run_ids)) == len(run_ids)
    assert len(sessions) == (6 if fail_a else 12)
    for report in reports:
        completed = [row["score"] for row in report["rows"] if row["status"] == "completed"]
        assert report["mean_score"] == (mean(completed) if completed else None)
        for row in report["rows"]:
            if row.get("run_id"):
                run = get_run(row["run_id"])
                assert run["evaluation_id"] == report["id"]
                assert run["evaluation_variant"] == report["variant_id"]
    assert buyers and all(len({system.splitlines()[0] for system in buyer.systems}) == 1 for buyer in buyers)
    assert {buyer.systems[0].splitlines()[0] for buyer in buyers} == ({"Exact B"} if fail_a else {"Exact A", "Exact B"})


def test_all_six_runs_overlap_and_publish_each_result_before_batch_finishes(sandbox):
    factory, _, sessions, _, _, scores = sandbox
    entered = threading.Barrier(7)
    buyers, buyer_lock = [], threading.Lock()

    class WaitingBuyer(Buyer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.release = threading.Event()
            with buyer_lock:
                buyers.append(self)

        def complete(self, system, user):
            if not self.calls:
                entered.wait(15)
                assert self.release.wait(15)
            return super().complete(system, user)

    report = eval_suite.prepare_evaluation("Six simultaneous runs", variant_id="b")
    with ThreadPoolExecutor(max_workers=1) as batch_pool:
        future = batch_pool.submit(eval_suite.run_evaluation, report, WaitingBuyer, factory)
        try:
            entered.wait(15)  # Fails if even one run waits for another to finish.
            live = client.get("/api/eval-suite").json()["variants"][1]["latest"]
            assert len(sessions) == live["arena_sessions_used"] == 6
            assert all(row["status"] == "running" for row in live["rows"])
            assert live["completed"] == 0 and live["mean_score"] is None
            assert len({row["session_id"] for row in live["rows"]}) == 6
            assert sorted((row["case_id"], row["repeat"]) for row in live["rows"]) == [
                (case_id, repeat) for case_id in sorted(eval_suite.SANDBOX_CASES) for repeat in (1, 2, 3)
            ]
            # Release one worker out of order, keeping the other five blocked.
            buyers[-1].release.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                live = client.get("/api/eval-suite").json()["variants"][1]["latest"]
                if live["completed"]:
                    break
                time.sleep(0.01)
            assert live["completed"] == 1 and live["status"] == "running"
            completed = next(row for row in live["rows"] if row["status"] == "completed")
            assert live["mean_score"] == completed["score"] == sessions[completed["session_id"]]["score"]
            assert sum(row["status"] == "running" for row in live["rows"]) == 5
            for row in live["rows"]:
                assert client.get(f"/api/runs/{row['run_id']}").json()["session_id"] == row["session_id"]
        finally:
            for buyer in buyers:
                buyer.release.set()
    result = future.result()
    assert result["status"] == "completed" and result["completed"] == 6
    assert result["mean_score"] == mean(scores)
    assert client.get("/api/eval-suite").json()["variants"][1]["latest"] == result


def test_two_batches_share_ten_run_slots_and_finish_all_twelve(sandbox):
    factory, _, sessions, _, _, scores = sandbox
    scores.extend([0.1] * 6)
    entered = threading.Barrier(11)
    release, count_lock = threading.Event(), threading.Lock()
    count = 0

    class WaitingBuyer(Buyer):
        def complete(self, system, user):
            nonlocal count
            if not self.calls:
                with count_lock:
                    count += 1
                    first_ten = count <= 10
                if first_ten:
                    entered.wait(15)
                assert release.wait(15)
            return super().complete(system, user)

    reports = [eval_suite.prepare_evaluation(f"Prompt {v}", variant_id=v) for v in ("a", "b")]
    with ThreadPoolExecutor(max_workers=2) as batch_pool:
        futures = [batch_pool.submit(eval_suite.run_evaluation, r, WaitingBuyer, factory) for r in reports]
        try:
            entered.wait(15)
            live = client.get("/api/eval-suite").json()["variants"]
            rows = [row for variant in live for row in variant["latest"]["rows"]]
            assert count == len(sessions) == 10
            assert sum(row["status"] == "running" for row in rows) == 10
            assert sum(row["status"] == "queued" for row in rows) == 2
        finally:
            release.set()
    assert count == len(sessions) == 12
    assert all(f.result()["completed"] == 6 for f in futures)


def test_one_failed_parallel_run_does_not_abort_five_active_runs(sandbox):
    factory, _, sessions, _, _, _ = sandbox
    entered = threading.Barrier(6)
    buyer_lock, buyers = threading.Lock(), []

    class SometimesFailingBuyer(Buyer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            with buyer_lock:
                self.fail = not buyers
                buyers.append(self)

        def complete(self, system, user):
            if not self.calls:
                entered.wait(15)
                if self.fail:
                    raise RuntimeError("Only this run failed")
            return super().complete(system, user)

    result = eval_suite.run_evaluation(eval_suite.prepare_evaluation("Prompt"), SometimesFailingBuyer, factory)
    assert result["status"] == "stopped" and len(sessions) == 6
    assert result["completed"] == 5
    assert sum(row["status"] == "failed" for row in result["rows"]) == 1
    assert result["mean_score"] == mean(row["score"] for row in result["rows"] if row["score"] is not None)


def test_sandbox_clients_share_request_limit_including_retries(monkeypatch):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(eval_suite, "time", clock)
    monkeypatch.setattr(eval_suite, "NEXT_REQUEST", 0.0)
    requests, clients = [], []

    def handler(request):
        requests.append((request.url.path, clock.now))
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "rate limit"})
        return httpx.Response(200, json=[])

    try:
        for _ in range(2):
            clients.append(eval_suite.SandboxClient(
                base_url=eval_suite.ARENA_URL, transport=httpx.MockTransport(handler),
            ))
        clients[0].cases("sandbox")
        clients[1].cases("sandbox")
        clients[0].attachment("test-session", "test-attachment")
    finally:
        for remote in clients:
            remote.http.close()
    assert [at for _, at in requests] == [0, 0.125, 0.25, 0.375]


def test_stop_before_worker_starts_opens_no_sessions_and_is_idempotent(sandbox):
    factory, backend, sessions, requests, _, _ = sandbox
    report = eval_suite.prepare_evaluation("Stop before start", variant_id="b")
    url = f"/api/eval-suite/{report['id']}/stop"
    assert client.post(url, headers={"Origin": "https://example.org"}).status_code == 403
    assert client.post(url).json()["status"] == "stopping"
    assert client.post(url).json()["status"] == "stopping"
    result = eval_suite.run_evaluation(report, backend, factory)
    assert result["status"] == "stopped" and result["stop_requested"]
    assert all(row["status"] == "not_started" for row in result["rows"])
    assert not sessions and not requests
    assert client.post(url).json()["status"] == "stopped"
    assert client.post("/api/eval-suite/unknown/stop").status_code == 404


def test_stopping_one_prompt_keeps_completed_score_and_blocks_later_messages(sandbox):
    factory, _, _, requests, _, _ = sandbox
    entered, release = threading.Barrier(7), threading.Event()
    buyer_lock, buyers = threading.Lock(), []
    first_done = threading.Event()

    class PausedBuyer(Buyer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            with buyer_lock:
                self.first = not buyers
                buyers.append(self)

        def complete(self, system, user):
            if not self.calls:
                entered.wait(15)
                if not self.first:
                    assert release.wait(15)
            result = super().complete(system, user)
            if self.first:
                first_done.set()
            return result

    a = eval_suite.prepare_evaluation("Unaffected A")
    b = eval_suite.prepare_evaluation("Stop B", variant_id="b")
    with ThreadPoolExecutor(max_workers=1) as batch_pool:
        future = batch_pool.submit(eval_suite.run_evaluation, b, PausedBuyer, factory)
        try:
            entered.wait(15)
            assert first_done.wait(10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                live = client.get("/api/eval-suite").json()["variants"][1]["latest"]
                if live["completed"]:
                    break
                time.sleep(0.01)
            assert live["completed"] == 1
            response = client.post(f"/api/eval-suite/{b['id']}/stop")
            assert response.status_code == 202 and response.json()["status"] == "stopping"
            variants = client.get("/api/eval-suite").json()["variants"]
            assert not variants[0]["latest"].get("stop_requested")
            assert variants[1]["latest"]["stop_requested"]
            assert client.get("/api/status").status_code == 200
        finally:
            release.set()
    result = future.result()
    assert result["status"] == "stopped" and result["completed"] == 1
    assert sum(row["status"] == "cancelled" for row in result["rows"]) == 5
    assert len([r for r in requests if r.url.path.endswith("/messages")]) == 1
    completed = next(row for row in result["rows"] if row["status"] == "completed")
    assert result["mean_score"] == completed["score"]
    for row in result["rows"]:
        if row["status"] == "cancelled":
            saved = get_run(row["run_id"])
            assert saved["termination"]["origin"] == "user"
            assert saved["termination"]["buyer_action_sent"] is False
            assert saved["remote_cleanup"]["status"] == "ended"
            assert saved["threads"]
    assert client.post(f"/api/eval-suite/{a['id']}/stop").status_code == 202


def test_stop_between_supplier_replies_prevents_another_model_call(sandbox, monkeypatch):
    factory, backend, _, requests, buyers, _ = sandbox
    report = eval_suite.prepare_evaluation("Cancel after reply")
    original_send = eval_suite.SandboxTable.send

    def stop_after_reply(table, sid, message):
        reply = original_send(table, sid, message)
        eval_suite.stop_evaluation(report["id"])
        return reply

    # Send a normal email first; cancellation must prevent the next model decision.
    class EmailBuyer(Buyer):
        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            sid = next(iter(payload["protocol_state"]["suppliers"]))
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Please improve your offer.",
                               **assessment_fields(payload, sid)})

    def make_backend(**kwargs):
        buyer = EmailBuyer(**kwargs)
        buyers.append(buyer)
        return buyer

    monkeypatch.setattr(eval_suite.SandboxTable, "send", stop_after_reply)
    result = eval_suite.run_evaluation(report, make_backend, factory)
    assert result["status"] == "stopped"
    assert all(buyer.calls <= 1 for buyer in buyers)
    assert not any(json.loads(r.content)["message"].get("action") for r in requests if r.url.path.endswith("/messages"))
