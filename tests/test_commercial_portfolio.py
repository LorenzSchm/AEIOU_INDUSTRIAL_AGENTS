import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.commercial_portfolio import portfolio_probes, structure
from aeiou.grounding import package_rationale
from aeiou.optimizer import valid_terms
from aeiou.research import package
from aeiou.scenario_planner import equivalent_package
from aeiou.simulation import local_cases
from aeiou.strategic import StrategicNegotiator
from aeiou.strategy_policy import StrategicFeatures


def opening(terms, status="open"):
    return [
        {
            "from": "supplier",
            "status": status,
            "body": "Please review our quote.",
            "term_sheets": [{"term_sheet_id": "offer:unseen", "terms": terms}],
        }
    ]


def quality_case():
    case = copy.deepcopy(local_cases()["case:T01"])
    case.update(case_id="case:NEW-QUALITY", suppliers=["sup:NEW"])
    case["term_options"] = {"ppm_commitment": [200, 900, 1800, 4000], "chargeback_eur_per_complaint": [0, 60, 120, 240]}
    case["buyer"].update(cost_per_defect=240, must_haves=[{"field": "ppm_commitment", "op": "<=", "value": 1800}])
    return case


def test_quality_portfolio_reaches_supplier_with_multiple_legal_liability_prices():
    case = quality_case()
    events = opening({"unit_price": 29, "ppm_commitment": 4000, "chargeback_eur_per_complaint": 0})
    agent = StrategicNegotiator(package())
    move = agent.decide(case, {"sup:NEW": events})
    terms = [s["terms"] for s in move["message"]["term_sheets"]]
    assert len({t["chargeback_eur_per_complaint"] for t in terms}) == 3
    assert all(valid_terms(case, t) and t["ppm_commitment"] <= 1800 for t in terms)
    assert all(t["ppm_commitment"] == 1800 for t in terms)
    assert "reduces complaint liability" in move["message"]["body"]
    for p in agent.support["sup:NEW"]["plan"]["packages"]:
        if p.get("portfolio_probe"):
            assert p["cost"] <= p["portfolio_ceiling"]
            assert p["cost"] < p["standing_cost"]


def test_new_portfolio_is_ablatable_without_changing_protected_anchor():
    case = quality_case()
    events = opening({"unit_price": 29, "ppm_commitment": 1800, "chargeback_eur_per_complaint": 240})
    plans = []
    for enabled in (False, True):
        agent = StrategicNegotiator(package(), features=StrategicFeatures(multi_issue_portfolio=enabled))
        agent.decide(case, {"sup:NEW": events})
        plans.append(agent.support["sup:NEW"]["plan"]["packages"])
    assert plans[0][0]["terms"] == plans[1][0]["terms"]
    assert any(p.get("portfolio_probe") for p in plans[1])
    assert not any(p.get("portfolio_probe") for p in plans[0])


@pytest.mark.parametrize("status", ["agreed", "final_offer", "closed"])
def test_portfolio_does_not_reopen_terminal_negotiations(status):
    case = quality_case()
    base = {"unit_price": 20, "ppm_commitment": 1800, "chargeback_eur_per_complaint": 240}
    assert (
        portfolio_probes(
            case, opening(base, status), base, lambda t: total_cost(case, t), None, None, equivalent_package
        )
        == []
    )


def test_price_compensation_never_withdraws_a_previous_same_structure_concession():
    case = quality_case()
    base = {"unit_price": 20, "ppm_commitment": 1800, "chargeback_eur_per_complaint": 240}
    prior = {**base, "unit_price": 28, "chargeback_eur_per_complaint": 120}
    events = opening({**base, "unit_price": 30})
    events += [{"from": "buyer", "term_sheets": [{"terms": prior}]}] + opening({**base, "unit_price": 29})
    probes = portfolio_probes(case, events, base, lambda t: total_cost(case, t), None, None, equivalent_package)
    assert all(structure(p["terms"]) != structure(prior) or p["terms"]["unit_price"] >= 28 for p in probes)


def test_index_option_remains_available_with_a_different_payment_structure():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(
        case_id="case:NEW-RISK", suppliers=["sup:NEW"], negotiable=["unit_price", "payment_days", "index_clause"]
    )
    case["fixed_terms"].pop("index_clause", None)
    clause = {"series": "UNSEEN", "material_share": 0.27, "deadband_pct": 4, "base_month": "2026-01"}
    case["term_options"] = {"payment_days": [21, 45, 75], "index_clause": [None, clause]}
    case["buyer"].update(category_goal="leverage", index_paths={"UNSEEN": 0.07})
    agent = StrategicNegotiator(package())
    move = agent.decide(case, {"sup:NEW": opening({"unit_price": 29, "payment_days": 45, "index_clause": None})})
    terms = [s["terms"] for s in move["message"]["term_sheets"]]
    assert any(t["index_clause"] is None for t in terms)
    assert any(t["index_clause"] == clause for t in terms)
    assert all(valid_terms(case, t) for t in terms)
    assert "material-price risk" in move["message"]["body"]


