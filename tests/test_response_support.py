import copy
import json

import pytest
from arena_kit.cost import total_cost
from test_custom_model import recorded
from test_negotiation_data_pipeline import NeverCall, scenario

from aeiou.action_policy import NegotiationBlocked
from aeiou.agent import Negotiator
from aeiou.config import ROOT
from aeiou.custom_model import VERSION, CustomResponseModel, register
from aeiou.custom_model_data import SCHEMA, TARGETS, live_features
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec, prompt_specs
from aeiou.research import package
from aeiou.response_support import GUIDANCE, ResponseSupport
from aeiou.strategic import StrategicNegotiator
from aeiou.tactical_features import experience_state


class Forecast:
    sha256 = "test-model"
    artifact = {"version": VERSION}

    def __init__(self):
        self.calls = []

    def forecast(self, case, sid, events, proposal):
        self.calls.append(copy.deepcopy(proposal))
        return {
            "predictions": {"quoted_cost_gain": 0.02, "supplier_exit": 0},
            "reliability": {"quoted_cost_gain": {"validation_mae": 0.01}},
            "status": "advisory_only",
        }


def configured(model=None, mode="shadow"):
    policy = json.loads((ROOT / "config/p18-accept-510.json").read_text())["case:P18"]
    agent = PromptLabAgent(
        package(),
        NeverCall(),
        PromptSpec(
            "built-in-test",
            "Use evidence and full package costs.",
            assessment_enabled=True,
            execution_policy=policy,
            response_support_mode=mode,
        ),
    )
    agent.response_model = model or Forecast()
    return agent


def test_experience_forecast_uses_complete_historical_email_and_current_state():
    case, sid, threads = scenario(5.4, "open")
    model = Forecast()
    terms = {**threads[sid][-1]["term_sheets"][0]["terms"], "unit_price": 5.0}
    proposal = {"body": "Historical complete proposal with its actual evidence.", "term_sheets": [{"terms": terms}]}
    model.artifact = {
        **model.artifact,
        "experiences": [
            {
                "case_id": case["case_id"],
                "session_id": "historical",
                "turn": 1,
                "state": experience_state(case, threads[sid]),
                "proposal": proposal,
                "source_attachment_present": True,
            }
        ],
    }
    buyer = configured(model, mode="advisory")
    buyer.prepare(case)
    payload = json.loads(buyer.render_lab(case, threads))
    example = payload["learned_response_support"]["suppliers"][sid]["historical_experiences"][0]
    assert example["current_state_forecast"]["status"] == "advisory_only"
    evaluated = next(p for p in model.calls if p["body"] == proposal["body"])
    assert evaluated["term_sheets"] == proposal["term_sheets"]
    assert evaluated["attachments"] == [{"historical_presence_only": True}]
    assert "attachments" not in proposal


def test_history_prefers_nearest_state_then_recent_sessions_without_mutating_model():
    case, sid, threads = scenario(5.4, "open")
    model = Forecast()
    state = experience_state(case, threads[sid])
    proposal = {"body": "Historical offer", "term_sheets": threads[sid][-1]["term_sheets"]}
    experiences = [
        {"session_id": name, "created_at": date, "state": vector, "case_id": case["case_id"], "proposal": proposal}
        for name, date, vector in (
            ("older", "2026-01-01", state),
            ("newest-but-distant", "2026-10-25", [v + 10 for v in state]),
            ("recent", "2026-10-24", state),
        )
    ]
    model.artifact = {**model.artifact, "experiences": experiences}
    before = copy.deepcopy(model.artifact)
    buyer = configured(model, mode="advisory")
    buyer.prepare(case)
    payload = json.loads(buyer.render_lab(case, threads))
    examples = payload["learned_response_support"]["suppliers"][sid]["historical_experiences"]
    assert [e["session_id"] for e in examples] == ["recent", "older", "newest-but-distant"]
    assert model.artifact == before


@pytest.mark.parametrize("mode", ["shadow", "advisory"])
def test_support_is_audited_but_only_explicit_advisory_reaches_buyer(mode):
    case, sid, threads = scenario(5.4, "open")
    agent = configured(mode=mode)
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    assert {"decision_context", "price_plan", "protocol_state", "supplier_signals"} <= payload.keys()
    support = agent.last_response_support
    assert support["mode"] == mode
    assert ("learned_response_support" in payload) == (mode == "advisory")
    assert (GUIDANCE in agent.spec.system) == (mode == "advisory")
    assert support["status"] == "advisory_only"
    assert support["suppliers"][sid]["menus"]
    assert support["suppliers"][sid]["commercial_cues"]["themes"]
    assert support["suppliers"][sid]["preferred_menu"] is None
    assert agent.response_model.calls
    assert len(agent.audit["response_support"]) == 1


