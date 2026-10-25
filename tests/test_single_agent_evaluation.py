import json

import pytest
from arena_kit.sim import Sim
from fastapi.testclient import TestClient

from aeiou.action_policy import NegotiationBlocked
from aeiou.hosted_replay import public_setup
from aeiou.local_evaluation import evaluate_local
from aeiou.runner import RecordingTable, make_agent
from aeiou.service import app
from aeiou.simulation import LocalTable, local_cases


def test_removed_agent_rejected_before_execution():
    with pytest.raises(ValueError, match="Unknown agent"):
        make_agent("lookahead")
    assert (
        TestClient(app)
        .post(
            "/api/play",
            json={
                "case_id": "case:S01",
                "mode": "local",
                "agent": "lookahead",
            },
        )
        .status_code
        == 422
    )
    assert TestClient(app).get("/api/planning-comparisons").status_code == 404


def test_both_transports_block_walkaway_before_touching_the_session():
    message = {"action": {"type": "walk_away"}}
    local = LocalTable(None, {})
    hosted = RecordingTable.__new__(RecordingTable)
    for table in (local, hosted):
        with pytest.raises(NegotiationBlocked, match="BUYER_WALK_AWAY_DISABLED"):
            table.send("sup:test", message)


def test_total_attempt_cap_includes_interrupted_previous_attempts():
    with pytest.raises(ValueError, match="20 total attempts"):
        evaluate_local(plan=[("case:P01", "relationship", 1)] * 13, prior_attempts=8)


def test_single_agent_report_uses_current_prompt_and_no_arena(monkeypatch, tmp_path):
    from test_prompt_comparison import FakeBackend

    import aeiou.local_evaluation as module

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    case = local_cases()["case:S01"]
    opened = Sim({case["case_id"]: case}, no_clock=True).open("fixture", case["case_id"])
    setup = public_setup({"id": "fixture", "mode": "hosted", "case": case, "threads": opened["threads"]})
    (tmp_path / "state/public-setups.json").write_text(json.dumps({case["case_id"]: setup}))

    def forbidden(*args, **kwargs):
        raise AssertionError("Local evaluation attempted a hosted call")

    monkeypatch.setattr("arena_kit.ArenaClient.__init__", forbidden)
    report = evaluate_local(plan=[("case:S01", "relationship", 701)], backend_factory=FakeBackend)
    assert report["status"] == "completed"
    assert report["summary"]["completed"] == 1
    assert report["summary"]["deals"] == 1
    assert report["summary"]["accepts_before_five_repeats"] == 0
    assert report["arena_sessions_used"] == 0
    run = report["rows"][0]["run"]
    assert run["agent"] == "prompt-original"
    assert all(d["action"] != "walk_away" for d in run["decisions"])
    assert all("planning" not in d for d in run["decisions"])
