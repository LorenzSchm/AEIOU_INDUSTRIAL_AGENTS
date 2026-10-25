import json
from types import SimpleNamespace

import pytest

from aeiou.agent import ClaudeBackend, ProviderRefusalError
from aeiou.evaluation_data import stats
from aeiou.local_evaluation import evaluate_local, evaluation_plan
from aeiou.store import get_run, new_run, save_run
from aeiou.supplier_profiles import PROFILES, case_profile


def test_provider_refusal_records_category_but_not_fallback_secret():
    backend = ClaudeBackend.__new__(ClaudeBackend)
    backend.model, backend.max_tokens = "test-model", 10
    backend.effort, backend.speed = "low", "standard"
    backend.calls = backend.errors = backend.refusals = 0
    backend.input_tokens = backend.output_tokens = backend.empty_responses = 0
    backend.stop_reasons, backend.refusal_details = {}, []
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=20, output_tokens=0),
        stop_reason="refusal",
        stop_details={
            "category": "reasoning_extraction",
            "explanation": "Use a decision brief",
            "fallback_credit_token": "SECRET",
        },
        _request_id="req:test",
        content=[],
    )
    backend.client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    with pytest.raises(ProviderRefusalError) as exc:
        backend.complete("system", "user")
    assert exc.value.details["category"] == "reasoning_extraction"
    assert backend.calls == backend.errors == backend.refusals == 1
    assert "SECRET" not in json.dumps(backend.refusal_details)


def test_three_repeats_follow_actual_company_ownership(monkeypatch):
    import aeiou.local_evaluation as module

    setups = {"case:A": {"case": {"suppliers": ["sup:70110"]}}, "case:B": {"case": {"suppliers": ["sup:70510"]}}}
    monkeypatch.setattr(module, "public_setups", lambda: setups)
    plan = evaluation_plan()
    assert len(plan) == 6
    assert len({s for _, _, s in plan}) == 6
    for cid, setup in setups.items():
        profile = case_profile(setup["case"])
        assert profile in PROFILES
        assert sum(c == cid and p == profile for c, p, _ in plan) == 3
    assert len(evaluation_plan(profiles=["family"])) == 3
    assert evaluation_plan(profiles=["group"]) == [row for row in plan if row[1] == "group"]


def test_stats_do_not_invent_negotiation_scores_for_failed_runs():
    runs = [
        {"status": "completed", "result": {"score": 0.8, "deal": True}},
        {"status": "failed"},
        {"status": "cancelled"},
    ]
    s = stats(runs)
    assert s["mean_score"] == 0.8
    assert s["reliability_score"] == 0.4
    assert s["completed"] == s["failures"] == s["cancelled"] == 1
    assert stats([{"status": "failed"}])["mean_score"] is None
    supplier_error = {"status": "failed", "failure_origin": "supplier"}
    assert stats([supplier_error])["reliability_score"] is None
    assert stats([*runs, supplier_error])["reliability_score"] == 0.4
    assert stats([*runs, supplier_error])["supplier_failures"] == 1


def test_batch_stops_before_starting_second_case_on_canary_refusal(monkeypatch, tmp_path):
    from arena_kit.sim import Sim

    import aeiou.local_evaluation as module
    from aeiou.hosted_replay import public_setup
    from aeiou.simulation import local_cases

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    case = local_cases()["case:S01"]
    opened = Sim({case["case_id"]: case}, no_clock=True).open("test", case["case_id"])
    setup = public_setup({"id": "fixture", "mode": "hosted", "case": case, "threads": opened["threads"]})
    (tmp_path / "state/public-setups.json").write_text(json.dumps({case["case_id"]: setup}))

    class Refuse:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def complete(self, system, user):
            self.calls += 1
            raise ProviderRefusalError("refused")

    r = evaluate_local(plan=[("case:S01", "family", i) for i in range(3)], parallel=3, backend_factory=Refuse)
    assert r["status"] == "stopped"
    assert r["summary"]["runs"] == 1
    assert sum(row.get("status") == "not_started" for row in r["rows"]) == 2
    assert len(list((tmp_path / "state/runs").glob("*.json"))) == 1


def test_run_list_retains_prompt_versions_and_cancelled_state(monkeypatch, tmp_path):
    from aeiou.store import list_runs

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    run = new_run("case:P01", "local", "prompt-original")
    run.update(status="cancelled", prompt={"system_sha256": "version-a"}, history={"retired": True}, seed=42)
    save_run(run)
    assert list_runs()[0]["prompt"]["system_sha256"] == "version-a"
    assert list_runs()[0]["status"] == "cancelled"
    assert get_run(run["id"])["seed"] == 42


def test_hosted_benchmark_keeps_mean_best_and_failed_attempts_separate(monkeypatch, tmp_path):
    import aeiou.hosted_benchmarks as module

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    rows = []
    for i, score in enumerate([0.2, 0.8, None]):
        rows.append(
            {
                "id": str(i),
                "mode": "hosted",
                "agent": "original",
                "case_id": "case:P01",
                "model": "same-model",
                "prompt": {"system_sha256": "same-prompt"},
                "status": "completed" if score is not None else "failed",
                "result": {"score": score, "deal": True} if score is not None else {},
            }
        )
    monkeypatch.setattr(module, "list_runs", lambda **kwargs: rows)
    data = module.hosted_benchmarks()
    assert len(data["rows"]) == 1
    summary = data["rows"][0]["summary"]
    assert summary["mean_score"] == 0.5
    assert summary["best_score"] == 0.8
    assert summary["failures"] == 1
    assert summary["reliability_score"] == pytest.approx(1 / 3)
    assert module.hosted_benchmarks(True)["summary"]["attempts"] == 0
