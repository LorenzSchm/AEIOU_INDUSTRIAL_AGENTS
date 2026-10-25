import copy
from types import SimpleNamespace

import pytest

from aeiou.grounding import communication_evidence, next_evidence_ids, requests_price_evidence
from aeiou.research import recent_index_reversals
from aeiou.retrieval import evidence_cards


def fixture():
    case = {"as_of": "2026-09-30", "buyer": {"category_goal": "leverage"}, "negotiable": ["index_clause"]}
    points = {"2025-09": 90, "2026-03": 160, "2026-06": 110, "2026-09": 100, "2026-10": 70}
    facts = {f"idx:NEW@{m}": {"month": m, "value": v, "series": "NEW"} for m, v in points.items()}
    return case, SimpleNamespace(facts=facts), [{"series": "NEW", "weight": 0.4}]


def test_recent_window_is_fixed_not_cherry_picked_and_respects_data_cutoff():
    case, pkg, annual = fixture()
    before = copy.deepcopy(pkg.facts)
    result = recent_index_reversals(case, pkg, annual)
    assert pkg.facts == before
    assert len(result) == 1
    row = result[0]
    assert row["refs"] == ["idx:NEW@2026-06", "idx:NEW@2026-09"]
    assert row["change_pct"] == pytest.approx(-9.0909)
    assert row["annual_change_pct"] == pytest.approx(11.1111)
    assert "twelve-month change remains positive" in row["claim"]
    assert "does not establish a fall in your total cost" in row["claim"]


def test_recent_window_rejects_stale_series():
    case, pkg, annual = fixture()
    case["as_of"] = "2028-01-31"
    assert recent_index_reversals(case, pkg, annual) == []


@pytest.mark.parametrize(
    "change",
    [
        "missing_quarter",
        "missing_year",
        "negative_year",
        "positive_quarter",
        "zero",
        "infinite",
        "other_goal",
        "no_index",
    ],
)
def test_trend_requires_complete_valid_comparable_points(change):
    case, pkg, annual = fixture()
    if change == "missing_quarter":
        del pkg.facts["idx:NEW@2026-06"]
    elif change == "missing_year":
        del pkg.facts["idx:NEW@2025-09"]
    elif change == "negative_year":
        pkg.facts["idx:NEW@2025-09"]["value"] = 120
    elif change == "positive_quarter":
        pkg.facts["idx:NEW@2026-06"]["value"] = 95
    elif change == "zero":
        pkg.facts["idx:NEW@2026-06"]["value"] = 0
    elif change == "infinite":
        pkg.facts["idx:NEW@2026-06"]["value"] = float("inf")
    elif change == "other_goal":
        case["buyer"]["category_goal"] = "stabilise"
    elif change == "no_index":
        case["negotiable"] = ["unit_price"]
    assert recent_index_reversals(case, pkg, annual) == []


@pytest.mark.parametrize(
    "text,requested",
    [
        ("Please provide the data underlying your target price.", True),
        ("We kindly ask you to provide supporting figures.", True),
        ("Could you share the evidence for this proposal?", True),
        ("We accept provided you implement the index clause.", False),
        ("Please do not send more data.", False),
        ("Our data supports the price.", False),
    ],
)
def test_price_evidence_request_is_distinct_from_conditional_offer(text, requested):
    assert requests_price_evidence([{"from": "supplier", "body": text}]) is requested


def test_qualified_card_is_sent_once_on_request_even_during_price_progress():
    case, pkg, annual = fixture()
    cards = evidence_cards([], {"recent_indices": recent_index_reversals(case, pkg, annual)})
    assert communication_evidence(cards, []) == []
    sid = "sup:NEW"
    events = [
        {"from": "supplier", "term_sheets": [{"terms": {"unit_price": 12}}]},
        {"from": "buyer", "body": "Our initial offer."},
        {
            "from": "supplier",
            "body": "Please provide the data underlying your target price.",
            "term_sheets": [{"terms": {"unit_price": 11}}],
        },
    ]
    assert communication_evidence(cards, events) == cards
    assert next_evidence_ids(cards, {sid: events}, sid) == ["index:NEW:recent_quarter"]
    events += [{"from": "buyer", "body": cards[0]["text"]}, events[-1]]
    assert next_evidence_ids(cards, {sid: events}, sid) == []
