import copy
import json

import pytest
from arena_kit.cost import closing_terms

from aeiou.config import data_root
from aeiou.economic_beliefs import total_cost
from aeiou.payment_focus import focused_payment_packages


def scenario():
    c = json.loads((data_root() / "cases/practice/P03.json").read_text())
    c.update(case_id="case:unseen", suppliers=["sup:unseen"])
    q = {"unit_price": 438.21, "payment_days": 30, "contract_months": 12, "volume_per_year": 4580}
    e = [{"from": "seller", "status": "open", "term_sheets": [{"terms": q}]}]
    p = [{"id": "A", "terms": {**q, "unit_price": 415.13, "payment_days": 14, "contract_months": 24}}]
    s = {"full_cost": 403.21, "interest_rate": 0.12, "standard": closing_terms(c, q)}
    return c, e, p, s


def test_opening_discovers_long_payment_without_changing_case_or_horizon():
    c, e, p, s = scenario()
    before = copy.deepcopy((c, e, p, s))
    result = focused_payment_packages(c, e, p, {}, s, 90)
    assert (c, e, p, s) == before
    assert result[0]["terms"]["payment_days"] == 90
    assert result[0]["terms"]["unit_price"] == 415.13
    assert result[0]["cost"] < total_cost(c, e[0]["term_sheets"][0]["terms"], {}, s)


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_terminal_offers_are_unchanged(status):
    c, e, p, s = scenario()
    e[0]["status"] = status
    assert focused_payment_packages(c, e, p, {}, s, 90) == p


def test_illegal_target_and_unaffordable_trade_do_not_bypass_safety():
    c, e, p, s = scenario()
    assert focused_payment_packages(c, e, p, {}, s, 999) == p
    c["buyer"]["alternative"] = {"kind": "quote", "terms": closing_terms(c, {**p[0]["terms"], "unit_price": 100})}
    assert focused_payment_packages(c, e, p, {}, s, 90) == p


def test_current_quote_counter_keeps_coverage_and_prior_concessions():
    c, e, p, s = scenario()
    q = {**p[0]["terms"], "unit_price": 433.07, "payment_days": 90}
    e += [
        {"from": "buyer", "term_sheets": [{"terms": {**q, "unit_price": 429}}]},
        {"from": "seller", "status": "open", "term_sheets": [{"terms": q}]},
    ]
    result = focused_payment_packages(c, e, p, {}, s, 90)
    assert result[0]["terms"]["payment_days"] == 90
    assert 429 <= result[0]["terms"]["unit_price"] < 433.07
    assert result[0]["terms"]["contract_months"] == 24
