import copy

import pytest

from aeiou.response_value import SCHEMA
from aeiou.value_portfolio import ValueSelector


def selector(gains=(0.01, 0.02), progress=(0.8, 0.79)):
    result = ValueSelector({"schema": list(SCHEMA)}, experimental=True)
    result.estimates = lambda *args: [
        {"ids": ["A", "B"], "quoted_cost_gain": gains[0], "direct_cost_improved": progress[0]},
        {"ids": ["A"], "quoted_cost_gain": gains[1], "direct_cost_improved": progress[1]},
    ]
    return result


def choose(s, status="open", rounds=10):
    packages = [{"id": "A", "terms": {"unit_price": 95}}, {"id": "B", "terms": {"unit_price": 97}}]
    before = copy.deepcopy(packages)
    result = s.choose({"round_limit": rounds}, "s", [{"from": "seller", "status": status}], packages, ["A", "B"])
    assert packages == before
    return result


def test_requires_explicit_experiment_opt_in():
    with pytest.raises(ValueError, match="opt-in"):
        ValueSelector({"schema": list(SCHEMA)})


def test_value_changes_menu_without_changing_terms():
    ids, audit = choose(selector())
    assert ids == ["A"] and audit["changed"] and not audit["predictive_gates_passed"]


def test_tiny_gain_or_large_progress_drop_keeps_incumbent():
    assert choose(selector(gains=(0.01, 0.014)))[0] == ["A", "B"]
    assert choose(selector(progress=(0.8, 0.7)))[0] == ["A", "B"]


@pytest.mark.parametrize("status", ["final_offer", "final", "agreed", "walked_away"])
def test_terminal_state_bypasses_value_model(status):
    s = selector()
    s.estimates = lambda *args: pytest.fail("No model call during closing")
    assert choose(s, status)[0] == ["A", "B"]


def test_last_two_moves_keep_incumbent():
    assert choose(selector(), rounds=2)[0] == ["A", "B"]
