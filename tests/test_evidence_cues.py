import copy
import hashlib

from aeiou.evidence_cues import keyword_brief, matching_themes
from aeiou.research import package, research


def test_keyword_sources_cannot_cross_supplier_part_or_time_boundaries():
    case = package().cases["case:P10"]
    docs = [
        {"id": ref, "text": "We prioritise firm commitments and allocation."}
        for ref in ("doc:valid", "doc:future", "doc:other", "doc:undated", "doc:part", "doc:parts", "doc:invalid")
    ]
    base = {"supplier": "sup:70410", "date": "2026-09-18"}
    intelligence = {
        "documents": docs,
        "facts": {
            "doc:valid": base,
            "doc:future": {**base, "date": "2026-10-01"},
            "doc:other": {**base, "supplier": "sup:70740"},
            "doc:undated": {"supplier": "sup:70410"},
            "doc:part": {**base, "part": "part:unrelated"},
            "doc:parts": {**base, "part_ids": "part:unrelated,part:another"},
            "doc:invalid": {**base, "date": "2026-00-32"},
        },
    }
    original = copy.deepcopy(intelligence)
    brief = keyword_brief(case, intelligence, "sup:70410")
    evidence = [e for theme in brief["themes"] for e in theme["evidence"]]
    assert {e["source_ref"] for e in evidence} == {"doc:valid"}
    assert evidence[0]["source_sha256"] == hashlib.sha256(docs[0]["text"].encode()).hexdigest()
    assert evidence[0]["excerpts"] == [docs[0]["text"]]
    assert len(brief["excluded_sources"]) == 6
    assert intelligence == original
    assert "capacity_and_commitment" not in matching_themes("Please confirm receipt of our message.")


def test_case_keywords_retain_real_capacity_and_cost_estimate_provenance():
    pkg = package()
    case = pkg.cases["case:P10"]
    intelligence = research(case, pkg.for_case(case))
    brief = keyword_brief(case, intelligence, case["suppliers"][0])
    themes = {t["theme"]: t for t in brief["themes"]}
    assert "doc:20260918-70410-P10" in {e["source_ref"] for e in themes["capacity_and_commitment"]["evidence"]}
    estimate = next(e for e in themes["cost_transparency"]["evidence"] if e["source_ref"] == "cost:340010")
    assert estimate["kind"] == "buyer_estimate"
    assert estimate["source_date"] == "2026-07-15"
    assert brief["evidence_cutoff"] == "2026-09-30"
