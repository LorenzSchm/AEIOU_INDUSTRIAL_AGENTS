import copy

import pytest
from arena_kit.cost import total_cost

from aeiou.grounding import material_cover_evidence
from aeiou.index_exchange import index_commitment_exchange
from aeiou.optimizer import valid_terms
from aeiou.scenario_planner import equivalent_package
from aeiou.simulation import local_cases


def fixture():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(
        case_id="case:UNSEEN",
        suppliers=["sup:NEW"],
        negotiable=["unit_price", "index_clause", "payment_days", "contract_months"],
    )
    for field in case["negotiable"]:
        case["fixed_terms"].pop(field, None)
    clause = {"series": "NEW-MATERIAL", "material_share": 0.28, "deadband_pct": 4, "base_month": "2026-03"}
    case["term_options"] = {"index_clause": [None, clause], "payment_days": [21, 75], "contract_months": [24, 30, 42]}
    case["buyer"].update(category_goal="leverage", horizon_years=2, index_paths={"NEW-MATERIAL": 0.07})
    base = {"unit_price": 25, "index_clause": None, "payment_days": 75, "contract_months": 24}
    packages = [
        {"id": "A", "terms": base},
        {"id": "B", "terms": {**base, "unit_price": 24.7, "payment_days": 21}},
        {"id": "C", "terms": {**base, "unit_price": 24.6, "index_clause": clause}},
    ]

    def cost(t):
        return total_cost(case, t)

    for p in packages:
        p["cost"] = cost(p["terms"])
    quotes = [
        {**base, "unit_price": 30, "index_clause": clause},
        {**base, "unit_price": 29.6, "index_clause": clause, "payment_days": 21},
    ]
    events = [
        {"from": "buyer", "term_sheets": [{"terms": p["terms"]} for p in packages]},
        {
            "from": "sales@NEW",
            "status": "open",
            "body": "We must include a price adjustment clause.",
            "term_sheets": [{"terms": q} for q in quotes],
        },
    ]
    return case, events, packages, cost


@pytest.mark.parametrize(
    "reply",
    [
        "We must include a price adjustment clause.",
        "An index clause is essential for that duration.",
        "A fixed price over this period is not viable.",
    ],
)
def test_constraint_opens_bounded_cash_and_duration_exchange(reply):
    case, events, packages, cost = fixture()
    events[-1]["body"] = reply
    original = copy.deepcopy(packages)
    result = index_commitment_exchange(case, events, packages, cost, equivalent_package)
    assert packages == original
    assert result[0] == packages[0] and result[2] == packages[2]
    b = result[1]
    assert b["terms"]["index_clause"] is not None
    assert b["terms"]["payment_days"] == 21 and b["terms"]["contract_months"] == 30
    assert valid_terms(case, b["terms"])
    assert b["cost"] <= b["portfolio_ceiling"] < b["standing_cost"]
    assert b["standing_cost"] - b["cost"] >= 0.75 * (b["standing_cost"] - cost(packages[0]["terms"])) - 0.001


@pytest.mark.parametrize(
    "reply",
    [
        "We can offer an index clause if you prefer.",
        "An index clause is not required.",
        "We do not require an index clause.",
    ],
)
def test_optional_or_negated_constraint_does_not_replace_portfolio(reply):
    case, events, packages, cost = fixture()
    events[-1]["body"] = reply
    assert index_commitment_exchange(case, events, packages, cost, equivalent_package) == packages


@pytest.mark.parametrize("status", ["final_offer", "closed", "agreed"])
def test_terminal_offer_is_not_reopened(status):
    case, events, packages, cost = fixture()
    events[-1]["status"] = status
    assert index_commitment_exchange(case, events, packages, cost, equivalent_package) == packages


@pytest.mark.parametrize("goal", ["stabilise", "secure_supply", "routine"])
def test_other_category_policies_remain_unchanged(goal):
    case, events, packages, cost = fixture()
    case["buyer"]["category_goal"] = goal
    assert index_commitment_exchange(case, events, packages, cost, equivalent_package) == packages


def test_no_invention_of_unquoted_cash_or_invalid_longer_duration():
    case, events, packages, cost = fixture()
    events[-1]["term_sheets"] = events[-1]["term_sheets"][:1]
    result = index_commitment_exchange(case, events, packages, cost, equivalent_package)
    assert result[1]["terms"]["payment_days"] == 75
    case["term_options"]["contract_months"] = [24]
    assert index_commitment_exchange(case, events, packages, cost, equivalent_package) == packages


def test_previous_same_structure_concession_never_withdrawn():
    case, events, packages, cost = fixture()
    first = index_commitment_exchange(case, events, packages, cost, equivalent_package)
    # Both structures were previously offered above the new safe envelope.
    for payment in (21, 75):
        events[0]["term_sheets"].append({"terms": {**first[1]["terms"], "payment_days": payment, "unit_price": 100}})
    assert index_commitment_exchange(case, events, packages, cost, equivalent_package) == packages


def cover_fixture():
    case, _, _, _ = fixture()
    case["fixed_terms"]["effective_date"] = "2027-01-01"
    cards = [
        {
            "id": "cover",
            "supplier_id": "sup:NEW",
            "shareable": True,
            "kind": "verbatim_source",
            "text": (
                "Our copper requirement is covered at fixed prices until April 2027. "
                "Later movements reach us after that."
            ),
        }
    ]
    events = [{"from": "sales@NEW", "body": "Material indices rose in the past year."}]
    return cards, case, events


def test_cover_evidence_keeps_exact_qualifications_and_requires_overlap():
    cards, case, events = cover_fixture()
    original = copy.deepcopy(cards)
    assert material_cover_evidence(cards, case, events, "sup:NEW") == "cover"
    assert cards == original
    case["fixed_terms"]["effective_date"] = "2027-05-01"
    assert material_cover_evidence(cards, case, events, "sup:NEW") is None


@pytest.mark.parametrize("change", ["private", "wrong_supplier", "used", "no_inflation", "undated"])
def test_cover_evidence_is_narrow_and_does_not_repeat(change):
    cards, case, events = cover_fixture()
    if change == "private":
        cards[0]["shareable"] = False
    if change == "wrong_supplier":
        cards[0]["supplier_id"] = "sup:OTHER"
    if change == "used":
        events.append({"from": "buyer", "body": "Already sent a counter."})
    if change == "no_inflation":
        events[0]["body"] = "Please review our offer."
    if change == "undated":
        cards[0]["text"] = "Our copper requirement is covered at fixed prices."
    assert material_cover_evidence(cards, case, events, "sup:NEW") is None
