import copy

import pytest

from aeiou.learned_portfolio import ProgressSelector, menu_variants


def packages():
    return [
        {"id": "A", "terms": {"unit_price": 95, "payment_days": 30}},
        {"id": "B", "terms": {"unit_price": 97, "payment_days": 60}},
        {"id": "C", "terms": {"unit_price": 98, "payment_days": 90}},
    ]


def test_variants_preserve_anchor_and_explicit_retained_probe():
    ps = packages()
    ps[1]["retained_concession"] = True
    before = copy.deepcopy(ps)
    variants = menu_variants(ps, ["A", "B", "C"])
    assert all({"A", "B"}.issubset(v) for v in variants)
    assert ps == before


def test_model_can_change_menu_without_changing_any_terms():
    selector = ProgressSelector({})
    selector.probability = lambda case, sid, ev, ps, *args: 0.8 if len(ps) == 1 else 0.6
    ps = packages()
    before = copy.deepcopy(ps)
    chosen, audit = selector.choose({"round_limit": 10}, "s", [{"from": "seller", "status": "open"}], ps, ["A", "B"])
    assert chosen == ["A"] and audit["changed"]
    assert ps == before


@pytest.mark.parametrize("status", ["final_offer", "final", "agreed", "walked_away"])
def test_terminal_states_cannot_be_overridden_by_the_model(status):
    selector = ProgressSelector({})

    def fail(*args):
        raise AssertionError("No model evaluation at a terminal state")

    selector.probability = fail
    chosen, audit = selector.choose(
        {"round_limit": 10}, "s", [{"from": "seller", "status": status}], packages(), ["A", "B"]
    )
    assert chosen == ["A", "B"] and not audit["changed"]


def test_closing_turn_and_small_prediction_difference_preserve_menu():
    selector = ProgressSelector({})
    selector.probability = lambda case, sid, ev, ps, *args: 0.605 if len(ps) == 1 else 0.6
    chosen, _ = selector.choose(
        {"round_limit": 10}, "s", [{"from": "seller", "status": "open"}], packages(), ["A", "B"]
    )
    assert chosen == ["A", "B"]
    chosen, audit = selector.choose(
        {"round_limit": 2}, "s", [{"from": "seller", "status": "open"}], packages(), ["A", "B"]
    )
    assert chosen == ["A", "B"] and audit["reason"] == "terminal_or_closing_turn"
