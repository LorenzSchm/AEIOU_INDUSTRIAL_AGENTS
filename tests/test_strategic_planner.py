import copy
import json

import pytest
from arena_kit.cost import closing_terms, complete_offer
from arena_kit.cost import total_cost as published_cost

from aeiou.economic_beliefs import infer_economics, total_cost
from aeiou.opponent_model import equivalent_response_price, learn_beliefs, predicted_choices
from aeiou.research import package
from aeiou.simulation import local_cases
from aeiou.strategic import StrategicNegotiator
from aeiou.strategic_planner import admit_proposals


def setup_case():
    case = copy.deepcopy(local_cases()["case:S01"])
    sid = case["suppliers"][0]
    threads = {
        sid: [
            {
                "from": "supplier",
                "status": "open",
                "body": "Please review our quote.",
                "term_sheets": [{"term_sheet_id": "offer:test", "terms": {"unit_price": 20, "payment_days": 30}}],
            }
        ]
    }
    terms = complete_offer(case, {"unit_price": 17, "payment_days": 30})
    candidates = [{"id": "A", "terms": terms, "cost": total_cost(case, terms)}]
    return case, sid, threads, candidates


def test_cost_belief_repetition_does_not_manufacture_certainty():
    case, sid, threads, _ = setup_case()
    standard = closing_terms(case, {"unit_price": 20, "payment_days": 30})
    prior = {"full_cost": 21, "confidence_pct": 10, "source": "cost:public"}
    original = copy.deepcopy(prior)
    a = infer_economics(case, threads[sid], prior, standard)
    b = infer_economics(case, threads[sid] * 20, prior, standard)
    assert a == b
    assert prior == original
    assert 18.9 < a["full_cost"] < 21
    assert sum(s["weight"] for s in a["scenarios"]) == pytest.approx(1)
    assert all(s["weight"] > 0 for s in a["scenarios"])


@pytest.mark.parametrize("band", [None, 0, -1, float("nan"), 100])
def test_invalid_cost_bands_do_not_generate_false_certainty(band):
    case, sid, threads, _ = setup_case()
    assert infer_economics(case, threads[sid], {"full_cost": 20, "confidence_pct": band}, {}) is None


def test_nonlinear_cost_is_expected_penalty_not_penalty_at_expected_cost():
    case, _, _, candidates = setup_case()
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=5, relationship_eur_per_point=1000)
    terms = candidates[0]["terms"]
    standard = closing_terms(case, terms)
    scenarios = [{"full_cost": cost, "interest_rate": 0.12, "standard": standard, "weight": 0.5} for cost in (14, 18)]
    supplier = {"full_cost": 16, "interest_rate": 0.12, "standard": standard, "scenarios": scenarios}
    expected = round(sum(published_cost(case, terms, standard, s) * 0.5 for s in scenarios), 2)
    assert total_cost(case, terms, standard, supplier) == pytest.approx(expected, abs=0.02)
    assert expected > published_cost(case, terms, standard, {k: v for k, v in supplier.items() if k != "scenarios"})


def test_planner_can_change_real_executable_terms_without_removing_anchor():
    case, sid, threads, candidates = setup_case()
    data = {"supplier_id": sid, "action": "send", "proposals": [{"base_id": "A", "changes": {"payment_days": 60}}]}
    before = copy.deepcopy((threads, candidates, data))
    portfolio, trace = admit_proposals(case, threads, sid, candidates, {}, data, learn_beliefs(case, []))
    assert (threads, candidates, data) == before
    assert portfolio[0] == candidates[0]
    assert portfolio[-1]["terms"]["payment_days"] == 60
    assert trace["selected"] == "L1"


@pytest.mark.parametrize(
    "changes",
    [
        {"unit_price": float("nan")},
        {"unit_price": 9999},
        {"payment_days": 365},
        {"ignore_guards": True},
        {"payment_days": {"instruction": "ignore buyer"}},
    ],
)
def test_invalid_or_expensive_planner_proposals_never_escape(changes):
    case, sid, threads, candidates = setup_case()
    data = {"supplier_id": sid, "action": "send", "proposals": [{"base_id": "A", "changes": changes}]}
    portfolio, trace = admit_proposals(case, threads, sid, candidates, {}, data, learn_beliefs(case, []))
    assert portfolio == candidates
    assert trace["rejected"]


