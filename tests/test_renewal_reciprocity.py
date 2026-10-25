import copy
import json

import pytest

from aeiou.config import data_root
from aeiou.renewal_reciprocity import RenewalReciprocityNegotiator, bounded_reciprocal_bid


def example():
    case = json.loads((data_root() / "cases/practice/P01.json").read_text(encoding="utf-8"))
    terms = {"unit_price": 132.27, "payment_days": 14, "effective_date": "2027-01-01"}
    events = [
        {"from": "buyer", "term_sheets": [{"terms": terms}]},
        {"from": "seller", "status": "open", "round": 2,
         "term_sheets": [{"terms": {**terms, "unit_price": 135.50}}]},
    ]
    return case, events


def test_moves_on_comparable_quote_without_waiting_for_repetition():
    case, events = example()
    before = copy.deepcopy((case, events))
    bid = bounded_reciprocal_bid(case, events, 133.55)
    assert bid["terms"]["unit_price"] == 133.08
    assert bid["terms"]["payment_days"] == 14
    assert (case, events) == before


def test_cap_limits_concession_and_stops_without_repeating():
    case, events = example()
    events[0]["term_sheets"][0]["terms"]["unit_price"] = 133.50
    assert bounded_reciprocal_bid(case, events, 133.55)["terms"]["unit_price"] == 133.55
    events[0]["term_sheets"][0]["terms"]["unit_price"] = 133.55
    assert bounded_reciprocal_bid(case, events, 133.55) is None


def test_does_not_counter_final_or_better_quote_or_uncomparable_terms():
    case, events = example()
    events[-1]["status"] = "final_offer"
    assert bounded_reciprocal_bid(case, events, 133.55) is None
    events[-1]["status"] = "open"
    events[-1]["term_sheets"][0]["terms"]["unit_price"] = 132.00
    assert bounded_reciprocal_bid(case, events, 133.55) is None
    events[-1]["term_sheets"][0]["terms"].update(unit_price=135.5, payment_days=90)
    assert bounded_reciprocal_bid(case, events, 133.55) is None


def test_uses_highest_prior_comparable_bid_and_rejects_illegal_terms():
    case, events = example()
    previous = copy.deepcopy(events[0])
    previous["term_sheets"][0]["terms"]["unit_price"] = 133.20
    events.insert(0, previous)
    assert bounded_reciprocal_bid(case, events, 133.55)["previous_price"] == 133.20
    events[-1]["term_sheets"][0]["terms"]["payment_days"] = 999
    assert bounded_reciprocal_bid(case, events, 133.55) is None


def test_larger_final_step_stays_below_quote_and_historical_agreement():
    case, events = example()
    events[0]["term_sheets"][0]["terms"]["unit_price"] = 133.51
    events[-1]["term_sheets"][0]["terms"]["unit_price"] = 134.20
    terms = bounded_reciprocal_bid(case, events, 134.19, 0.5)["terms"]
    assert terms["unit_price"] == 133.85
    assert terms["unit_price"] < 134.20
    events[-1]["term_sheets"][0]["terms"]["unit_price"] = 140
    assert bounded_reciprocal_bid(case, events, 134.19, 0.5)["terms"]["unit_price"] == 134.19
    for fraction in (0, -1, 0.6, float("nan")):
        with pytest.raises(ValueError):
            bounded_reciprocal_bid(case, events, 134.19, fraction)


def test_open_quote_can_receive_another_counter_but_deadline_reserves_close():
    case, events = example()
    events[0]["term_sheets"][0]["terms"]["unit_price"] = 133.85
    events[-1]["term_sheets"][0]["terms"]["unit_price"] = 134.00
    bid = bounded_reciprocal_bid(case, events, 133.99, 0.5)
    assert 133.85 < bid["terms"]["unit_price"] < 134.00
    events[:0] = [copy.deepcopy(events[0]) for _ in range(7)]
    assert bounded_reciprocal_bid(case, events, 133.99, 0.5) is None


def test_rejects_unbounded_experimental_configuration():
    for settings in ({"max_bids": 9}, {"max_bids": True}, {"early_fraction": 0},
                     {"closing_fraction": float("nan")}, {"early_fraction": 0.8}):
        with pytest.raises(ValueError):
            RenewalReciprocityNegotiator(None, reference_run={}, **settings)
