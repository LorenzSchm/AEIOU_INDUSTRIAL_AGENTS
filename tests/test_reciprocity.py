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
