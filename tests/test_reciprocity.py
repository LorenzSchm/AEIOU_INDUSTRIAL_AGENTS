import copy

import pytest
from arena_kit.cost import complete_offer, total_cost

from aeiou.reciprocity import responsive_packages, same_structure
from aeiou.simulation import local_cases


def scenario():
    case = copy.deepcopy(local_cases()["case:S01"])
    terms = complete_offer(case, {"unit_price": 17, "payment_days": 30})
    quote = {**terms, "unit_price": 19}
    buyer = {"from": "buyer", "term_sheets": [{"terms": terms}]}
    seller = {"from": "seller", "status": "open", "body": "Please review.", "term_sheets": [{"terms": quote}]}
    packages = [{"id": "A", "terms": terms, "cost": total_cost(case, terms)}]
    return case, buyer, seller, packages


def test_repeated_package_gets_real_bounded_reciprocity_without_duplicate_prices():
    case, buyer, seller, packages = scenario()
    events = [seller, buyer, seller, buyer, seller]
    before = copy.deepcopy((events, packages))
    result = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf"))
    assert before == (events, packages)
    assert result[0]["id"] == "A"
    assert result[0]["terms"]["unit_price"] == 17.5
    assert result[0]["cost"] < total_cost(case, seller["term_sheets"][0]["terms"])
    assert len(result) == 1  # no higher-price alternative on identical terms


def test_does_not_bid_up_a_productive_early_negotiation():
    case, buyer, seller, packages = scenario()
    events = [seller, buyer, seller]
    assert responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf")) == packages


def test_explicit_impasse_asks_for_one_term_instead_of_everything_at_once():
    case, buyer, seller, packages = scenario()
    seller["body"] = "This is our best possible pricing and firm position."
    result = responsive_packages(
        case, [seller, buyer, seller], packages, {}, lambda t: total_cost(case, t), float("inf")
    )
    probe = next(p for p in result if p.get("responsive_probe"))
    quote = seller["term_sheets"][0]["terms"]
    assert probe["terms"]["unit_price"] == quote["unit_price"]
    assert sum(probe["terms"][f] != quote[f] for f in quote) == 1
    assert probe["cost"] < total_cost(case, quote)
    assert len({p["id"] for p in result}) == len(result)


def test_explicit_price_freeze_explores_terms_after_first_reply():
    case, buyer, seller, packages = scenario()
    seller["body"] = "Management has frozen all price reductions. We can discuss terms instead."
    result = responsive_packages(
        case, [seller, buyer, seller], packages, {}, lambda t: total_cost(case, t), float("inf")
    )
    probe = next(p for p in result if p.get("responsive_probe"))
    quote = seller["term_sheets"][0]["terms"]
    assert probe["terms"]["unit_price"] == quote["unit_price"]
    assert probe["cost"] < total_cost(case, quote)


@pytest.mark.parametrize("status", ["agreed", "final_offer", "walked_away"])
def test_reciprocity_never_reopens_terminal_quote(status):
    case, buyer, seller, packages = scenario()
    seller.update(status=status, body="We require a reciprocal step.")
    assert (
        responsive_packages(case, [seller, buyer, seller], packages, {}, lambda t: total_cost(case, t), float("inf"))
        == packages
    )


def test_external_ceiling_still_blocks_reciprocal_price_increase():
    case, buyer, seller, packages = scenario()
    result = responsive_packages(
        case, [seller, buyer, seller, buyer, seller], packages, {}, lambda t: total_cost(case, t), packages[0]["cost"]
    )
    assert result == packages
    assert same_structure(result[0]["terms"], buyer["term_sheets"][0]["terms"])


def test_illegal_supplier_term_is_not_silently_snapped_into_a_binding_quote():
    case, buyer, seller, packages = scenario()
    seller["body"] = "This is our best possible pricing."
    seller["term_sheets"][0]["terms"]["payment_days"] = 999
    result = responsive_packages(
        case, [seller, buyer, seller], packages, {}, lambda t: total_cost(case, t), float("inf")
    )
    assert result == packages


def test_fixed_volume_request_gets_an_answer_without_disclosing_private_numbers():
    from aeiou.grounding import fixed_commitment_answer

    case, _, _, _ = scenario()
    case["fixed_terms"]["volume_per_year"] = 12345
    case["negotiable"] = [f for f in case["negotiable"] if f != "volume_per_year"]
    events = [{"from": "supplier", "body": "Which volume can you commit to? We need a real jump in volume."}]
    answer = fixed_commitment_answer(case, events)
    assert "annual volume is fixed" in answer and "12345" not in answer
    case["negotiable"].append("volume_per_year")
    assert fixed_commitment_answer(case, events) == ""


