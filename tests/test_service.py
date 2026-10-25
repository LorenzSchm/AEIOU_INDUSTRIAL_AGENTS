import os

from fastapi.testclient import TestClient

from aeiou.service import app

client = TestClient(app)


def test_status_exposes_presence_not_credentials():
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["facts"] > 10000
    for variable in ("ANTHROPIC_API_KEY", "ARENA_API_KEY"):
        if os.environ.get(variable):
            assert os.environ[variable] not in response.text


def test_prompt_api_distinguishes_active_revision_from_archived_original():
    from aeiou.config import ROOT

    response = client.get("/api/prompts")
    assert response.status_code == 200
    body = response.json()
    active = body["variants"]["original"]
    assert active["strategy"] == (ROOT / "SYSTEM_PROMPT.md").read_text()
    assert "how am i supposed to do that?" not in active["strategy"].casefold()
    assert active["required_repeats"] == 5
    reference = body["reference_original"]
    assert reference["strategy_sha256"] == "e8bb52863ea8b281f0c5e05bb533a414f22d0b9cd1d02ed95aa7d07e3cf1fe28"
    assert "how am i supposed to do that?" in reference["strategy"].casefold()
    assert len(body["research_sources"]) == 3
    assert active["strategy_sha256"] != reference["strategy_sha256"]
    assert client.get("/api/prompt-comparisons").status_code == 200
    local = client.get("/api/local-evaluations")
    assert local.status_code == 200 and isinstance(local.json(), list)


def test_cross_origin_paid_job_rejected():
    response = client.post("/api/benchmark", headers={"Origin": "https://example.org"})
    assert response.status_code == 403


def test_invalid_case_rejected_before_scheduling():
    response = client.post("/api/play", json={"case_id": "case:fake", "mode": "local", "agent": "adaptive"})
    assert response.status_code == 422


def test_traversal_cannot_read_env():
    assert client.get("/api/runs/..%2F.env").status_code == 404
    assert client.get("/assets/..%2F..%2F..%2F.env").status_code == 404


def test_local_ui_job_persists_completed_transcript(tmp_path, monkeypatch, model_supplier):
    from aeiou.service import JOBS
    from aeiou.store import get_run

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    response = client.post("/api/play", json={"case_id": "case:S01", "mode": "local", "agent": "adaptive"})
    assert response.status_code == 202
    run_id = response.json()["id"]
    # Submit a barrier after the one job, then wait on its future rather than sleep-polling.
    # The job itself is short; await its recorded state with a bounded event loop.
    import time

    deadline = time.monotonic() + 5
    while JOBS[run_id]["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert JOBS[run_id]["status"] == "completed"
    run = get_run(run_id)
    assert run["status"] == "completed"
    assert run["result"]["must_haves_ok"]
    assert any(m.get("from") == "buyer" for ev in run["threads"].values() for m in ev)
