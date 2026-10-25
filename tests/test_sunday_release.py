import copy
import json
from pathlib import Path

import pytest

from aeiou.config import data_root
from aeiou.grounding import compose_email
from aeiou.research import ResearchPackage, research
from aeiou.research_tools import decision_research
from aeiou.retrieval import DATA_CUTOFF, evidence_cards, evidence_cutoff
from aeiou.strategic import StrategicNegotiator


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    old = ResearchPackage(data_root(), release=tmp_path_factory.mktemp("no-release"))
    current = ResearchPackage(data_root(), release=data_root().parent / "release-sunday")
    yield old, current
    for pkg in (old, current):
        if pkg.db:
            pkg.db.close()


def case(sid="sup:70270", **overrides):
    return {
        "case_id": "case:test-release",
        "set": "hidden",
        "suppliers": [sid],
        "parts": [],
        "refs": [],
        "buyer": {},
        "negotiable": [],
        **overrides,
    }


def test_installed_release_adds_ten_ids_without_modifying_base(packages):
    old, new = packages
    assert len(new.facts) == len(old.facts) + 10
    assert all(new.facts[k] == v for k, v in old.facts.items())
    assert new.release_as_of == "2026-10-23"


def test_explicit_as_of_and_historical_sets_win_over_release(packages):
    _, pkg = packages
    assert evidence_cutoff(case(), pkg) == "2026-10-23"
    assert evidence_cutoff(case(set="final"), pkg) == "2026-10-23"
    for scope in ("practice", "sandbox", "local", None):
        assert evidence_cutoff(case(set=scope), pkg) == DATA_CUTOFF
    assert evidence_cutoff(case(as_of="2026-10-15"), pkg) == "2026-10-15"


def test_news_body_is_searchable_scoped_and_citable(packages):
    _, pkg = packages
    result = decision_research(case(), pkg, "automotive order short-time work")
    cards = [c for c in result["evidence_cards"] if c.get("source_id") == "news:20261020-S16"]
    assert len(cards) == 1 and "automotive order" in cards[0]["text"]
    assert cards[0]["shareable"]
    assert all(c.get("supplier_id") in {None, "sup:70270"} for c in result["evidence_cards"])
    assert "automotive order" in compose_email({"evidence_ids": [cards[0]["id"]]}, cards, "sup:70270")


def test_october_facts_are_filtered_in_search_research_and_explicit_refs(packages):
    _, pkg = packages
    old_case = case(as_of=DATA_CUTOFF, refs=["news:20261020-S16"], trigger_doc="doc:20261023-HPW-MEMO")
    info = research(old_case, pkg)
    assert not pkg.release_ids.intersection(info["facts"])
    assert not info.get("release_context")
    hits = pkg.evidence_index().search("automotive payment", ["sup:70270"], cutoff=DATA_CUTOFF)
    assert not {h["id"] for h in hits}.intersection(pkg.release_ids)


def test_release_news_rows_and_global_memo_reach_private_preparation(packages):
    _, pkg = packages
    info = research(case(), pkg)
    assert any("automotive order" in r.get("body", "") for r in info["tables"]["news"])
    memo = next(r for r in info["release_context"] if r["source_id"] == "doc:20261023-HPW-MEMO")
    assert memo["private_planning_only"] and "Purchasing memo" in memo["text"]
    assert any("slow winter" in r["text"] for r in info["release_context"])
    assert all(r["supplier_id"] in {None, "sup:70270"} for r in info["release_context"])


def test_memo_and_noise_cannot_become_supplier_evidence(packages):
    _, pkg = packages
    index = pkg.evidence_index()
    assert not any(c["id"] == "news:20261022-S18" for c in index.chunks)
    memo = next(c for c in index.chunks if c["id"] == "doc:20261023-HPW-MEMO")
    cards = evidence_cards([{**memo, "method": "test"}], {})
    assert not cards[0]["shareable"]
    with pytest.raises(ValueError):
        compose_email({"evidence_ids": [cards[0]["id"]]}, cards, "sup:70270")


def test_fixed_price_letter_preserves_cover_period_qualification(packages):
    _, pkg = packages
    result = decision_research(case("sup:70340"), pkg, "steel secured forward fixed prices")
    card = next(c for c in result["evidence_cards"] if c.get("source_id") == "doc:20261013-70340-S15")
    assert card["shareable"] and "2027" in card["text"] and "2028" in card["text"]


def test_future_corpus_does_not_change_historical_search_scores(packages):
    old, new = packages
    query = "costs payment cash volume fixed material capacity approval"
    assert old.evidence_index().search(query, ["sup:70110"], cutoff=DATA_CUTOFF) == new.evidence_index().search(
        query, ["sup:70110"], cutoff=DATA_CUTOFF
    )


def test_planner_sees_memo_but_outgoing_message_does_not(packages):
    _, pkg = packages
    data = json.loads((Path(__file__).parent / "fixtures/practice_public_openings.json").read_text())["P01"]
    hidden = copy.deepcopy(data["case"])
    hidden.update(case_id="case:release-planning-test", set="hidden")
    seen = []

    class Planner:
        def plan_negotiation(self, user):
            payload = json.loads(user)
            seen.append(payload)
            assert any(r["source_id"] == "doc:20261023-HPW-MEMO" for r in payload["private_planning_evidence"])
            assert not any(c.get("source_id") == "doc:20261023-HPW-MEMO" for c in payload["research"]["evidence_cards"])
            return {**payload["planned_action"], "evidence_ids": [], "proposals": []}

    agent = StrategicNegotiator(pkg, Planner())
    move = agent.decide(hidden, copy.deepcopy(data["threads"]))
    assert seen and agent.fallbacks == 0
    assert "slow winter" not in move["message"]["body"]
    assert "HPW-MEMO" not in move["message"]["body"]


@pytest.mark.parametrize("label", ["P01", "P02", "P11", "P18"])
def test_historical_openings_unchanged_by_release_activation(packages, label):
    old, new = packages
    data = json.loads((Path(__file__).parent / "fixtures/practice_public_openings.json").read_text())[label]
    a = StrategicNegotiator(old).decide(data["case"], copy.deepcopy(data["threads"]))
    b = StrategicNegotiator(new).decide(data["case"], copy.deepcopy(data["threads"]))
    assert a == b
