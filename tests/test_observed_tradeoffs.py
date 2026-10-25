import copy
import json
from pathlib import Path

import pytest
from arena_kit.cost import total_cost

from aeiou.learned_portfolio import menu_variants
from aeiou.observed_tradeoffs import liability_response


def setup():
    case = json.loads((Path(__file__).parent / "fixtures/practice_public_openings.json").read_text())["P11"]["case"]
    a = {"unit_price": 39.0, "ppm_commitment": 1500, "chargeback_eur_per_complaint": 500}
    c = {**a, "unit_price": 38.5, "chargeback_eur_per_complaint": 0}

    def message(source, *terms):
        return {"from": source, "status": "open", "term_sheets": [{"terms": t} for t in terms]}

    events = [
        message("buyer", a, c),
        message("supplier", {**c, "unit_price": 46}),
        message("buyer", a),
        message("supplier", {**a, "unit_price": 49.5}, {**a, "unit_price": 47, "chargeback_eur_per_complaint": 150}),
    ]
    packages = [{"id": i, "terms": t, "cost": total_cost(case, t)} for i, t in (("A", a), ("C", c))]
    return case, events, packages


def apply(case, events, packages, ceiling=float("inf")):
    return liability_response(case, events, packages, {}, lambda t: total_cost(case, t), ceiling)


def test_observed_fee_premium_keeps_lower_cost_option_and_bounds_counter():
    case, events, packages = setup()
    before = copy.deepcopy((case, events, packages))
    result = apply(case, events, packages)
    assert (case, events, packages) == before
    assert result[0] == packages[0]
    probe = result[1]
    assert probe["observed_tradeoff"]
    assert probe["terms"]["unit_price"] == 38.5  # stale quote only protects the option
    assert probe["terms"]["chargeback_eur_per_complaint"] == 0
    assert probe["cost"] < probe["standing_cost"]
    assert all("C" in ids for ids in menu_variants(result, ["A", "C"]))


def test_only_a_live_comparable_counter_allows_bounded_settlement():
    case, events, packages = setup()
    events += [
        {"from": "supplier", "status": "open", "term_sheets": [{"terms": {**packages[1]["terms"], "unit_price": 45.5}}]}
    ]
    result = apply(case, events, packages)
    assert result[1]["terms"]["unit_price"] == 45.05
    assert result[1]["cost"] < result[1]["standing_cost"]


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_terminal_supplier_never_reopened(status):
    case, events, packages = setup()
    events[-1]["status"] = status
    assert apply(case, events, packages) == packages


def test_price_comparison_requires_same_message_and_same_other_terms():
    case, events, packages = setup()
    events[-1]["term_sheets"][1]["terms"]["ppm_commitment"] = 800
    assert apply(case, events, packages) == packages
    case, events, packages = setup()
    alternative = events[-1]["term_sheets"].pop()
    events.append({"from": "supplier", "status": "open", "term_sheets": [alternative]})
    assert apply(case, events, packages) == packages


def test_ceiling_and_past_concessions_block_unsafe_probe():
    case, events, packages = setup()
    assert apply(case, events, packages, packages[0]["cost"]) == packages
    events[0]["term_sheets"][1]["terms"]["unit_price"] = 46.5
    assert apply(case, events, packages) == packages


def test_cheaper_price_alone_is_insufficient_when_total_cost_is_worse():
    case, events, packages = setup()
    result = liability_response(case, events, packages, {}, lambda t: -t["chargeback_eur_per_complaint"], float("inf"))
    assert result == packages


def test_unseen_supplier_and_shifted_prices_do_not_depend_on_track_identity():
    case, events, packages = setup()
    case["case_id"] = "case:unseen"
    case["suppliers"] = ["sup:unseen"]
    events.append(
        {"from": "supplier", "status": "open", "term_sheets": [{"terms": {**packages[1]["terms"], "unit_price": 46}}]}
    )
    events = copy.deepcopy(events)
    packages = copy.deepcopy(packages)
    seen = set()
    for message in events:
        for sheet in message["term_sheets"]:
            if id(sheet["terms"]) in seen:
                continue
            seen.add(id(sheet["terms"]))
            sheet["terms"]["unit_price"] += 10
    for p in packages:
        p["terms"]["unit_price"] += 10
    result = apply(case, events, packages)
    assert result[1]["terms"]["unit_price"] == 55.44