def test_shared_shadow_support_stays_out_of_base_buyer_and_strategic_planner(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    case, sid, threads = scenario(5.4, "open")
    base = Negotiator(package())
    base.response_model = Forecast()
    base.prepare(case)
    payload = json.loads(base.render(case, threads, base.packages(case, threads), []))
    assert "learned_response_support" not in payload
    assert GUIDANCE not in base.system_prompt()
    assert base.last_response_support["suppliers"][sid]["menus"]

    class Planner:
        payload = None

        def plan_negotiation(self, payload):
            self.payload = json.loads(payload)
            return {"supplier_id": sid, "action": "send", "proposals": [], "evidence_ids": []}

    planner = Planner()
    strategic = StrategicNegotiator(package(), backend=planner)
    strategic.response_model = Forecast()
    # An opening requires a proposal, not terminal economic closure.
    strategic.decide(case, {sid: threads[sid][:1]})
    assert "learned_response_support" not in planner.payload
    assert strategic.decisions[-1]["learned_response_support"]


def test_shadow_forecasts_leave_original_prompt_input_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    case, sid, threads = scenario(5.4, "open")
    spec = prompt_specs()["original"]
    buyer = PromptLabAgent(package(), NeverCall(), spec)
    buyer.response_model = Forecast()
    buyer.prepare(case)
    with_forecasts = buyer.render_lab(case, threads)
    assert buyer.response_model.calls
    assert buyer.last_response_support["mode"] == "shadow"
    buyer.response_support = None
    assert buyer.render_lab(case, threads) == with_forecasts
    assert GUIDANCE not in spec.system
    assert "learned_response_support" not in with_forecasts


def test_invalid_support_mode_is_rejected():
    with pytest.raises(ValueError, match="shadow or advisory"):
        PromptSpec("test", "Test", response_support_mode="anything")


@pytest.mark.parametrize("status", ["open", "final_offer"])
def test_acceptance_precedes_even_extremely_optimistic_model(status):
    case, sid, threads = scenario(5.10, status)
    agent = configured()
    move = agent.decide(case, threads)
    assert move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:current"}
    assert agent.response_model.calls == []
    assert agent.decisions[-1]["source"] == "policy:accept"


def test_pasted_final_offer_remains_blocked_without_a_counter():
    case, sid, threads = scenario(5.30, "final_offer")
    agent = configured()
    with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
        agent.decide(case, threads)
    assert not agent.response_model.calls
    assert agent.audit["response_support"][-1]["suppliers"][sid]["status"] == "skipped"


def test_forged_cheap_cost_and_invalid_terms_never_reach_forecaster():
    case, sid, threads = scenario(5.4, "open")
    agent = configured()
    agent.prepare(case)
    terms = dict(threads[sid][-1]["term_sheets"][0]["terms"])
    bad_price = {"id": "over-cap", "terms": terms, "cost": 0}
    invalid = {"id": "invalid", "terms": {**terms, "payment_days": 7}, "cost": 0}
    valid = {"id": "valid", "terms": {**terms, "unit_price": 5.10}, "cost": 0}
    result = agent.response_support.safe_assess(
        case,
        threads,
        {sid: [bad_price, invalid, valid]},
        protocol=agent.decision_protocol_state(case, threads),
        floor=agent.acceptance_floor,
        policy=agent.execution_policy,
    )["suppliers"][sid]
    assert [p["id"] for p in result["packages"]] == ["valid"]
    assert result["packages"][0]["direct_cost"] == total_cost(case, valid["terms"])
    assert len(agent.response_model.calls) == 1
    assert {p["id"] for p in result["excluded_candidates"]} == {"over-cap", "invalid"}


def test_missing_corrupt_or_failing_advice_does_not_block_buyer(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    case, sid, threads = scenario(5.10, "final_offer")
    for registry in (None, "{broken json"):
        if registry:
            (tmp_path / "custom-model.json").write_text(registry)
        agent = configured()
        del agent.response_model
        assert agent.decide(case, threads)["message"]["action"]["type"] == "accept"
        assert agent.decisions[-1]["learned_response_support"]["status"] == "unavailable"

    class Broken(Forecast):
        def forecast(self, *args):
            raise RuntimeError("unavailable model")

    case, sid, threads = scenario(5.4, "open")
    agent = configured(Broken())
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    assert agent.last_response_support["reason"] == "forecast_failed"
    assert "learned_response_support" not in payload
    assert payload["candidate_packages"][sid]


def test_other_cases_do_not_load_specialist(monkeypatch):
    def fail():
        raise AssertionError("Should never load this model")

    monkeypatch.setattr("aeiou.response_support.CustomResponseModel", fail)
    support = ResponseSupport({"case_id": "case:S01"})
    assert support.unavailable == "case_outside_training_scope"


def constant_artifact(case, sid, row):
    return {
        "version": VERSION,
        "schema": list(SCHEMA),
        "training_examples": 40,
        "training_sessions": 15,
        "models": {t: {"target": t, "prior": 0.02, "selection": {"method": "prior", "strength": 0}} for t in TARGETS},
        "case_support": {
            case["case_id"]: {
                "suppliers": [sid],
                "feature_min": row["v"],
                "feature_max": row["v"],
            }
        },
        "reliability": {
            case["case_id"]: {
                t: {"usable_for_comparison": t == "quoted_cost_gain", "validation_mae": 0.01} for t in TARGETS
            }
        },
    }


def test_forecast_abstains_on_unseen_inputs_and_hides_unsupported_probabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    _, case, seller, buyer, _ = recorded()
    row = live_features(case, "sup:A", [seller], buyer)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(constant_artifact(case, "sup:A", row)))
    register(path)
    model = CustomResponseModel()
    valid = model.forecast(case, "sup:A", [seller], buyer)
    assert valid["predictions"] == {"quoted_cost_gain": 0.02}
    assert "supplier_exit" in valid["unsupported_targets"]
    assert model.forecast(case, "sup:NEW", [seller], buyer)["predictions"] == {}
    changed = copy.deepcopy(buyer)
    changed["term_sheets"][0]["terms"]["unit_price"] = 1
    assert model.forecast(case, "sup:A", [seller], changed)["status"] == "abstained"
    path.write_text(path.read_text().replace('"prior": 0.02', '"prior": 0.99'))
    assert ResponseSupport(case).unavailable == "model_missing_or_invalid"
    # A prepared run keeps its verified in-memory snapshot.
    assert model.forecast(case, "sup:A", [seller], buyer)["predictions"] == valid["predictions"]
