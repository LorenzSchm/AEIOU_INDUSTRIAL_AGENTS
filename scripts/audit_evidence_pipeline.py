"""Audit local case collection, payload coverage and lossless retrieval; no model or hosted calls."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from aeiou.evidence_transport import record_text, retrieve, source_records
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package


def audit_case(pkg, case, threads=None, strategy="Use only supplied buyer evidence."):
    threads = threads or {}
    agent = PromptLabAgent(pkg, None, PromptSpec("offline-data-audit", strategy, assessment_enabled=True))
    agent.prepare(case)
    encoded = agent.render_lab(case, threads)
    payload = json.loads(encoded)
    records = source_records(agent.intelligence, threads)
    sources = payload["decision_context"]["sources"]
    if set(records) - sources.keys():
        raise AssertionError("Collected records missing from the agent's source catalog")
    verified, pages = [], 0
    for ref, record in records.items():
        chunks, offset = [], 0
        while offset is not None:
            page = retrieve(records, {"source_ref": ref, "offset": offset})
            chunks.append(page["text"])
            pages += 1
            offset = page["next_offset"]
        reconstructed = "".join(chunks)
        if reconstructed != record_text(record):
            raise AssertionError(f"Retrieval changed or lost data for {ref}")
        if hashlib.sha256(reconstructed.encode()).hexdigest() != page["content_sha256"]:
            raise AssertionError(f"Retrieval hash mismatch for {ref}")
        verified.append(ref)
    if payload["evidence_transport"]["unretrievable_refs"]:
        raise AssertionError("A deferred source has no read path")
    return {"case_id": case["case_id"], "status": "passed", "collection": payload["research"]["collection"],
            "table_row_counts": payload["research"]["table_row_counts"],
            "transport": payload["evidence_transport"], "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "verified_source_refs": verified, "local_pages_checked": pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Repeat to select cases; defaults to all public package cases")
    parser.add_argument("--output", type=Path, default=Path("reports/evidence-pipeline/audit.json"))
    args = parser.parse_args()
    pkg = package()
    ids = args.case or sorted(pkg.cases)
    results = [audit_case(pkg, pkg.cases[cid]) for cid in ids]
    report = {"created_at": datetime.now(UTC).isoformat(), "mode": "offline_data_integrity",
              "model_calls": 0, "hosted_sessions_used": 0,
              "limitations": "Checks collected buyer data, generated model inputs and local retrieval. "
                              "Does not prove model attention, supplier acceptance or improved scores. "
                              "The audit reads all pages offline; a live decision retains its two-read time budget.",
              "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output.resolve()), "cases_passed": len(results),
                      "sources_verified": sum(len(r["verified_source_refs"]) for r in results),
                      "collection_issues": sum(len(r["collection"]["issues"]) for r in results)}))


if __name__ == "__main__":
    main()
