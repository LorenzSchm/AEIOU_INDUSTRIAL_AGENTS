import copy
import json

import pytest

from aeiou.config import data_root
from aeiou.structure_focus import focused_structure


def scenario():
    case = json.loads((data_root() / "cases/practice/P11.json").read_text())
    case.update(case_id="case:new", suppliers=["sup:new"])
    q = {"unit_price": 49.5, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 500}
    ev = [{"from": "seller", "status": "open", "term_sheets": [{"terms": q}]}]
    ps = [{"id": "A", "terms": {**q, "unit_price": 40}}, {"id": "B", "terms": {**q, "unit_price": 41}}]
    return case, ev, ps


def apply(c, e, p, focus=None):
    return focused_structure(c, e, p, {}, None, focus or {"ppm_commitment": 1500, "chargeback_eur_per_complaint": 0})


def test_early_liability_focus_keeps_quality_and_deduplicates_prices():
    c, e, p = scenario()
    original = copy.deepcopy((c, e, p))
    out = apply(c, e, p)
    assert len(out) == 1
    assert out[0]["id"] == "A"
    assert out[0]["terms"] == {"unit_price": 40, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 0}
    assert out[0]["structure_focus"]
    assert (c, e, p) == original


def test_invalid_opening_quality_is_only_a_cost_reference_not_a_permitted_proposal():
    c, e, p = scenario()
    e[-1]["term_sheets"][0]["terms"]["ppm_commitment"] = 6400
    result = apply(c, e, p)
    assert result[0]["structure_focus"]
    assert result[0]["terms"]["ppm_commitment"] == 1500


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_terminal_quotes_remain_unchanged(status):
    c, e, p = scenario()
    e[-1]["status"] = status
    assert apply(c, e, p) == p


def test_illegal_focus_and_quality_breach_are_not_admitted():
    c, e, p = scenario()
    assert apply(c, e, p, {"payment_days": 90}) == p
    assert apply(c, e, p, {"ppm_commitment": 2500}) == p
    assert apply(c, e, p, {"ppm_commitment": 999}) == p


def test_counter_to_live_focused_quote_preserves_prior_concession():
    c, e, p = scenario()
    q = {"unit_price": 45, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 0}
    e += [
        {"from": "buyer", "term_sheets": [{"terms": {**q, "unit_price": 44.5}}]},
        {"from": "seller", "status": "open", "term_sheets": [{"terms": q}]},
    ]
    out = apply(c, e, p)
    assert out[0]["structure_focus"]
    assert out[0]["terms"]["unit_price"] >= 44.5
    assert out[0]["terms"]["unit_price"] < 45


def test_expensive_structure_cannot_replace_a_cheaper_live_quote():
    c, e, p = scenario()
    e[-1]["term_sheets"][0]["terms"]["unit_price"] = 20
    assert apply(c, e, p) == p


def test_reciprocal_move_uses_live_comparable_quote_and_never_future_prices():
    c, e, p = scenario()
    q = {"unit_price": 46, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 0}
    e += [
        {"from": "buyer", "term_sheets": [{"terms": {**q, "unit_price": 38}}]},
        {"from": "seller", "status": "open", "term_sheets": [{"terms": q}]},
    ]
    focus = {"ppm_commitment": 1500, "chargeback_eur_per_complaint": 0}
    result = focused_structure(c, e, p, {}, None, focus, 0.5)
    assert result[0]["terms"]["unit_price"] == 42
    assert result[0]["observed_tradeoff"]
    assert result[0]["response_fraction"] == 0.5
    e[-1]["status"] = "final_offer"
    assert focused_structure(c, e, p, {}, None, focus, 0.5) == p
