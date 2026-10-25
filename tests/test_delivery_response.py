import copy
import json

import pytest
from arena_kit.cost import total_cost

from aeiou.config import data_root
from aeiou.observed_tradeoffs import delivery_response


def setup():
    case = json.loads((data_root() / "cases/practice/P10.json").read_text())
    case.update(case_id="case:unseen-service", suppliers=["sup:unseen"])
    a = {"unit_price": 28, "volume_per_year": 23400, "contract_months": 24, "lead_time_days": 42}
    packages = [
        {"id": "A", "terms": a},
        {"id": "B", "terms": {**a, "unit_price": 31, "contract_months": 36}},
        {"id": "C", "terms": {**a, "unit_price": 27, "lead_time_days": 56}},
    ]
    events = [
        {"from": "buyer", "term_sheets": [{"terms": p["terms"]} for p in packages]},
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"terms": {**a, "unit_price": 34, "contract_months": 36}},
                {"terms": {**a, "unit_price": 32, "lead_time_days": 56}},
            ],
        },
    ]
    return case, events, packages


def apply(case, events, packages, ceiling=float("inf")):
    return delivery_response(case, events, packages, {}, lambda t: total_cost(case, t), ceiling)


def test_compares_full_cost_and_preserves_anchor_and_existing_duration_limit():
    case, events, packages = setup()
    original = copy.deepcopy((case, events, packages))
    result = apply(case, events, packages)
    assert (case, events, packages) == original
    assert result[:2] == packages[:2]
    assert result[2]["terms"] == {**packages[2]["terms"], "unit_price": 31.36, "contract_months": 36}
    assert result[2]["observed_tradeoff"] and result[2]["cost"] < result[2]["standing_cost"]
    assert "lead_time_days" in result[2]["reference_quote"]


def test_high_lateness_cost_blocks_apparently_cheaper_unit_price():
    case, events, packages = setup()
    case["buyer"]["late_cost_per_day"] = 100000
    assert apply(case, events, packages) == packages


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_terminal_offer_is_preserved(status):
    case, events, packages = setup()
    events[-1]["status"] = status
    assert apply(case, events, packages) == packages


def test_no_unquoted_delivery_trade_on_first_contact_or_single_quote():
    case, events, packages = setup()
    assert apply(case, events[1:], packages) == packages
    events[-1]["term_sheets"].pop()
    assert apply(case, events, packages) == packages


def test_ceiling_and_previous_price_concession_are_respected():
    case, events, packages = setup()
    assert apply(case, events, packages, 1) == packages
    prior = {**packages[2]["terms"], "contract_months": 36, "unit_price": 31.7}
    events[0]["term_sheets"].append({"terms": prior})
    assert apply(case, events, packages)[2]["terms"]["unit_price"] == 31.7


def test_no_extra_commitment_outside_existing_portfolio():
    case, events, packages = setup()
    packages[1]["terms"]["contract_months"] = 24
    assert apply(case, events, packages)[2]["terms"]["contract_months"] == 24
