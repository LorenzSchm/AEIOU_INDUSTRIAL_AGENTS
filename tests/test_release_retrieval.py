"""Release-aware retrieval must not rewrite historical evidence boundaries."""

import json

from aeiou.research import ResearchPackage
from aeiou.research_tools import decision_research
from aeiou.retrieval import evidence_cutoff


def test_final_retrieval_uses_release_news_and_respects_explicit_cutoff(tmp_path):
    (tmp_path / "facts.json").write_text(
        json.dumps(
            {
                "news:base": {"supplier": "sup:1", "date": "2026-09-10", "headline": "Payment finance review"},
            }
        )
    )
    release = tmp_path / "release-sunday"
    release.mkdir()
    (release / "facts.json").write_text(
        json.dumps(
            {
                "news:new": {"supplier": "sup:1", "date": "2026-10-20", "headline": "Payment credit line tightened"},
                "news:other": {"supplier": "sup:2", "date": "2026-10-23", "headline": "Payment credit line tightened"},
            }
        )
    )
    pkg = ResearchPackage(tmp_path)
    case = {"case_id": "case:test", "suppliers": ["sup:1"], "parts": [], "set": "final"}
    result = decision_research(case, pkg, "payment credit")
    sources = {card["source_id"] for card in result["evidence_cards"]}
    assert sources == {"news:base", "news:new"}
    assert result["as_of"] == "2026-10-23"
    assert not any(card["shareable"] for card in result["evidence_cards"])
    for historical in ({**case, "set": "practice"}, {**case, "set": "sandbox"}, {**case, "as_of": "2026-09-30"}):
        result = decision_research(historical, pkg, "payment credit")
        assert {card["source_id"] for card in result["evidence_cards"]} == {"news:base"}
        assert result["as_of"] == "2026-09-30"
    assert evidence_cutoff(case, pkg.for_case({**case, "set": "practice"})) == "2026-09-30"
