"""Rebuild the P10 experiment ledger from saved hosted results, without API calls."""

import json
from pathlib import Path

from aeiou.hosted_replay import verify_replay

ROOT = Path(__file__).resolve().parent


def summarize():
    rows = []
    folders = sorted((p for p in ROOT.glob("v*") if p.is_dir() and p.name[1:].isdigit()),
                     key=lambda p: int(p.name[1:]))
    for folder in folders:
        if not (folder / "run.json").exists():
            continue
        run = json.loads((folder / "run.json").read_text())
        evaluation = json.loads((folder / "evaluation.json").read_text())
        result = run.get("result") or run.get("remote_cleanup", {}).get("result") or {}
        messages = [message for events in run["threads"].values() for message in events
                    if message.get("from") != "buyer"]
        offers = [message for message in messages if message.get("term_sheets")]
        claims = [claim for message in messages if not message.get("result")
                  for claim in message.get("claim_results", [])]
        rows.append({
            "variant": folder.name, "evaluation_id": evaluation["id"], "run_id": run["id"],
            "session_id": run.get("session_id"), "run_status": run["status"],
            "official_score": result["score"] * 100 if "score" in result else None,
            "accepted": result.get("deal", False), "terms": result.get("terms"),
            "total_cost": result.get("c_deal"), "must_haves_ok": result.get("must_haves_ok"),
            "error": run.get("error"), "last_supplier_offer": offers[-1]["term_sheets"] if offers else [],
            "claim_results": claims,
            "transport": evaluation.get("experiment", {}).get("transport", "email"),
            "target_met": bool(result.get("deal") and result.get("must_haves_ok")
                               and result.get("score", 0) >= .72),
        })
        if run["status"] == "completed":
            (folder / "recorded-replay.json").write_text(json.dumps(verify_replay(run), indent=2))
    best = max((r for r in rows if r["accepted"] and r["must_haves_ok"]),
               key=lambda row: (row["official_score"], -max(0, row["terms"]["contract_months"] - 24),
                                sum(c.get("result") == "verified"
                                                          for c in row["claim_results"])), default=None)
    report = {
        "case_id": "case:P10", "target_score": 72,
        "baseline": {"run_id": "c842436fe9b34fc88555d4db8df05ae0",
                     "evaluation_id": "123b7ac5b0c6438baae9f2f33e50cdc1",
                     "score": 64.7293982, "total_cost": 1541808},
        "trials": rows, "best_accepted": best, "target_met": any(r["target_met"] for r in rows),
        "best_selection": "Highest official accepted score; ties prefer fewer committed months beyond "
                          "the two-year horizon, then more verified outgoing claims.",
        "best_score_observed_in": [r["variant"] for r in rows if best and r["accepted"]
                                   and r["must_haves_ok"]
                                   and abs(r["official_score"] - best["official_score"]) < 1e-8],
        "limitations": [
            "Single hosted attempts: exploratory comparisons, not controlled estimates of prompt effects.",
            "The score covers two years. A 36-month commitment includes a real third year outside this cost.",
            "A local score-floor stop is not an accepted deal; its cleanup score is zero. "
            "A hard target guard is not a recommended fallback in a secure-supply case with no alternative.",
        ],
    }
    (ROOT / "results.json").write_text(json.dumps(report, indent=2))
    lines = ["P10 / Ravensteiner: target 72 experiment ledger", "",
             f"Target achieved: {report['target_met']}. Saved attempts: {len(rows)}. "
             f"Hosted sessions started: {sum(bool(row['session_id']) for row in rows)}."]
    if best:
        lines.extend([
            f"Best accepted score: {best['official_score']:.4f} ({best['variant']}).",
            f"Total cost: EUR {best['total_cost']:,.2f}. Savings against the user's baseline: "
            f"EUR {1541808 - best['total_cost']:,.2f}.",
            f"Accepted terms: {best['terms']}. Must-haves: {best['must_haves_ok']}.",
            f"Evidence: {best['variant']}/run.json, {best['variant']}/evaluation.json, "
            f"{best['variant']}/recorded-replay.json.",
        ])
    lines.extend([
        "", "Pipeline changes:",
        "- Register 72 scoped purchase-order facts and deliver the chosen canonical sources to the actual model input.",
        "- Distinguish 19,880 ordered units, a 21,310-unit forecast, and the authorized 23,400-unit annual need.",
        "- Allow an explicit case-scoped opening fraction with complete-cost and must-have checks.",
        "- Reject recipient-owned offer IDs in email prose before sending; use the existing model repair loop. "
        "Structured acceptance and real rival citations remain available.",
        "- Keep local calculation/retrieval handles in private audits. Outgoing emails must use appropriate "
        "canonical source references or clearly identify a figure as the buyer's calculation.",
        "- Include numeric supplier defaults observed in its opening quote alongside the case options. "
        "Candidate selection and term completion now preserve Ravensteiner's 54 days instead of forcing 56.",
        "- Send exact, fully read source documents selected by canonical ID as Markdown attachments, "
        "restricted to the intended supplier. Preserve source bytes, record hashes, and show the sent text "
        "in later model inputs and the workbench without changing the stored wire request.",
        "- The idle local workbench was reloaded and its live source-attachment transport and preview verified.",
        "", "Validation: see validation.json for the focused attachment tests, shared transport regressions, "
        "workbench checks and Ruff. Test groups overlap and are not an aggregate test count.",
        "Completed recorded paths are replayed locally when this ledger is rebuilt; no API/model calls are used.",
        "", "Target arithmetic:",
        "C <= EUR 1,497,925.17. Full-coverage ceilings are EUR 30.96/unit at 56 days, "
        "EUR 31.07 at the quoted 54-day default, or EUR 31.68 at 42 days.",
        "", "Key findings:",
        "- Lower EUR 25.04 openings scored 63.95 and 63.17; lower anchoring did not help in those trials.",
        "- EUR 29.89 openings led to the strongest accepted outcomes so far.",
        "- Direct committee escalation and a 42-day delivery trade were refused in their respective trials.",
        "- v12 cited the recipient's own offer ID, generating an irrelevant rival-quote result. "
        "v13 used the same strategy after the guard and received six verified claim results, "
        "but still quoted EUR 31.60.",
        "- v14 reached EUR 31.40 after a collaborative sponsored-exception request. "
        "Its estimate and service figures lacked outgoing source IDs, so neither became a checked claim.",
        "- v15 repeated EUR 31.40 with both cost and service claims explicitly cited and verified.",
        "- v16's EUR 29.00 opening and v17's reserved delivery flexibility each finished at EUR 31.50.",
        "- v18 failed before starting a session. v19 exposed that /moves updates the standing offer "
        "without updating the email-history array. The opt-in adapter now uses the actual structured reply "
        "and checks its ID against current server state. It is not the workbench's default transport.",
        "- v20 completed on the corrected typed transport at EUR 31.60 (67.07); "
        "explicit claim typing did not improve on the best email result.",
        "- v21 held back the third contract year until after a final offer. "
        "That fresh commitment still caused withdrawal; do not counter after finality.",
        "- v22 explicitly requested production-planning assessment of 42-day delivery. "
        "The supplier cited reprioritization and overtime and closed at EUR 33.50/42 days (57.81).",
        "- v23 committed all three years upfront and used fresh forecast evidence: EUR 31.40 (68.63). "
        "The forecast was verified but the supplier said it did not affect pricing.",
        "- v24 opened at EUR 28.40 and made a larger EUR 2.50 reciprocal price move: "
        "again EUR 31.40 (68.63). This is evidence against lower anchoring alone as the next improvement.",
        "- v25 supplied the recorded material/conversion/overhead breakdown with the later cost claim; "
        "it also closed at EUR 31.40 (68.63). The estimate remains buyer evidence, not disclosed supplier cost.",
        "- v26's adaptive, high-reasoning run achieved EUR 31.40 with only 24 months at 56 days (68.63). "
        "Its unaccepted 42-day alternative promised a level monthly schedule; that wording is not a "
        "recommended tactic. It also exposed a private calculation ID in email prose, now guarded.",
        "- v27 added a third year to the delivery-flexibility trade and asked for manager approval, "
        "but again closed at EUR 31.40/36 months/56 days. The ended backoffice showed Legal involvement, "
        "not an approval by a pricing manager.",
        "- v28 explicitly requested the boss's price approval. Ms Zellner stated pricing authority "
        "resided with her; the result remained EUR 31.40/56 days.",
        "- v29 demonstrated that the hosted API accepts the quoted 54-day default. At EUR 31.40 "
        "this saved EUR 4,800 and improved the official score to 69.43, with all must-haves satisfied.",
        "- v30 combined three fresh, verified evidence groups with the moderate opening and 54 days. "
        "It also closed at EUR 31.40 (69.43); verified delivery and forecast facts were dismissed as pricing reasons.",
        "- v31 offered the exact EUR 31.07 public-cost ceiling in the second exchange, before finality. "
        "The supplier again offered EUR 31.40 at 54 days (69.43). Independent server verification confirms "
        "an accepted deal with all must-haves, below the target.",
        "- v32 explicitly exchanged the recorded 42-day delivery standard for the quoted 54 days, "
        "alongside the binding term extension. It closed at EUR 31.30 (70.21), independently verified "
        "by the server with all must-haves. This exploratory result does not establish a causal effect.",
        "- v33 added historical forecast evidence and the proposed 70,200-unit three-year total before "
        "finality. It closed at EUR 31.50/54 days (68.65), below v32. The added explanation is not "
        "promoted; this single comparison cannot isolate whether wording or supplier variability caused the change.",
        "- v34 repeated the exact v32 strategy and model settings, but closed at EUR 31.40 (69.43). "
        "The best v32 outcome is not yet a reliably reproduced result. Neither run had local deadline failures.",
        "- v35 supplied the recent EUR 29.89 order prices with the second offer to substantiate the "
        "proposed EUR 1.18 increase. The supplier verified the order history but said it did not change "
        "the calculation, closing at EUR 31.50/54 days (68.65).",
        "- v36 answered an explicit volume question in the final quote with no term sheet or price "
        "request. The supplier verified the new fact but withdrew, scoring zero. Even a requested "
        "factual answer after finality is unsafe in the observed P10 behavior. Contradictory prose "
        "saying the offer remains valid does not override the authoritative walked_away status.",
        "- v37 opened at EUR 28.40 using the buyer estimate plus its 5% uncertainty and a modeled "
        "10% gross margin. The supplier verified the source, rejected the modeled economics as a "
        "pricing basis, and closed at EUR 31.40/54 days (69.43). This lower opening did not beat v32.",
        "- v38 explicitly requested committee price approval after the first counter at EUR 31.00. "
        "The supplier stated Ms Zellner was authorized directly, requested the actual historical note, "
        "and closed at EUR 31.40 (69.43).",
        "- v39 delivered the exact March note as a Markdown attachment before finality. The server "
        "stored it and the workbench displayed the exact source text. Ms Zellner again stated she held "
        "approval authority and closed at EUR 31.50 (68.65). Document delivery is fixed; a scoring "
        "benefit from that historical note is not established.",
        "- v40 reran the best v32 strategy on the updated source-attachment pipeline and closed at "
        "EUR 31.40/54 days (69.43). Current pipeline correctness does not establish attainment of 72.",
        "", "Trials:",
    ])
    for row in rows:
        terms = row["last_supplier_offer"][0]["terms"] if row["last_supplier_offer"] else {}
        outcome = "accepted" if row["accepted"] else (
            "failed before session start" if not row["session_id"] else
            "local stop/failure; see error and cleanup" if row["error"] else "supplier withdrawal")
        lines.append(f"{row['variant']}: {outcome}; official score {row['official_score']}; "
                     f"last quote {terms}; evaluation {row['evaluation_id']}; transport {row['transport']}")
    lines.extend(["", *report["limitations"], "",
                  "No unsuccessful experimental strategy was installed as the global default. "
                  "The source/transport fixes are active. Each folder preserves its actual prompt and run."])
    (ROOT / "notes.txt").write_text("\n".join(lines) + "\n")
    print(json.dumps({"trials": len(rows), "target_met": report["target_met"],
                      "best_score": best["official_score"] if best else None}))


if __name__ == "__main__":
    summarize()
