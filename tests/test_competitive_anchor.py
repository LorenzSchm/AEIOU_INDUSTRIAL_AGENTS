import copy

import pytest
from arena_kit.cost import complete_offer, total_cost

from aeiou.competitive_anchor import competitive_packages
from aeiou.opponent_model import learn_beliefs
from aeiou.simulation import local_cases
from aeiou.strategic_planner import admit_proposals


def fixture():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"].update(category_goal="leverage", alternative={"kind": "none", "terms": {}})
    base = complete_offer(case, {"unit_price": 32, "payment_days": 30})
    threads = {
        "seller": [
            {
                "from": "seller",
                "status": "open",
                "term_sheets": [{"term_sheet_id": "own", "terms": {**base, "unit_price": 40}}],
            }
        ],
        "rival": [
            {
                "from": "seller",
                "status": "open",
                "term_sheets": [{"term_sheet_id": "other", "terms": {**base, "unit_price": 30}}],
            }
        ],
    }
    packages = [{"id": "A", "terms": base, "cost": total_cost(case, base)}]
    return case, threads, packages


def test_first_bid_uses_cheaper_binding_competition_without_changing_nonprice_terms():
    case, threads, packages = fixture()
    before = copy.deepcopy((threads, packages))
    revised = competitive_packages(case, threads, "seller", packages, {})
    assert (threads, packages) == before
    assert revised[0]["terms"]["unit_price"] < packages[0]["terms"]["unit_price"]
    assert revised[0]["competitive_anchor"]["offer_id"] == "other"
    assert revised[0]["terms"]["payment_days"] == packages[0]["terms"]["payment_days"]


def test_competition_does_not_retract_sent_prices_or_use_closed_rival():
    case, threads, packages = fixture()
    threads["rival"][0]["status"] = "walked_away"
    assert competitive_packages(case, threads, "seller", packages, {}) == packages
    threads["rival"][0]["status"] = "open"
    threads["seller"].insert(0, {"from": "buyer", "term_sheets": [{"terms": packages[0]["terms"]}]})
    assert competitive_packages(case, threads, "seller", packages, {}) == packages


def test_rejected_rival_contract_cannot_set_the_anchor():
    case, threads, packages = fixture()
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    threads["rival"][0]["term_sheets"][0]["terms"]["payment_days"] = 14
    assert competitive_packages(case, threads, "seller", packages, {}) == packages


def test_price_amendment_replaces_same_structure_instead_of_sending_two_prices():
    case, threads, packages = fixture()
    del threads["rival"]
    data = {"supplier_id": "seller", "action": "send", "proposals": [{"base_id": "A", "changes": {"unit_price": 31}}]}
    revised, trace = admit_proposals(case, threads, "seller", packages, {}, data, learn_beliefs(case, []))
    assert trace["selected"] == "L1" and trace["executed_id"] == "A"
    assert len(revised) == 1 and revised[0]["terms"]["unit_price"] == 31


@pytest.mark.parametrize("prior_turn", [False, True])
def test_model_cannot_skip_controlled_price_steps_before_closing(prior_turn):
    case, threads, packages = fixture()
    del threads["rival"]
    if prior_turn:
        prior = {**packages[0]["terms"], "unit_price": 31}
        threads["seller"].insert(0, {"from": "buyer", "term_sheets": [{"terms": prior}]})
    data = {"supplier_id": "seller", "action": "send", "proposals": [{"base_id": "A", "changes": {"unit_price": 33}}]}
    revised, trace = admit_proposals(case, threads, "seller", packages, {}, data, learn_beliefs(case, []))
    assert revised == packages
    assert any("controller schedule" in reason for reason in trace["rejected"])


def test_model_cannot_hide_early_concession_in_new_cash_structure():
    case, threads, packages = fixture()
    del threads["rival"]
    data = {
        "supplier_id": "seller",
        "action": "send",
        "proposals": [
            {"base_id": "A", "changes": {"unit_price": 35, "payment_days": 14}},
        ],
    }
    revised, trace = admit_proposals(case, threads, "seller", packages, {}, data, learn_beliefs(case, []))
    assert revised == packages
    assert any("concession envelope" in reason for reason in trace["rejected"])


def test_closing_phase_can_still_make_a_bounded_price_concession():
    case, threads, packages = fixture()
    del threads["rival"]
    case["round_limit"] = 2
    threads["seller"].insert(0, {"from": "buyer", "term_sheets": [{"terms": packages[0]["terms"]}]})
    data = {"supplier_id": "seller", "action": "send", "proposals": [{"base_id": "A", "changes": {"unit_price": 33}}]}
    revised, trace = admit_proposals(case, threads, "seller", packages, {}, data, learn_beliefs(case, []))
    assert trace["selected"] == "L1"
    assert revised[0]["terms"]["unit_price"] == 33


def test_followup_uses_sent_bid_not_the_sellers_original_anchor():
    case, threads, packages = fixture()
    previous = {**packages[0]["terms"], "unit_price": 25}
    quote = {**previous, "unit_price": 30}
    threads["seller"].extend(
        [
            {"from": "buyer", "term_sheets": [{"terms": previous}]},
            {"from": "seller", "status": "open", "term_sheets": [{"term_sheet_id": "counter", "terms": quote}]},
        ]
    )
    before = copy.deepcopy((threads, packages))
    revised = competitive_packages(case, threads, "seller", packages, {})
    assert (threads, packages) == before
    assert 25 < revised[0]["terms"]["unit_price"] <= 26.01
    assert revised[0]["concession_progress"]["prior_price"] == 25
    # Late closure and binding agreement must not be reopened or slowed down.
    threads["seller"][-1]["status"] = "agreed"
    assert competitive_packages(case, threads, "seller", packages, {}) == packages
    threads["seller"][-1]["status"] = "open"
    case["round_limit"] = 3
    assert competitive_packages(case, threads, "seller", packages, {}) == packages


def test_single_supplier_policy_is_unchanged_by_rfq_continuation():
    case, threads, packages = fixture()
    del threads["rival"]
    threads["seller"].insert(
        0, {"from": "buyer", "term_sheets": [{"terms": {**packages[0]["terms"], "unit_price": 25}}]}
    )
    assert competitive_packages(case, threads, "seller", packages, {}) == packages
