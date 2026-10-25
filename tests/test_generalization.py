import copy

import pytest

from aeiou.generalization import generate_cases
from aeiou.grounding import responsive_act
from aeiou.negotiation_state import supplier_state
from aeiou.opponent_model import learn_beliefs
from aeiou.research import package
from aeiou.simulation import local_cases
from aeiou.strategic import StrategicNegotiator
from aeiou.strategy_policy import select_strategy
from aeiou.supplier_signals import observed_signals, recent_kinds
from aeiou.uncertainty import robust_quote_improvement


def quote(price, payment=30, **extra):
    return {
        "from": "seller",
        "status": "open",
        "body": "A current quote.",
        "term_sheets": [{"terms": {"unit_price": price, "payment_days": payment, **extra}}],
    }


def test_concession_estimate_does_not_confuse_price_with_payment_trade():
    case = local_cases()["case:S01"]
    events = [quote(20, 90), quote(18, 14), quote(17, 14)]
    assert learn_beliefs(case, events)["observed_price_step"] == 1
    assert learn_beliefs(case, events[:2])["observed_price_step"] == 0


def test_model_does_not_learn_quality_preference_using_cash_hypotheses():
    case = copy.deepcopy(local_cases()["case:T01"])
    events = [
        quote(20),
        {
            "from": "buyer",
            "term_sheets": [
                {"terms": {"unit_price": 18, "ppm_commitment": 500, "chargeback_eur_per_complaint": 100}},
                {"terms": {"unit_price": 18, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 100}},
            ],
        },
        {
            "from": "seller",
            "status": "open",
            "term_sheets": [{"terms": {"unit_price": 19, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 100}}],
        },
    ]
    assert learn_beliefs(case, events)["observations"] == 0


def test_old_resistance_does_not_survive_an_unrelated_fresh_reply():
    case = local_cases()["case:S01"]
    events = [dict(quote(20), body="That price is unrealistic."), {"from": "buyer"}, quote(19)]
    state = supplier_state(case, events)
    state["signals"] = observed_signals(events)
    assert not recent_kinds(state)
    assert responsive_act({"question": "delivery"}, events)["question"] == "delivery"


def test_weak_cash_hypothesis_cannot_flip_the_strategy():
    case = local_cases()["case:S01"]
    events = [quote(20)]
    belief = learn_beliefs(case, events)
    belief["observations"] = 3
    state = supplier_state(case, events)
    assert select_strategy(case, events, state, belief)["name"] == "balanced"


@pytest.mark.parametrize("confidence", [None, 0, -5, 100, float("nan")])
def test_invalid_uncertainty_never_relaxes_the_point_estimate_guard(confidence):
    assert robust_quote_improvement({}, {}, {}, {}, {"full_cost": 15}, confidence) is None


def test_exploratory_trade_must_beat_quote_at_every_supplier_cost_stress():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=3, relationship_eur_per_point=50000)
    supplier = {"full_cost": 15, "interest_rate": 0.12, "standard": {"payment_days": 30, "incoterm": "FCA"}}
    quote_terms = {"unit_price": 24, "payment_days": 30}
    safe = robust_quote_improvement(case, {"unit_price": 21, "payment_days": 30}, quote_terms, {}, supplier, 20)
    assert len(safe) == 3
    assert all(r["proposal_cost"] < r["quote_cost"] for r in safe)
    assert robust_quote_improvement(case, {"unit_price": 5, "payment_days": 30}, quote_terms, {}, supplier, 20) is None


def test_generated_splits_are_disjoint_deterministic_and_not_arena_ids():
    dev, holdout = generate_cases("development"), generate_cases("holdout")
    assert dev == generate_cases("development")
    assert {s["case"]["case_id"] for s in dev}.isdisjoint(s["case"]["case_id"] for s in holdout)
    assert all(s["case"]["case_id"].startswith("case:G") and not s["case"]["refs"] for s in dev + holdout)
    assert all("c_best" not in str(s["case"]) and "floor_ratio" not in str(s["case"]) for s in dev + holdout)


def test_renaming_unknown_case_and_supplier_does_not_change_economic_action():
    case = copy.deepcopy(generate_cases("holdout")[0]["case"])
    sid = case["suppliers"][0]
    opening = quote(25)
    opening["term_sheets"][0]["term_sheet_id"] = "offer:neutral"
    first = StrategicNegotiator(package()).decide(case, {sid: [opening]})
    renamed = copy.deepcopy(case)
    renamed.update(case_id="case:UNSEEN", suppliers=["sup:UNSEEN"])
    second = StrategicNegotiator(package()).decide(renamed, {"sup:UNSEEN": [opening]})
    assert first["message"].get("term_sheets") == second["message"].get("term_sheets")
    assert first["message"].get("action") == second["message"].get("action")


def test_estimate_sensitive_probe_survives_full_controller_without_relaxing_must_haves():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNKNOWN-INDEX", suppliers=["sup:UNKNOWN"], negotiable=["unit_price", "index_clause"])
    case["fixed_terms"]["payment_days"] = 30
    case["fixed_terms"].pop("index_clause")
    case["term_options"] = {
        "index_clause": [None, {"series": "TEST", "material_share": 0.35, "deadband_pct": 3, "base_month": "2026-01"}]
    }
    case["buyer"].update(
        category_goal="stabilise", sustainable_margin_pct=3, relationship_eur_per_point=5000, index_paths={"TEST": 0.06}
    )
    events = [
        {
            "from": "seller",
            "status": "open",
            "body": "Quote.",
            "term_sheets": [{"term_sheet_id": "offer:UNKNOWN", "terms": {"unit_price": 17.7, "index_clause": None}}],
        }
    ]
    agent = StrategicNegotiator(package())
    agent.prepare(case)
    agent.intelligence["estimates"] = {"sup:UNKNOWN": {"full_cost": 15, "confidence_pct": 2}}
    move = agent.decide(case, {"sup:UNKNOWN": events})
    packages = agent.support["sup:UNKNOWN"]["plan"]["packages"]
    probe = next(p for p in packages if p.get("uncertainty_probe"))
    assert packages[0]["id"] == "A" and packages[0]["terms"]["index_clause"] is None
    assert probe["cost"] > packages[0]["cost"]  # do not pretend this dominates the optimistic anchor
    assert probe["cost"] < probe["standing_cost"]
    assert all(r["proposal_cost"] < r["quote_cost"] for r in probe["estimate_stresses"])
    assert any(s["terms"] == probe["terms"] for s in move["message"]["term_sheets"])
