import copy
import json
import runpy

import pytest
from test_negotiation_data_pipeline import NeverCall, scenario

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.learning_buyer import LearningBuyer
from aeiou.prompt_protocol import PromptSpec
from aeiou.research import package
from aeiou.tactical_features import TACTIC_SCHEMA, experience_state, tactic_features
from aeiou.tactical_training import tactical_gate


def test_actual_email_citations_and_new_evidence_are_visible_without_future_data():
    old = {"from": "buyer", "body": "PO history (po:4500014022-10)."}
    proposal = {
        "body": "In return for a firm commitment, review po:4500014022-10 and idx:AS-14404@2026-09.",
        "term_sheets": [{"terms": {"unit_price": 5.1}}],
    }
    before = copy.deepcopy((old, proposal))
    values = dict(zip(TACTIC_SCHEMA, tactic_features([old], proposal), strict=True))
    assert values["cited_po"] == values["cited_index"] == 1
    assert values["new_source_fraction"] == values["reused_source_fraction"] == 0.5
    assert values["conditional_exchange"] == values["firm_commitment"] == 1
    assert (old, proposal) == before


def test_experience_similarity_preserves_actual_quote_terms():
    case, sid, threads = scenario(5.3, "open")
    before = experience_state(case, threads[sid])
    threads[sid][-1]["term_sheets"][0]["terms"]["payment_days"] = 90
    after = experience_state(case, threads[sid])
    assert before != after
    assert after[2] == 1


@pytest.mark.parametrize(
    "price,status,accept", [(5.1, "final_offer", True), (5.3, "final_offer", False), (5.1, "agreed", True)]
)
def test_experiment_terminal_control_preserves_caps_without_model_calls(price, status, accept):
    case, sid, threads = scenario(price, status)
    policy = json.loads((ROOT / "config/p18-accept-510.json").read_text())["case:P18"]
    buyer = LearningBuyer(
        package(),
        NeverCall(),
        PromptSpec("test", "Evidence-led buyer", assessment_enabled=True, execution_policy=policy),
    )
    if accept:
        move = buyer.decide(case, threads)
        assert move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:current"}
    else:
        with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
            buyer.decide(case, threads)


def test_trial_budget_reserves_three_validation_runs_and_enforces_per_case_limit(tmp_path):
    update = runpy.run_path(str(ROOT / "scripts/run_learning_trial.py"))["update_ledger"]
    for i in range(3):
        update(tmp_path, {"trial": str(i), "case_id": "case:P10", "phase": "development"}, reserve=True)
    with pytest.raises(ValueError, match="Validation reserve"):
        update(tmp_path, {"trial": "extra", "case_id": "case:P10", "phase": "development"}, reserve=True)
    for i in range(3, 6):
        update(tmp_path, {"trial": str(i), "case_id": "case:P10", "phase": "validation"}, reserve=True)
    with pytest.raises(ValueError, match="per-case"):
        update(tmp_path, {"trial": "seventh", "case_id": "case:P10", "phase": "validation"}, reserve=True)


def test_tactics_must_improve_current_incumbent_not_only_old_focused_baseline():
    def metric(error):
        return {"validation": {"macro_mse": error}, "test": {"macro_mse": error, "macro_mae": error}}

    variants = {"focused": metric(1), "incumbent": metric(0.5), "case_tactics": metric(0.7)}
    assert tactical_gate(variants, "incumbent", True)[0] == "incumbent"
    variants["case_tactics"] = metric(0.4)
    assert tactical_gate(variants, "incumbent", True)[0] == "case_tactics"
    variants["case_tactics"]["test"]["macro_mae"] = 0.6
    assert tactical_gate(variants, "incumbent", True)[0] == "incumbent"


def test_validation_refuses_missing_or_changed_frozen_candidate(tmp_path):
    check = runpy.run_path(str(ROOT / "scripts/run_learning_trial.py"))["check_frozen"]
    with pytest.raises(ValueError, match="Freeze"):
        check(tmp_path, "case:P10", {"model_sha256": "a"})
    (tmp_path / "freeze.json").write_text(json.dumps({"candidates": {"case:P10": {"model_sha256": "a"}}}))
    check(tmp_path, "case:P10", {"model_sha256": "a"})
    with pytest.raises(ValueError, match="changed"):
        check(tmp_path, "case:P10", {"model_sha256": "b"})


def test_terminal_advertised_alternative_gets_one_exact_confirmation_then_acceptance():
    case, sid, threads = scenario(5.3, "final_offer")
    current = threads[sid][-1]
    current["standing_offer_id"] = "offer:current"
    alternative = copy.deepcopy(current["term_sheets"][0])
    alternative["term_sheet_id"] = "offer:alternative"
    alternative["terms"]["unit_price"] = 5.1
    current["term_sheets"].append(alternative)
    policy = json.loads((ROOT / "config/p18-accept-510.json").read_text())["case:P18"]
    buyer = LearningBuyer(
        package(),
        NeverCall(),
        PromptSpec("test", "Evidence-led buyer", assessment_enabled=True, execution_policy=policy),
    )
    move = buyer.decide(case, threads)
    assert move["message"]["term_sheets"] == []
    assert "unit_price: 5.1" in move["message"]["body"]
    assert buyer.decisions[-1]["confirmation"]["terms"]["unit_price"] == 5.1
    threads[sid].append({"from": "buyer", **move["message"]})
    threads[sid].append(copy.deepcopy(current))
    with pytest.raises(NegotiationBlocked):
        buyer.decide(case, threads)
    threads[sid][-1]["standing_offer_id"] = "offer:alternative"
    move = buyer.decide(case, threads)
    assert move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:alternative"}