def test_rationale_does_not_invent_price_or_service_trades():
    terms = [{"unit_price": 20, "payment_days": 21}, {"unit_price": 20, "payment_days": 75}]
    assert package_rationale(terms) == ""


def test_first_offer_includes_bounded_timely_delivery_and_longer_commitment():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(
        case_id="case:NEW-SERVICE",
        suppliers=["sup:NEW"],
        negotiable=["unit_price", "lead_time_days", "contract_months"],
    )
    for field in ("lead_time_days", "contract_months"):
        case["fixed_terms"].pop(field, None)
    case["fixed_terms"]["payment_days"] = 30
    case["term_options"] = {"lead_time_days": [21, 28, 49], "contract_months": [12, 24, 36]}
    case["buyer"].update(need_lead_time_days=28, horizon_years=2, late_cost_per_day=1000, category_goal="leverage")
    events = opening({"unit_price": 30, "lead_time_days": 49, "contract_months": 12})
    agent = StrategicNegotiator(package())
    move = agent.decide(case, {"sup:NEW": events})
    plan = agent.support["sup:NEW"]["plan"]["packages"]
    probe = next(p for p in plan if p.get("issue") == "delivery")
    assert probe["terms"]["lead_time_days"] == 28
    assert probe["terms"]["contract_months"] >= 24
    assert probe["cost"] <= probe["portfolio_ceiling"] < probe["standing_cost"]
    assert any(s["terms"] == probe["terms"] for s in move["message"]["term_sheets"])


def test_existing_index_probe_is_not_replaced_by_redundant_risk_packages():
    from aeiou.commercial_portfolio import merge_portfolio

    fixed = {"unit_price": 20, "payment_days": 90, "index_clause": None}
    indexed = {**fixed, "unit_price": 19, "index_clause": {"series": "TEST"}}
    existing = [
        {"id": "A", "terms": fixed},
        {"id": "B", "terms": {**fixed, "payment_days": 14}},
        {"id": "C", "terms": indexed},
    ]
    probes = [{"issue": "index", "terms": {**indexed, "payment_days": 30}}]
    assert merge_portfolio(existing, probes) == existing


def test_grounded_writer_receives_one_unambiguous_output_contract():
    prompt = StrategicNegotiator(package()).system_prompt()
    assert "You choose only a communication act" in prompt
    assert "To propose your own" not in prompt
    assert "Copy supplier_id and candidate_ids exactly" in prompt


@pytest.mark.parametrize("path, expected_deadband", [(0.08, 6), (-0.08, 1)])
@pytest.mark.parametrize("reverse", [False, True])
def test_index_exploration_uses_economics_not_enum_order(path, expected_deadband, reverse):
    from arena_kit.cost import complete_offer

    from aeiou.scenario_planner import plan_packages

    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(case_id="case:UNSEEN-INDEX", suppliers=["sup:NEW"], negotiable=["unit_price", "index_clause"])
    case["fixed_terms"].pop("index_clause", None)
    clauses = [{"series": "NEW", "material_share": 0.31, "deadband_pct": d, "base_month": "2026-02"} for d in (1, 4, 6)]
    case["term_options"] = {"index_clause": [None, *(reversed(clauses) if reverse else clauses)]}
    case["buyer"].update(category_goal="leverage", index_paths={"NEW": path})
    base = complete_offer(case, {"unit_price": 25, "index_clause": None})
    candidate = {"id": "A", "label": "Protected", "terms": base, "cost": total_cost(case, base)}
    plan = plan_packages(
        case,
        opening({**base, "unit_price": 30}),
        "sup:NEW",
        {},
        [candidate],
        {"moves_left": 8, "preferences": []},
        use_counteroffers=False,
        use_portfolio=False,
    )
    probe = next(p for p in plan["packages"] if p["id"] == "C")
    assert plan["packages"][0]["terms"] == base
    assert probe["terms"]["index_clause"]["deadband_pct"] == expected_deadband
    assert probe["cost"] <= candidate["cost"] + 0.001
    assert valid_terms(case, probe["terms"])