def indexed_scenario():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(
        case_id="case:UNSEEN-INDEX", negotiable=["unit_price", "index_clause", "payment_days", "contract_months"]
    )
    for field in case["negotiable"]:
        case["fixed_terms"].pop(field, None)
    clause = {"series": "UNSEEN", "material_share": 0.32, "deadband_pct": 4, "base_month": "2026-02"}
    case["term_options"] = {"index_clause": [None, clause], "payment_days": [21, 75], "contract_months": [24, 30]}
    case["buyer"].update(category_goal="leverage", index_paths={"UNSEEN": 0.08})
    fixed = {"unit_price": 20, "index_clause": None, "payment_days": 75, "contract_months": 24}
    indexed = {**fixed, "unit_price": 19.5, "index_clause": clause}
    packages = [
        {"id": "A", "terms": fixed},
        {"id": "B", "terms": {**fixed, "unit_price": 19.8, "payment_days": 21}},
        {"id": "C", "terms": indexed},
    ]
    for p in packages:
        p["cost"] = total_cost(case, p["terms"])
    events = [
        {"from": "buyer", "term_sheets": [{"terms": p["terms"]} for p in packages]},
        {
            "from": "seller",
            "status": "open",
            "body": "Here is our counter on your indexed terms.",
            "term_sheets": [{"terms": {**indexed, "unit_price": 23.5}}],
        },
    ]
    return case, events, packages


def test_index_reciprocity_moves_quarter_gap_after_first_comparable_counter():
    case, events, packages = indexed_scenario()
    before = copy.deepcopy((events, packages))
    result = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf"))
    assert (events, packages) == before
    assert result[:2] == packages[:2]
    assert result[2]["index_reciprocity"]
    assert result[2]["terms"]["unit_price"] == 20.5
    assert same_structure(result[2]["terms"], packages[2]["terms"])
    assert result[2]["cost"] < result[2]["standing_cost"]


@pytest.mark.parametrize(
    "change", ["unoffered_shape", "nonindexed", "stabilise", "terminal", "ceiling", "already_higher"]
)
def test_index_reciprocity_requires_observed_structure_and_safe_movement(change):
    case, events, packages = indexed_scenario()
    ceiling = float("inf")
    if change == "unoffered_shape":
        events[-1]["term_sheets"][0]["terms"]["contract_months"] = 30
    elif change == "nonindexed":
        events[-1]["term_sheets"][0]["terms"]["index_clause"] = None
    elif change == "stabilise":
        case["buyer"]["category_goal"] = "stabilise"
    elif change == "terminal":
        events[-1]["status"] = "final_offer"
    elif change == "ceiling":
        ceiling = packages[2]["cost"]
    elif change == "already_higher":
        packages[2] = {**packages[2], "terms": {**packages[2]["terms"], "unit_price": 21}}
        packages[2]["cost"] = total_cost(case, packages[2]["terms"])
    assert responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), ceiling) == packages


def momentum_scenario():
    case, events, packages = indexed_scenario()
    first = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf"))
    buyer = {"from": "buyer", "term_sheets": [{"terms": copy.deepcopy(p["terms"])} for p in first]}
    seller = copy.deepcopy(events[-1])
    seller["term_sheets"][0]["terms"]["unit_price"] = 23
    events += [buyer, seller]
    packages = copy.deepcopy(packages)
    packages[2]["terms"]["unit_price"] = 20.6
    packages[2]["cost"] = total_cost(case, packages[2]["terms"])
    return case, events, packages


def test_continue_reciprocated_index_step_without_changing_terms_or_anchor():
    case, events, packages = momentum_scenario()
    result = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf"))
    assert result[:2] == packages[:2]
    assert result[2]["terms"]["unit_price"] == 21.5
    assert result[2]["reciprocal_momentum"]["previous_buyer_step"] == 1
    assert same_structure(result[2]["terms"], packages[2]["terms"])
    assert result[2]["cost"] < result[2]["standing_cost"]


def test_reciprocal_momentum_never_crosses_midpoint_of_remaining_gap():
    case, events, packages = momentum_scenario()
    events[-1]["term_sheets"][0]["terms"]["unit_price"] = 21
    result = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf"))
    assert result[2]["terms"]["unit_price"] == 20.75


@pytest.mark.parametrize("change", ["stall", "increase", "changed_terms", "buyer_backwards"])
def test_unreciprocated_or_incomparable_moves_do_not_enable_momentum(change):
    case, events, packages = momentum_scenario()
    if change == "stall":
        events[-1]["term_sheets"][0]["terms"]["unit_price"] = 23.5
    elif change == "increase":
        events[-1]["term_sheets"][0]["terms"]["unit_price"] = 24
    elif change == "changed_terms":
        events[1]["term_sheets"][0]["terms"]["contract_months"] = 30
    else:
        events[-2]["term_sheets"][-1]["terms"]["unit_price"] = 19.4
    result = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), float("inf"))
    assert not any(p.get("reciprocal_momentum") for p in result)


def test_external_ceiling_blocks_reciprocal_momentum():
    case, events, packages = momentum_scenario()
    result = responsive_packages(case, events, packages, {}, lambda t: total_cost(case, t), packages[2]["cost"])
    assert result == packages
