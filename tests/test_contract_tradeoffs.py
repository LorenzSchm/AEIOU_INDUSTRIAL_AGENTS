import copy

import pytest
from arena_kit.cost import complete_offer, total_cost

from aeiou.contract_tradeoffs import compensated_date
from aeiou.opponent_model import learn_beliefs
from aeiou.simulation import local_cases
from aeiou.strategic_planner import admit_proposals, proposal_limits


def date_case():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["negotiable"].append("effective_date")
    case["term_options"]["effective_date"] = ["2028-01-01", "2028-04-01", "2028-07-01"]
    case["buyer"]["current_unit_price"] = 100
    return case


@pytest.mark.parametrize("price,allowed", [(119, False), (110, True)])
def test_earlier_increase_needs_real_compensation(price, allowed):
    case = date_case()
    reference = complete_offer(case, {"unit_price": 120, "payment_days": 60, "effective_date": "2028-07-01"})
    proposal = {**reference, "unit_price": price, "effective_date": "2028-04-01"}
    assert compensated_date(case, proposal, reference) is allowed


def test_earlier_discount_is_beneficial_and_fixed_dates_are_untouched():
    case = date_case()
    reference = complete_offer(case, {"unit_price": 90, "effective_date": "2028-07-01"})
    assert compensated_date(case, {**reference, "effective_date": "2028-01-01"}, reference)
    case["negotiable"].remove("effective_date")
    assert compensated_date(case, {}, {})


def test_model_cannot_restore_uncompensated_early_date():
    case = date_case()
    sid = case["suppliers"][0]
    terms = complete_offer(case, {"unit_price": 120, "payment_days": 60, "effective_date": "2028-07-01"})
    candidates = [{"id": "A", "terms": terms, "cost": total_cost(case, terms)}]
    threads = {sid: [{"from": "supplier", "status": "open", "term_sheets": []}]}
    limits = proposal_limits(case, threads, sid, candidates, {})
    assert limits["earlier_effective_date"]["reference_date"] == terms["effective_date"]
    assert limits["earlier_effective_date"]["max_direct_contract_cost"] == total_cost(case, terms)
    proposal = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [{"base_id": "A", "changes": {"unit_price": 119, "effective_date": "2028-04-01"}}],
    }
    result, trace = admit_proposals(case, threads, sid, candidates, {}, proposal, learn_beliefs(case, []))
    assert result == candidates
    assert any("Earlier effective date" in r for r in trace["rejected"])
