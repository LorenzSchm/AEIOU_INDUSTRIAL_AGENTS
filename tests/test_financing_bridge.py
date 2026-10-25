import copy
import json

import pytest
from arena_kit.cost import total_cost

from aeiou.config import data_root
from aeiou.financing_bridge import financing_bridge


def setup():
    case = json.loads((data_root() / "cases/practice/P04.json").read_text())
    case.update(case_id="case:unseen-renewal", suppliers=["sup:unseen"])
    a = {"unit_price": 4.09, "payment_days": 90, "contract_months": 24}
    packages = [{"id": "A", "terms": a}, {"id": "C", "terms": {**a, "contract_months": 36}}]
    events = [
        {"from": "buyer", "term_sheets": [{"terms": a}]},
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"terms": {**a, "unit_price": 4.94}},
                {"terms": {**a, "unit_price": 4.87, "payment_days": 14}},
            ],
        },
    ]
    return case, events, packages


def apply(case, events, packages, ceiling=float("inf")):
    return financing_bridge(case, events, packages, {}, lambda t: total_cost(case, t), ceiling)


def test_intermediate_exchange_is_bounded_and_keeps_anchor():
    case, events, packages = setup()
    original = copy.deepcopy((case, events, packages))
    result = apply(case, events, packages)
    assert (case, events, packages) == original
    assert result[0] == packages[0]
    assert result[1]["terms"] == {"unit_price": 4.88, "payment_days": 45, "contract_months": 36}
    assert result[1]["cost"] < min(total_cost(case, s["terms"]) for s in events[-1]["term_sheets"])
    assert result[1]["observed_tradeoff"]


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_final_and_closed_states_preserved(status):
    case, events, packages = setup()
    events[-1]["status"] = status
    assert apply(case, events, packages) == packages


def test_single_quote_bundled_difference_and_first_contact_do_not_imply_financing_curve():
    case, events, packages = setup()
    assert apply(case, events[1:], packages) == packages
    events[-1]["term_sheets"][1]["terms"]["contract_months"] = 12
    assert apply(case, events, packages) == packages
    events[-1]["term_sheets"].pop()
    assert apply(case, events, packages) == packages


def test_scope_ceiling_and_prior_concession_are_preserved():
    case, events, packages = setup()
    assert apply(case, events, packages, 1) == packages
    events[0]["term_sheets"].append({"terms": {"unit_price": 4.9, "payment_days": 45, "contract_months": 36}})
    result = apply(case, events, packages)
    assert not any(p["terms"]["payment_days"] == 45 and p["terms"]["unit_price"] < 4.9 for p in result)
    case["buyer"]["category_goal"] = "leverage"
    assert apply(case, events, packages) == packages


def test_no_duplicate_prices_on_same_structure():
    case, events, packages = setup()
    packages.insert(1, {"id": "B", "terms": {"unit_price": 4.1, "payment_days": 45, "contract_months": 36}})
    result = apply(case, events, packages)
    assert sum(p["terms"]["payment_days"] == 45 and p["terms"]["contract_months"] == 36 for p in result) == 1
