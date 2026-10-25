import copy

import pytest

from aeiou.price_reconciliation import focused_index_terms, own_quote_cards
from aeiou.simulation import local_cases


def fixture():
    case = copy.deepcopy(local_cases()["case:S01"])
    case.update(parts=["part:NEW"], as_of="2026-09-30", negotiable=["unit_price", "index_clause", "payment_days"])
    case["buyer"]["category_goal"] = "leverage"
    for field in case["negotiable"]:
        case["fixed_terms"].pop(field, None)
    clause = {"series": "NEW", "material_share": 0.3, "deadband_pct": 4, "base_month": "2026-02"}
    terms = {"unit_price": 25, "payment_days": 75, "index_clause": clause}
    events = [{"from": "seller", "status": "open", "body": "Our price increased.", "term_sheets": [{"terms": terms}]}]
    q = {
        "id": "quote:NEW",
        "supplier_id": "sup:NEW",
        "part_id": "part:NEW",
        "quote_date": "2026-09-10",
        "unit_price": 22,
        "price_unit": 1,
        "currency": "EUR",
        "payment_days": 30,
        "lead_time_days": 12,
        "incoterm": "DAP",
    }
    intelligence = {"facts": {"quote:NEW": {}}, "tables": {"quotes": [q]}}
    return case, events, intelligence


def test_own_quote_is_qualified_dated_reference_and_source_scoped():
    case, events, intelligence = fixture()
    card = own_quote_cards(case, events, "sup:NEW", intelligence)[0]
    assert card["source_id"] == "quote:NEW" and card["shareable"]
    assert "EUR 22.00" in card["text"] and "2026-09-10" in card["text"]
    assert "conditions may differ" in card["text"]


@pytest.mark.parametrize(
    "change",
    [
        "other_supplier",
        "other_part",
        "future",
        "old",
        "units",
        "currency",
        "higher",
        "unknown",
        "later_turn",
        "stabilise",
    ],
)
def test_quote_evidence_rejects_unmatched_or_inapplicable_records(change):
    case, events, intelligence = fixture()
    q = intelligence["tables"]["quotes"][0]
    if change == "other_supplier":
        q["supplier_id"] = "sup:OTHER"
    elif change == "other_part":
        q["part_id"] = "part:OTHER"
    elif change == "future":
        q["quote_date"] = "2026-10-01"
    elif change == "old":
        q["quote_date"] = "2025-09-01"
    elif change == "units":
        q["price_unit"] = 100
    elif change == "currency":
        q["currency"] = "USD"
    elif change == "higher":
        q["unit_price"] = 26
    elif change == "unknown":
        intelligence["facts"] = {}
    elif change == "later_turn":
        events.append({"from": "buyer"})
    else:
        case["buyer"]["category_goal"] = "stabilise"
    assert own_quote_cards(case, events, "sup:NEW", intelligence) == []


def focus_fixture():
    case, events, _ = fixture()
    quote = copy.deepcopy(events[-1])
    quote["body"] = "We must include an index clause."
    events += [{"from": "buyer", "term_sheets": []}, quote]
    sheets = [
        {"terms": {**quote["term_sheets"][0]["terms"], "unit_price": 20, "index_clause": None}},
        {"terms": {**quote["term_sheets"][0]["terms"], "unit_price": 21}},
    ]
    return case, events, sheets


def test_focus_only_selects_existing_validated_indexed_terms():
    case, events, sheets = focus_fixture()
    before = copy.deepcopy(sheets)
    assert focused_index_terms(case, events, sheets, lambda t: t["unit_price"]) == sheets[1]["terms"]
    assert sheets == before


@pytest.mark.parametrize("change", ["optional", "negated", "terminal", "fixed_quote", "different_terms", "stabilise"])
def test_focus_requires_explicit_constraint_and_matching_live_structure(change):
    case, events, sheets = focus_fixture()
    if change == "optional":
        events[-1]["body"] = "Indexation is an option."
    elif change == "negated":
        events[-1]["body"] = "An index clause is not required."
    elif change == "terminal":
        events[-1]["status"] = "final_offer"
    elif change == "fixed_quote":
        events[-1]["term_sheets"][0]["terms"]["index_clause"] = None
    elif change == "different_terms":
        sheets[1]["terms"]["payment_days"] = 21
    else:
        case["buyer"]["category_goal"] = "stabilise"
    assert focused_index_terms(case, events, sheets, lambda t: t["unit_price"]) is None