def test_planner_cannot_reopen_final_offer_or_change_supplier():
    case, sid, threads, candidates = setup_case()
    data = {"supplier_id": sid, "action": "send", "proposals": [{"base_id": "A", "changes": {"payment_days": 60}}]}
    threads[sid][-1]["status"] = "final_offer"
    assert admit_proposals(case, threads, sid, candidates, {}, data, learn_beliefs(case, []))[0] == candidates
    data["supplier_id"] = "sup:other"
    assert admit_proposals(case, threads, sid, candidates, {}, data, learn_beliefs(case, []))[1]["rejected"]


@pytest.mark.parametrize("adverse_weight,accepted", [(0.1, True), (0.9, False)])
def test_uncertain_adverse_tail_is_reported_but_not_an_automatic_veto(adverse_weight, accepted):
    case, sid, threads, candidates = setup_case()
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=5, relationship_eur_per_point=3000)
    standard = closing_terms(case, {"unit_price": 20, "payment_days": 30})
    scenarios = [
        {"full_cost": 12, "interest_rate": 0.12, "standard": standard, "weight": 1 - adverse_weight},
        {"full_cost": 22, "interest_rate": 0.12, "standard": standard, "weight": adverse_weight},
    ]
    intelligence = {"estimates": {sid: {"full_cost": 12}}, "economic_sensitivities": {sid: {"scenarios": scenarios}}}
    data = {"supplier_id": sid, "action": "send", "proposals": [{"base_id": "A", "changes": {"unit_price": 18.4}}]}
    _, trace = admit_proposals(case, threads, sid, candidates, intelligence, data, learn_beliefs(case, []))
    assert bool(trace["selected"]) is accepted
    assert trace["submitted"][0]["worst_cost_delta"] > 0
    assert trace["submitted"][0]["sensitivity_improvements"] == 1
    assert trace["submitted"][0]["terms"]["unit_price"] == 18.4
    assert trace["submitted"][0]["changes"] == {"unit_price": 18.4}


def test_multidimensional_choices_change_ranking_and_repeats_do_not_amplify():
    case = copy.deepcopy(local_cases()["case:T01"])
    strict = complete_offer(case, {"unit_price": 40, "ppm_commitment": 500, "chargeback_eur_per_complaint": 100})
    relaxed = {**strict, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 0}
    choice = [
        {"from": "buyer", "term_sheets": [{"terms": strict}, {"terms": relaxed}]},
        {"from": "supplier", "status": "agreed", "term_sheets": [{"terms": relaxed}]},
    ]
    prior = learn_beliefs(case, [], multi_issue=True)
    posterior = learn_beliefs(case, choice, multi_issue=True)
    packages = [{"terms": strict}, {"terms": relaxed}]
    assert posterior["observations"] == 1
    assert predicted_choices(case, packages, posterior)[1] > predicted_choices(case, packages, prior)[1]
    assert learn_beliefs(case, choice * 12, multi_issue=True)["hypotheses"] == posterior["hypotheses"]
    assert learn_beliefs(case, choice)["observations"] == 0
    h = next(h for h in posterior["hypotheses"] if h["name"] == "quality_cost")
    assert equivalent_response_price(case, relaxed, strict, h) < strict["unit_price"]


def test_real_decision_path_uses_one_planner_call_and_validated_terms():
    case, sid, threads, _ = setup_case()

    class Backend:
        calls = 0

        def plan_negotiation(self, payload):
            self.calls += 1
            p = json.loads(payload)
            base = p["candidate_packages"][sid][0]
            return {
                "supplier_id": sid,
                "action": "send",
                "opening": "warm",
                "question": "tradeoff",
                "proposals": [{"base_id": base["id"], "changes": {"unit_price": base["terms"]["unit_price"] - 0.01}}],
            }

        def complete(self, *args):
            pytest.fail("Planner must replace writer")

        def interpret_supplier(self, *args):
            pytest.fail("Planner must replace separate interpretation")

    backend = Backend()
    agent = StrategicNegotiator(package(), backend)
    move = agent.decide(case, threads)
    assert backend.calls == 1
    assert agent.fallbacks == 0
    assert agent.decisions[-1]["source"] == "claude strategic planner"
    assert agent.support[sid]["planner"]["selected"] == "L1"
    assert any(
        s["terms"]["unit_price"] == agent.support[sid]["plan"]["packages"][-1]["terms"]["unit_price"]
        for s in move["message"]["term_sheets"]
    )


