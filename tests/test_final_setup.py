import json
from types import SimpleNamespace

import httpx
import pytest
from arena_kit.client import ArenaError
from arena_kit.play import CLOSE_BEFORE_CAP_S, RemoteTable

from aeiou import final_runtime
from aeiou.final_runtime import FinalClient, inspect_setup, runner_lock


def test_offline_inspection_never_connects_or_exposes_keys(monkeypatch):
    monkeypatch.setenv("ARENA_URL", "https://arena.tacto.ai")
    monkeypatch.setenv("ARENA_API_KEY", "private-arena-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-model-value")
    monkeypatch.setattr(final_runtime, "package", lambda: SimpleNamespace(facts={"f": 1}, release=True, db=None))
    monkeypatch.setattr(
        final_runtime.subprocess, "check_output", lambda args, **kwargs: "abc123" if "rev-parse" in args else ""
    )
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: pytest.fail("Offline check made HTTP request"))
    report = inspect_setup()
    assert not report["errors"] and report["network_requests"] == 0 and not report["started"]
    assert "private-arena-value" not in json.dumps(report)
    assert "private-model-value" not in json.dumps(report)
    assert report["close_before_cap_seconds"] == CLOSE_BEFORE_CAP_S == 90
    assert inspect_setup(rehearsal=True, waves=2)["errors"]  # rehearsal cannot target production


def test_long_retry_after_and_stable_post_identity(monkeypatch):
    requests, sleeps = [], []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"case_id": "case:new-final"}])
        if len(requests) == 2:
            return httpx.Response(429, headers={"Retry-After": "12"}, json={"detail": "rate limit"})
        return httpx.Response(200, json={"session_id": "one"})

    monkeypatch.setattr(final_runtime.time, "sleep", sleeps.append)
    client = FinalClient("https://arena.test", "fake", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(client, "_pace", lambda: None)
    client.cases()
    assert client.open_session("case:new-final")["session_id"] == "one"
    assert sleeps == [12.0]
    assert requests[1].headers["X-Move-Id"] == requests[2].headers["X-Move-Id"]
    assert requests[1].extensions["timeout"]["read"] >= 180
    client.http.close()


def test_final_client_rejects_side_channels_and_manual_practice_start():
    client = FinalClient(
        "https://arena.test",
        "fake",
        transport=httpx.MockTransport(lambda request: pytest.fail("Forbidden request reached network")),
    )
    for path in ("/leaderboard", "/events", "/screen", "/results", "/me", "/data"):
        with pytest.raises(ValueError):
            client._call("GET", path)
    with pytest.raises(ValueError):
        client.cases("practice")
    with pytest.raises(ValueError):
        client.open_session("case:P01")
    client.http.close()


def test_403_is_preserved_for_wave_waiting_and_409_resumes(monkeypatch):
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/v1/cases":
            return httpx.Response(403, json={"detail": "not started"})
        if request.method == "POST":
            return httpx.Response(409, json={"detail": "one session per case in the final: existing-1"})
        if request.url.path.endswith("/threads/supplier"):
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(200, json={"status": "open", "threads": {"supplier": {}}})

    client = FinalClient("https://arena.test", "fake", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(client, "_pace", lambda: None)
    with pytest.raises(ArenaError) as exc:
        client.cases()
    assert exc.value.status == 403
    client.final_case_ids.add("case:new-final")
    table = RemoteTable(client, {"case_id": "case:new-final"})
    table.open("case:new-final")
    assert table.session_id == "existing-1"
    assert sum(method == "POST" for method, _ in seen) == 1
    client.http.close()


def test_lock_blocks_second_runner_and_releases_without_deleting_file(tmp_path):
    path = tmp_path / "final.lock"
    with runner_lock(path), pytest.raises(RuntimeError, match="already holds"):
        with runner_lock(path):
            pytest.fail("Second runner acquired lock")
    assert path.exists()
    with runner_lock(path):
        pass


def test_final_cli_defaults_to_inspection_not_execution(monkeypatch, capsys):
    from aeiou.cli import main

    monkeypatch.setattr("sys.argv", ["aeiou", "final"])
    monkeypatch.setattr(final_runtime, "inspect_setup", lambda *args: {"errors": [], "started": False})
    monkeypatch.setattr(final_runtime, "run_final", lambda *args: pytest.fail("Default started the runner"))
    main()
    assert json.loads(capsys.readouterr().out)["started"] is False


def test_start_refuses_open_practice_sessions_before_any_wave(monkeypatch, tmp_path):
    monkeypatch.setattr(final_runtime.config, "ROOT", tmp_path)
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(final_runtime, "inspect_setup", lambda *args: {"errors": []})
    closed = []
    client = SimpleNamespace(
        sessions=lambda scope: [{"status": "open"}], http=SimpleNamespace(close=lambda: closed.append(True))
    )
    monkeypatch.setattr(final_runtime, "FinalClient", lambda: client)
    monkeypatch.setattr(final_runtime, "play_waves", lambda *a, **k: pytest.fail("Started with practice still open"))
    with pytest.raises(RuntimeError, match="Stop practice loops"):
        final_runtime.run_final()
    assert closed == [True]


def test_start_wiring_prepares_shared_evidence_and_saves_results_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(final_runtime.config, "ROOT", tmp_path)
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(final_runtime, "inspect_setup", lambda *args: {"errors": [], "commit": "test"})
    prepared, closed = [], []
    pkg = SimpleNamespace(evidence_index=lambda: prepared.append(True), db=None)
    client = SimpleNamespace(sessions=lambda scope: [], http=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(final_runtime, "package", lambda: pkg)
    monkeypatch.setattr(final_runtime, "FinalClient", lambda: client)
    monkeypatch.setattr(final_runtime, "make_agent", lambda name, shared: (name, shared))

    def fake_waves(c, factory, **kwargs):
        assert c is client and prepared == [True]
        assert factory() == ("strategic", pkg)
        assert kwargs["waves"] == 5 and kwargs["parallel"] == 10
        return {"mock-case": {"deal": True}}

    monkeypatch.setattr(final_runtime, "play_waves", fake_waves)
    output = final_runtime.run_final()
    assert json.loads(output.read_text(encoding="utf-8")) == {"mock-case": {"deal": True}}
    assert closed == [True]
