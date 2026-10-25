"""Release-aware retrieval must not rewrite historical evidence boundaries."""

import json

from aeiou.research import ResearchPackage
from aeiou.research_collection import collect_tables
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


def test_release_full_news_and_global_memo_are_available_only_after_cutoff(tmp_path):
    (tmp_path / "facts.json").write_text("{}")
    release = tmp_path / "release-sunday"
    (release / "data").mkdir(parents=True)
    (release / "facts.json").write_text(
        json.dumps(
            {
                "news:new": {"supplier": "sup:1", "date": "2026-10-20", "headline": "Supplier update"},
                "doc:memo": {"supplier": None, "date": "2026-10-23", "type": "memo"},
            }
        )
    )
    (release / "data/news.csv").write_text(
        "id,date,supplier_id,headline,body\n"
        "news:new,2026-10-20,sup:1,Supplier update,Short-time work creates capacity.\n"
    )
    (release / "data/documents.csv").write_text(
        "id,date,supplier_id,body\ndoc:memo,2026-10-23,,Pump tenders postponed into spring.\n"
    )
    pkg = ResearchPackage(tmp_path)
    case = {"case_id": "case:test", "set": "final", "suppliers": ["sup:1"], "parts": []}
    news = decision_research(case, pkg, "short-time capacity")
    assert "Short-time work" in news["evidence_cards"][0]["text"]
    memo = decision_research(case, pkg, "pump tenders spring")
    assert {c["source_id"] for c in memo["evidence_cards"]} == {"doc:memo"}
    assert not memo["evidence_cards"][0]["shareable"]
    tables, coverage = collect_tables(case, pkg)
    assert tables["news"][0]["body"] == "Short-time work creates capacity."
    assert coverage["tables"]["documents"]["release_rows"] == 1
    for past in ({**case, "set": "practice"}, {**case, "as_of": "2026-09-30"}):
        assert not decision_research(past, pkg, "short-time capacity pump spring")["evidence_cards"]
        tables, _ = collect_tables(past, pkg.for_case(past))
        assert not tables.get("news") and not tables.get("documents")
