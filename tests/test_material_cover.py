import copy

import pytest

from aeiou.grounding import material_cover_evidence
from aeiou.simulation import local_cases


def cover_fixture():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"]["category_goal"] = "leverage"
    case["negotiable"] = ["unit_price", "index_clause"]
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
