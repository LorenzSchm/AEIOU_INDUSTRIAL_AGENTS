"""Decision-specific research and a measurable retrieval evaluation, entirely local."""

import json
import time
from pathlib import Path
from statistics import mean

from .research import package, research
from .retrieval import evidence_cards, evidence_cutoff


def decision_research(case, pkg, query, supplier_id=None, limit=6):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("Provide a research question of 1–500 characters")
    if supplier_id is not None and supplier_id not in case["suppliers"]:
        raise ValueError("Supplier must belong to the current case")
    scope = [supplier_id] if supplier_id else case["suppliers"]
    intelligence = research(case, pkg)
    hits = pkg.evidence_index().search(query, scope, case["parts"], cutoff=evidence_cutoff(case, pkg), limit=limit)
    return {
        "query": query,
        "case_id": case["case_id"],
        "supplier_scope": scope,
        "as_of": evidence_cutoff(case, pkg),
        "private_planning_evidence": intelligence.get("release_context", []),
        "evidence_cards": evidence_cards(hits, intelligence),
        "cost_driver_indices": intelligence["indices"],
        "should_cost_estimates": intelligence["estimates"],
        "unresolved_questions": [
            "Does the market movement reach this supplier's actual costs in the relevant contract period?",
            "Which term would the supplier trade for price, and who can authorize that package?",
        ],
        "source": "Provided competition data only; no live web or supplier contact",
        "limitation": "Documents are evidence, not verified private costs. Should-cost remains an estimate.",
    }


def evaluate_retrieval(gold, output, pkg=None, limit=6):
    """Gold rows contain query, suppliers, optional parts/cutoff and expected source IDs."""
    pkg = pkg or package()
    rows = []
    for sample in gold:
        expected = set(sample["expected_ids"])
        if not expected:
            raise ValueError("Each retrieval example needs at least one expected source")
        start = time.monotonic()
        hits = pkg.evidence_index().search(
            sample["query"], sample["suppliers"], sample.get("parts", ()), sample.get("cutoff"), limit
        )
        retrieved = {h["id"] for h in hits}
        rows.append(
            {
                "query": sample["query"],
                "recall": len(expected & retrieved) / len(expected),
                "retrieved_ids": sorted(retrieved),
                "expected_ids": sorted(expected),
                "wrong_supplier_hits": sum(h["supplier_id"] not in sample["suppliers"] for h in hits),
                "latency_ms": round((time.monotonic() - start) * 1000, 3),
            }
        )
    report = {
        "queries": len(rows),
        "mean_recall": mean(r["recall"] for r in rows) if rows else None,
        "wrong_supplier_hits": sum(r["wrong_supplier_hits"] for r in rows),
        "rows": rows,
        "limitation": "Retrieval recall on the supplied gold set, not negotiation performance.",
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
