import copy
import json

import pytest
from arena_kit.cost import closing_terms

from aeiou.config import data_root
from aeiou.payment_extension import payment_extension


def scenario():
    case = json.loads((data_root() / "cases/practice/P03.json").read_text())
    case.update(case_id="case:unseen", suppliers=["sup:new"])
    early = {"unit_price": 430.15, "payment_days": 30, "contract_months": 24, "volume_per_year": 4580}
    late = {**early, "unit_price": 431.61, "payment_days": 60}
    buyer = {"from": "buyer", "term_sheets": [{"terms": {**early, "unit_price": 400}}]}
    seller = {
        "from": "seller",
        "status": "open",
        "body": "Our base prices are frozen.",
        "term_sheets": [{"terms": early}, {"terms": late}],
    }
    packages = [{"id": "A", "terms": {**early, "unit_price": 400}}, {"id": "B", "terms": {**late, "unit_price": 401}}]
    supplier = {"full_cost": 403.21, "interest_rate": 0.12, "standard": closing_terms(case, early)}
    return case, [buyer, seller], packages, supplier


def apply(case, events, packages, supplier, ceiling=float("inf")):
    return payment_extension(case, events, packages, {}, supplier, 5, ceiling)


def test_compensated_extension_is_legal_bounded_and_does_not_mutate_inputs():
    case, events, packages, supplier = scenario()
    before = copy.deepcopy((case, events, packages, supplier))
    result = apply(case, events, packages, supplier)
    assert (case, events, packages, supplier) == before
    assert result[0] == packages[0]
    probe = result[1]
    assert probe["payment_extension"]
    assert probe["terms"]["payment_days"] == 90
    assert probe["terms"]["unit_price"] == 433.07
    assert probe["direct_saving"] > 13000
    assert probe["worst_sensitivity_delta"] < 0


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_never_reopens_terminal_quotes(status):
    c, e, p, s = scenario()
    e[-1]["status"] = status
    assert apply(c, e, p, s) == p


def test_requires_live_comparable_quotes_without_relying_on_supplier_wording():
    c, e, p, s = scenario()
    assert apply(c, e[1:], p, s) == p
    e[-1]["body"] = "We can reduce prices further."
    assert apply(c, e, p, s)[1].get("payment_extension")
    e[-1]["body"] = "Our prices are frozen."
    e[-1]["term_sheets"][1]["terms"]["contract_months"] = 36
    assert apply(c, e, p, s) == p
    e[-1]["term_sheets"].pop()
    assert apply(c, e, p, s) == p


def test_no_repeat_retraction_or_ceiling_violation():
    c, e, p, s = scenario()
    result = apply(c, e, p, s)
    assert apply(c, e, p, s, 1) == p
    t = result[1]["terms"]
    e[0]["term_sheets"].append({"terms": t})
    assert apply(c, e, p, s) == p
    e[0]["term_sheets"][-1]["terms"] = {**t, "unit_price": t["unit_price"] + 1}
    assert apply(c, e, p, s) == p


def test_no_extension_beyond_legal_options_or_excessive_financing_premium():
    c, e, p, s = scenario()
    c["term_options"]["payment_days"] = [14, 30, 60]
    assert apply(c, e, p, s) == p
    c["term_options"]["payment_days"].append(90)
    e[-1]["term_sheets"][1]["terms"]["unit_price"] = 450
    assert apply(c, e, p, s) == p
