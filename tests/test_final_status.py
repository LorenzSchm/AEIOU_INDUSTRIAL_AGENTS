import json
import os
import time
from datetime import UTC, datetime

import pytest

from aeiou import final_status


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    monkeypatch.setattr(final_status, "runner_alive", lambda _: True)
    folder = tmp_path / "final-launch-20261025T114103Z"
    folder.mkdir()
    (folder / "launch.json").write_text(
        json.dumps(
            {
                "started_at": datetime.fromtimestamp(time.time() - 5, UTC).isoformat(),
                "pid": 123,
                "command": ["aeiou", "final", "--parallel", "10", "--waves", "5"],
                "model_settings": {"model": "claude-opus-5-5"},
                "sunday_release": True,
            }
        )
    )
    log = folder / "runner.log"
    log.write_text('INFO HTTP Request: GET https://arena.tacto.ai/v1/cases?set=final "HTTP/1.1 403 Forbidden"\n')
    return folder, log


def test_waiting_runner_is_visible_without_creating_sessions(runner):
    state = final_status.final_runner_status()
    assert state["state"] == "waiting"
    assert state["waves"] == 5 and state["parallel"] == 10
    assert state["sunday_release"] is True
    assert state["last_activity_at"]


def test_stopped_process_is_not_reported_as_waiting(runner, monkeypatch):
    monkeypatch.setattr(final_status, "runner_alive", lambda _: False)
    assert final_status.final_runner_status()["state"] == "stopped"


def test_stale_poll_does_not_claim_arena_is_still_closed(runner):
    _, log = runner
    os.utime(log, (time.time() - 60, time.time() - 60))
    assert final_status.final_runner_status()["state"] == "unknown"


def test_latest_poll_replaces_waiting_state(runner):
    _, log = runner
    with log.open("a") as out:
        out.write('INFO HTTP Request: GET https://arena.tacto.ai/v1/cases?set=final "HTTP/1.1 200 OK"\n')
    assert final_status.final_runner_status()["state"] == "running"


def test_prior_results_do_not_complete_new_runner(runner, monkeypatch):
    folder, _ = runner
    results = folder.parent / "final-results.json"
    results.write_text("{}")
    os.utime(results, (time.time() - 60, time.time() - 60))
    monkeypatch.setattr(final_status, "runner_alive", lambda _: False)
    assert final_status.final_runner_status()["state"] == "stopped"


def test_results_with_case_errors_are_not_reported_as_success(runner, monkeypatch):
    folder, _ = runner
    (folder.parent / "final-results.json").write_text(json.dumps({"case:F01": "request failed"}))
    monkeypatch.setattr(final_status, "runner_alive", lambda _: False)
    assert final_status.final_runner_status()["state"] == "error"


def test_status_endpoint_includes_final_runner(runner):
    from fastapi.testclient import TestClient

    from aeiou.service import app

    response = TestClient(app).get("/api/status")
    assert response.status_code == 200
    assert response.json()["final_runner"]["state"] == "waiting"
