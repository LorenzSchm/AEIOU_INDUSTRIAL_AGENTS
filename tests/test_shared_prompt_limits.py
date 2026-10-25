"""Shared strategy text must retain each case's saved authorization through transport."""

import copy
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from aeiou import eval_suite, service
from aeiou.acceptance_floor import AcceptanceFloor
from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT, model_settings
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package
from aeiou.runner import RecordingTable

STRATEGY = (ROOT / "prompts/universal-negotiator-v1.txt").read_text()
POLICY = json.loads((ROOT / "config/p18-accept-510.json").read_text())["case:P18"]
SID = "sup:70740"


def scenario():
    recorded = json.loads((ROOT / "tests/fixtures/p18-v19-verified-offer.json").read_text())
    threads = {SID: copy.deepcopy(recorded["threads"][SID][:7])}
    threads[SID][-1]["term_sheets"][0]["terms"].update(unit_price=5.10, contract_months=36)
    return recorded["case"], threads


def spec(policy=None):
    return PromptSpec(
        "shared-test",
        STRATEGY,
        assessment_enabled=True,
        execution_policy=copy.deepcopy(POLICY if policy is None else policy),
    )


@pytest.fixture
def saved_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    monkeypatch.setattr(eval_suite, "ACTIVE_EVALUATIONS", {})
    monkeypatch.setattr(service, "JOBS", {})
    (tmp_path / "negotiation-policies.json").write_text(json.dumps({"case:P18": POLICY}))
    return tmp_path


def test_shared_batch_snapshots_only_selected_limits_without_editing_strategy(saved_policy):
    catalog = {"cases": [{"case_id": cid, "title": cid} for cid in ["case:P10", "case:P18"]], "quota": {"left": 2}}
    report = eval_suite.prepare_evaluation(
        STRATEGY, variant_id="practice", repeats=1, case_ids=["case:P10", "case:P18"], catalog=catalog
    )
    assert report["prompt_text"] == STRATEGY
    assert "acceptance_floor" not in report["prompt"]
    assert report["execution_policies"] == {"case:P18": POLICY}
    assert report["arena_sessions_used"] == 0
    # Changing the live settings after preparation cannot change this batch.
    (saved_policy / "negotiation-policies.json").write_text("{}")
    base = PromptSpec("eval", report["prompt_text"], assessment_enabled=True)
    scoped = replace(base, execution_policy=report["execution_policies"]["case:P18"])
    assert scoped.acceptance_floor.max_cost == Decimal("295173.23")
    assert scoped.acceptance_floor.maximum_unit_price == Decimal("5.1")
    assert replace(base, execution_policy=report["execution_policies"].get("case:P10")).acceptance_floor is None


def test_caps_roundtrip_without_fabricating_benchmark_or_opening(saved_policy):
    floor = spec().acceptance_floor
    assert not floor.has_benchmark
    assert floor.c_open is floor.c_best is floor.minimum_score is None
    assert AcceptanceFloor.from_dict(spec().metadata()["acceptance_floor"]) == floor
    assert floor.allows(295173.23, {"unit_price": 5.10})
    assert not floor.allows(295173.24, {"unit_price": 5.10})
    assert not floor.allows(200000, {"unit_price": 5.11})
    with pytest.raises(ValueError, match="CASE_MISMATCH"):
        floor.check_case({"case_id": "case:P10"})
    case, threads = scenario()
    agent = PromptLabAgent(package(), None, spec())
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    assert payload["acceptance_policy"]["maximum_unit_price"] == 5.10
    assert payload["price_plan"]["minimum_score"] is None
    offers = payload["price_plan"]["suppliers"][SID]["offers"]
    assert offers and all(o["estimated_score"] is None and o["benchmark_price_limits"] == [] for o in offers)


def test_shared_prompt_accepts_exact_eligible_quote_without_model_call(saved_policy):
    class NoModel:
        def complete(self, *args):
            raise AssertionError("Authorized acceptance should happen before a model call")

    case, threads = scenario()
    sheet = threads[SID][-1]["term_sheets"][0]
    agent = PromptLabAgent(package(), NoModel(), spec())
    assert agent.decide(case, threads)["message"]["action"] == {
        "type": "accept",
        "term_sheet_id": sheet["term_sheet_id"],
    }
    assert agent.decisions[-1]["source"] == "policy:accept"


