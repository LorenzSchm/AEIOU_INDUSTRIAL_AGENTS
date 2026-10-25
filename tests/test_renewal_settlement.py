import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.opponent_model import learn_beliefs
from aeiou.reciprocity import responsive_packages
from aeiou.simulation import local_cases
from aeiou.strategic_planner import admit_proposals


def setup_renewal():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"].update(category_goal="routine", horizon_years=2)
    case["negotiable"] = ["unit_price", "payment_days", "contract_months"]
    case["fixed_terms"].pop("contract_months", None)
    case["term_options"].update(payment_days=[14, 30, 60, 90], contract_months=[12, 24, 36])
    quote = {"unit_price": 19.37, "payment_days": 90, "contract_months": 24}
    seller = {"from": "seller", "status": "open", "body": "Our best possible price.", "term_sheets": [{"terms": quote}]}
    bid = {**quote, "unit_price": 17}
    buyer = {"from": "buyer", "term_sheets": [{"terms": bid}]}
    events = [copy.deepcopy(x) for x in [seller, buyer, seller, buyer, seller]]

    def cost(t):
        return total_cost(case, t)

    packages = [{"id": "A", "terms": bid, "cost": cost(bid)}]
    return case, events, packages, cost


def test_stalled_renewal_exchanges_duration_for_one_tick_below_live_quote():
    case, events, packages, cost = setup_renewal()
    before = copy.deepcopy((events, packages))
    result = responsive_packages(case, events, packages, {}, cost, float("inf"))
    probe = next(p for p in result if p.get("renewal_settlement_probe"))
    assert probe["terms"] == {"unit_price": 19.36, "payment_days": 90, "contract_months": 36}
    assert probe["cost"] < cost(events[-1]["term_sheets"][0]["terms"])
    assert result[0]["terms"]["unit_price"] < probe["terms"]["unit_price"]
    assert (events, packages) == before


@pytest.mark.parametrize(
    "mode",
    [
        "first_counter",
        "still_improving",
        "final",
        "ceiling",
        "repeated",
        "prior_concession",
        "longer_costs_more",
        "no_longer_term",
        "other_category",
    ],
)
def test_renewal_settlement_is_bounded_and_not_a_general_price_concession(mode):
    case, events, packages, cost = setup_renewal()
    ceiling = float("inf")
    if mode == "first_counter":
        events = events[:3]
    elif mode == "still_improving":
        events[-1]["term_sheets"][0]["terms"]["unit_price"] -= 0.03
    elif mode == "final":
        events[-1]["status"] = "final_offer"
    elif mode == "ceiling":
        ceiling = packages[0]["cost"]
    elif mode in {"repeated", "prior_concession"}:
        events[1]["term_sheets"].append(
            {"terms": {"unit_price": 19.36 if mode == "repeated" else 19.40, "payment_days": 90, "contract_months": 36}}
        )
    elif mode == "longer_costs_more":
        original = cost

        def cost(t):
            return original(t) + (100000 if t["contract_months"] > 24 else 0)
    elif mode == "no_longer_term":
        case["term_options"]["contract_months"] = [12, 24]
    else:
        case["buyer"]["category_goal"] = "leverage"
    result = responsive_packages(case, events, packages, {}, cost, ceiling)
    assert not any(p.get("renewal_settlement_probe") for p in result)


@pytest.mark.parametrize("price", [18.5, 19.37])
def test_model_cannot_replace_settlement_with_a_different_price(price):
    case, events, packages, cost = setup_renewal()
    sid = case["suppliers"][0]
    candidates = responsive_packages(case, events, packages, {}, cost, float("inf"))
    probe = next(p for p in candidates if p.get("renewal_settlement_probe"))
    data = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [{"base_id": probe["id"], "changes": {"unit_price": price}}],
    }
    result, trace = admit_proposals(case, {sid: events}, sid, candidates, {}, data, learn_beliefs(case, events))
    assert result == candidates
    assert any("bounded renewal settlement" in r for r in trace["rejected"])


def test_different_model_proposal_retains_settlement_instead_of_dropping_it():
    case, events, packages, cost = setup_renewal()
    sid = case["suppliers"][0]
    candidates = responsive_packages(case, events, packages, {}, cost, float("inf"))
    probe = next(p for p in candidates if p.get("renewal_settlement_probe"))
    data = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [{"base_id": "A", "changes": {"unit_price": 16.9, "payment_days": 60}}],
    }
    result, trace = admit_proposals(case, {sid: events}, sid, candidates, {}, data, learn_beliefs(case, events))
    assert trace["accepted"]
    assert probe in result
