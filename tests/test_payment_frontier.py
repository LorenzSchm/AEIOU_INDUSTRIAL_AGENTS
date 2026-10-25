import copy

import pytest
from arena_kit.cost import closing_terms, complete_offer, total_cost

from aeiou.opponent_model import learn_beliefs
from aeiou.reciprocity import responsive_packages
from aeiou.simulation import local_cases
from aeiou.strategic_planner import admit_proposals
from aeiou.supplier_signals import observed_signals


def setup_menu():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"].update(
        need_per_year=1000, category_goal="stabilise", relationship_eur_per_point=1000, sustainable_margin_pct=8
    )
    case["fixed_terms"]["volume_per_year"] = 1000
    model = {
        "full_cost": 95,
        "interest_rate": 0.12,
        "standard": closing_terms(case, {"unit_price": 100, "payment_days": 30}),
    }
    quotes = [
        complete_offer(case, {"unit_price": round(100 / (1 - 0.04 * (d - 30) / 360), 2), "payment_days": d})
        for d in (14, 30)
    ]
    seller = {
        "from": "seller",
        "status": "open",
        "body": "Our guidelines prohibit further price reductions.",
        "term_sheets": [{"terms": q} for q in quotes],
    }
    base = complete_offer(case, {"unit_price": 98, "payment_days": 14})
    buyer = {"from": "buyer", "term_sheets": [{"terms": base}]}

    def cost(t):
        return total_cost(case, t, supplier=model)

    packages = [{"id": "A", "terms": base, "cost": cost(base)}]
    return case, [seller, buyer, copy.deepcopy(seller)], packages, cost


def test_extend_longest_quoted_payment_instead_of_repricing_existing_cash_option():
    case, events, packages, cost = setup_menu()
    before = copy.deepcopy((events, packages))
    results = responsive_packages(case, events, packages, {}, cost, float("inf"))
    probe = next(p for p in results if p.get("payment_frontier_probe"))
    assert probe["terms"] == {"unit_price": 100, "payment_days": 45}
    assert probe["reference_quote"]["payment_days"] == 30
    cheapest = min(cost(s["terms"]) for s in events[-1]["term_sheets"])
    assert probe["cost"] <= cheapest * 1.0025
    assert total_cost(case, probe["terms"]) < min(total_cost(case, s["terms"]) for s in events[-1]["term_sheets"])
    assert before == (events, packages)
    events[-1]["term_sheets"].reverse()
    reversed_result = responsive_packages(case, events, packages, {}, cost, float("inf"))
    assert next(p for p in reversed_result if p.get("payment_frontier_probe"))["terms"] == probe["terms"]


@pytest.mark.parametrize("mode", ["ceiling", "already_tried", "final", "no_restriction"])
def test_frontier_probe_respects_limits_and_does_not_repeat_failed_request(mode):
    case, events, packages, cost = setup_menu()
    ceiling = float("inf")
    if mode == "ceiling":
        ceiling = 1
    elif mode == "already_tried":
        events[1]["term_sheets"].append({"terms": {"unit_price": 100, "payment_days": 45}})
    elif mode == "final":
        events[-1]["status"] = "final_offer"
    else:
        events[-1]["body"] = "We can discuss price and terms."
    result = responsive_packages(case, events, packages, {}, cost, ceiling)
    assert not any(p.get("payment_frontier_probe") for p in result)


def test_model_cannot_short_circuit_term_exploration_by_reoffering_known_cash_quote():
    case, events, packages, cost = setup_menu()
    candidates = responsive_packages(case, events, packages, {}, cost, float("inf"))
    sid = case["suppliers"][0]
    old = events[-1]["term_sheets"][0]["terms"]
    data = {"supplier_id": sid, "action": "send", "proposals": [{"base_id": "A", "changes": old}]}
    result, trace = admit_proposals(case, {sid: events}, sid, candidates, {}, data, learn_beliefs(case, events))
    assert result == candidates
    assert any("bypass the controller" in r for r in trace["rejected"])


@pytest.mark.parametrize(
    "body,restricted",
    [
        ("Corporate guidelines strictly prohibit further price reductions.", True),
        ("Our policy forbids price cuts.", True),
        ("We do not prohibit price reductions.", False),
        ("Our rules no longer prohibit price reductions.", False),
    ],
)
def test_price_restriction_paraphrases_and_negation(body, restricted):
    signals = observed_signals([{"from": "supplier", "body": body}])
    assert any(s["kind"] == "price_freeze" for s in signals) is restricted


def test_reciprocate_each_quoted_date_instead_of_abandoning_one_for_cheapest_quote():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["negotiable"].append("effective_date")
    case["term_options"]["effective_date"] = ["2028-01-01", "2028-04-01"]
    case["buyer"]["current_unit_price"] = 100
    a = complete_offer(case, {"unit_price": 110, "payment_days": 30, "effective_date": "2028-01-01"})
    b = {**a, "unit_price": 115, "effective_date": "2028-04-01"}
    seller = {
        "from": "seller",
        "status": "open",
        "body": "Here are revised options.",
        "term_sheets": [{"terms": {**a, "unit_price": 120}}, {"terms": {**b, "unit_price": 125}}],
    }
    first = {"from": "buyer", "term_sheets": [{"terms": a}, {"terms": b}]}
    second = {"from": "buyer", "term_sheets": [{"terms": {**a, "unit_price": 111}}, {"terms": b}]}
    packages = [
        {"id": "A", "terms": a, "cost": total_cost(case, a)},
        {"id": "B", "terms": b, "cost": total_cost(case, b)},
    ]
    result = responsive_packages(
        case, [seller, first, seller, second, seller], packages, {}, lambda t: total_cost(case, t), float("inf")
    )
    assert result[0]["terms"]["unit_price"] == 113.25
    assert result[1]["terms"]["unit_price"] == 117.5
    assert all(p["cost"] < min(total_cost(case, s["terms"]) for s in seller["term_sheets"]) for p in result)