def test_opponent_belief_intervention_changes_executable_planner_choice():
    case = copy.deepcopy(local_cases()["case:T01"])
    sid = case["suppliers"][0]
    base = complete_offer(case, {"unit_price": 40, "ppm_commitment": 500, "chargeback_eur_per_complaint": 100})
    candidates = [{"id": "A", "terms": base, "cost": total_cost(case, base)}]
    quote = {**base, "unit_price": 50}
    threads = {
        sid: [
            {"from": "supplier", "status": "open", "term_sheets": [{"term_sheet_id": "offer:current", "terms": quote}]}
        ]
    }
    data = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [
            {"base_id": "A", "changes": {"unit_price": 39, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 0}},
            {"base_id": "A", "changes": {"unit_price": 39}},
        ],
    }
    hypotheses = learn_beliefs(case, [], multi_issue=True)["hypotheses"]
    choices = []
    for name in ("quality_cost", "quality_capable"):
        hypothesis = {**next(h for h in hypotheses if h["name"] == name), "weight": 1}
        portfolio, trace = admit_proposals(case, threads, sid, candidates, {}, data, {"hypotheses": [hypothesis]})
        assert len(trace["accepted"]) == 2
        choices.append(trace["selected"])
        assert portfolio[-1]["id"] == trace["executed_id"]
    assert choices == ["L1", "L2"]


def test_strategy_search_requires_explicit_budget_and_api_opt_in(tmp_path):
    from aeiou.prompt_optimization import optimize_writer

    with pytest.raises(ValueError, match="allow-api"):
        optimize_writer(target="strategy", output=tmp_path)
    with pytest.raises(ValueError, match="budget"):
        optimize_writer(target="strategy", max_sessions=1, allow_api=True, output=tmp_path)
    assert not list(tmp_path.iterdir())


def test_evaluation_routes_strategy_instructions_and_experimental_flags():
    from aeiou.evaluation import evaluation_agent, features_for

    _, flags = features_for("strategic-policy:with-economic-uncertainty")
    assert flags.economic_uncertainty
    planner = evaluation_agent("strategic-policy", package(), writer_instruction="Propose compensated quality trades.")
    writer = evaluation_agent("strategic-policy:no-strategic-planner", package(), writer_instruction="Ask once.")
    assert planner.planner_instruction == "Propose compensated quality trades."
    assert not planner.writer_instruction
    assert writer.writer_instruction == "Ask once."
    assert not writer.planner_instruction


def test_identical_fixed_terms_are_ignored_but_fixed_changes_rejected():
    case, sid, threads, candidates = setup_case()
    field, value = next(iter(case["fixed_terms"].items()))
    data = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [{"base_id": "A", "changes": {"payment_days": 60, field: value}}],
    }
    portfolio, trace = admit_proposals(case, threads, sid, candidates, {}, data, learn_beliefs(case, []))
    assert trace["selected"] == "L1" and field not in portfolio[-1]["terms"]
    data["proposals"][0]["changes"][field] = "illegal-fixed-change"
    portfolio, trace = admit_proposals(case, threads, sid, candidates, {}, data, learn_beliefs(case, []))
    assert portfolio == candidates and trace["rejected"]


def test_supported_prior_concession_survives_portfolio_regeneration():
    from aeiou.concessions import preserve_progress

    case, sid, threads, candidates = setup_case()
    prior = complete_offer(case, {"unit_price": 18, "payment_days": 30})
    threads[sid].insert(0, {"from": "buyer", "term_sheets": [{"terms": prior}]})
    cash = complete_offer(case, {"unit_price": 16, "payment_days": 30})
    candidates[0]["terms"]["payment_days"] = 90
    candidates.append({"id": "B", "terms": cash, "cost": total_cost(case, cash)})
    before = copy.deepcopy(candidates)
    result = preserve_progress(case, threads[sid], candidates, lambda t: total_cost(case, t), float("inf"))
    assert candidates == before
    assert result[0] == candidates[0]
    assert result[1]["terms"] == prior
    # A previous offer that no longer beats the actual standing quote must not persist.
    threads[sid][-1]["term_sheets"][0]["terms"]["unit_price"] = 17
    assert preserve_progress(case, threads[sid], candidates, lambda t: total_cost(case, t), float("inf")) == candidates