@pytest.mark.parametrize("price,duration", [(5.11, 36), (5.10, 12)])
def test_shared_prompt_cannot_bypass_price_or_duration(saved_policy, price, duration):
    case, threads = scenario()
    sheet = threads[SID][-1]["term_sheets"][0]
    sheet["terms"].update(unit_price=price, contract_months=duration)
    agent = PromptLabAgent(package(), None, spec())
    assert not agent.decision_protocol_state(case, threads)["suppliers"][SID]["eligible_offer_ids"]
    with pytest.raises(ContractError, match="saved execution controls"):
        agent.contract(case, threads, {"supplier_id": SID, "action": "accept", "term_sheet_id": sheet["term_sheet_id"]})


@pytest.mark.parametrize("changed", ["price", "cost"])
def test_saved_caps_are_rechecked_at_transport_before_network(saved_policy, changed):
    class NoNetwork:
        def send_message(self, *args):
            raise AssertionError("Forbidden acceptance reached the network")

    case, threads = scenario()
    policy = copy.deepcopy(POLICY)
    sheet = threads[SID][-1]["term_sheets"][0]
    if changed == "price":
        sheet["terms"]["unit_price"] = 5.11
    else:
        policy["maximum_total_cost"] = 200000
    run = {"case": case, "threads": threads, "prompt": spec(policy).metadata()}
    before = copy.deepcopy(threads)
    table = RecordingTable(NoNetwork(), run, case)
    with pytest.raises(NegotiationBlocked, match="ACCEPTANCE_"):
        table.send(SID, {"action": {"type": "accept", "term_sheet_id": sheet["term_sheet_id"]}})
    assert threads == before


def test_explicit_conflicting_limits_still_fail_before_a_batch_is_created(saved_policy):
    prompt = (ROOT / "prompts/p18-diagnostic-v14.txt").read_text()
    with pytest.raises(ValueError, match="limits differ"):
        eval_suite.prepare_evaluation(
            prompt,
            variant_id="practice",
            repeats=1,
            case_ids=["case:P18"],
            catalog={"cases": [{"case_id": "case:P18", "title": "P18"}], "quota": {"left": 1}},
        )
    assert eval_suite.ACTIVE_EVALUATIONS == {}
    assert not (saved_policy / "eval-suite").exists()


def test_case_specific_prompt_rejected_for_mixed_batch_at_preflight(saved_policy):
    prompt = (ROOT / "prompts/p18-diagnostic-v25.txt").read_text()
    with pytest.raises(ValueError, match="for one case"):
        eval_suite.prepare_evaluation(
            prompt,
            variant_id="practice",
            repeats=1,
            case_ids=["case:P10", "case:P18"],
            catalog={
                "cases": [{"case_id": "case:P10", "title": "P10"}, {"case_id": "case:P18", "title": "P18"}],
                "quota": {"left": 2},
            },
        )
    assert eval_suite.ACTIVE_EVALUATIONS == {}


@pytest.mark.parametrize("bad", [True, None, float("nan"), float("inf"), -1, 0])
def test_malformed_saved_caps_fail_closed(bad):
    with pytest.raises(ValueError):
        _ = spec({**POLICY, "maximum_total_cost": bad}).acceptance_floor


def test_browser_launch_api_accepts_shared_prompt_and_preserves_limits(monkeypatch, saved_policy):
    queued = []

    class QueueOnly:
        def submit(self, *args):
            queued.append(args)

    monkeypatch.setattr(service, "POOL", QueueOnly())
    monkeypatch.setattr(service, "validate_buyer", lambda *_: model_settings())
    monkeypatch.setattr(
        eval_suite,
        "refresh_practice_catalog",
        lambda: {
            "cases": [{"case_id": "case:P10", "title": "P10"}, {"case_id": "case:P18", "title": "P18"}],
            "quota": {"left": 2},
        },
    )
    response = TestClient(service.app).post(
        "/api/eval-suite/practice", json={"prompt": STRATEGY, "case_ids": ["case:P10", "case:P18"], "repeats": 1}
    )
    assert response.status_code == 202, response.text
    assert len(queued) == 1  # No real job, model call or hosted session is started.
    saved = json.loads((saved_policy / "eval-suite" / f"{response.json()['id']}.json").read_text())
    assert saved["prompt_text"] == STRATEGY
    assert saved["execution_policies"] == {"case:P18": POLICY}
    assert saved["arena_sessions_used"] == 0
