"""Audit saved P18 offers and cached, read-only server traces without model/API calls."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from arena_kit.cost import score, total_cost
from arena_kit.docs import standard_terms

from aeiou.config import ROOT


def read(path):
    return json.loads(path.read_text())


def claim_key(claim):
    if claim.get("type") == "index_change":
        return "index_change:" + ",".join(sorted(claim.get("refs", [])))
    return claim.get("type", "unknown")


def main():
    output = ROOT / "reports/p18-behavior-analysis"
    runs = sorted(
        (read(p) for p in (ROOT / "data/local/runs").glob("*.json")),
        key=lambda r: r.get("created_at", ""),
    )
    runs = [r for r in runs if r.get("case_id") == "case:P18" and r.get("mode") == "hosted"]
    public_case = read(ROOT / "data/package/cases/practice/P18.json")
    offers, responses, summaries, post_final = [], [], [], []
    claim_counts = defaultdict(Counter)
    final_rounds = Counter()
    final_validity = Counter()
    for run in runs:
        snapshot_path = output / "sessions" / (run["id"] + ".json")
        session = read(snapshot_path)
        assert session["case_id"] == "case:P18"
        assert session["session_id"] == run["session_id"]
        trace = session.get("result", {}).get("trace", [])
        assert trace, f"Missing server trace for {run['id']}"
        case = run.get("case") or public_case
        result = session["result"]
        c_open, c_best = result["c_open"], result["c_best"]
        standard = standard_terms(run.get("threads", {}).get("sup:70740", []))
        previous = None
        buyer = None
        first_final = None
        run_offers = []
        for item in trace:
            move = item["move"]
            if item["actor"] == "buyer":
                buyer = item
                if previous and previous["move"].get("status") == "final_offer":
                    post_final.append({
                        "run_id": run["id"], "round": item["round"],
                        "buyer_type": move.get("type"), "body": move.get("body"),
                        "session_end_reason": session.get("end_reason"),
                        "supplier_response_status": None,
                    })
                continue
            assert item["actor"] == "sup:70740"
            status = move.get("status")
            if status == "final_offer" and first_final is None:
                first_final = item["round"]
                final_rounds[first_final] += 1
                final_validity[move.get("signals", {}).get("validity_days")] += 1
            if (post_final and post_final[-1]["run_id"] == run["id"]
                    and post_final[-1]["round"] == item["round"]):
                post_final[-1]["supplier_response_status"] = status
            current = []
            if move.get("terms", {}).get("unit_price") is not None:
                packages = [{"offer_id": move["offer_id"], "terms": move["terms"], "primary": True}]
                packages += [dict(p, primary=False) for p in move.get("alternatives", [])]
                for package in packages:
                    terms = package["terms"]
                    cost = total_cost(case, terms, standard)
                    row = {
                        "run_id": run["id"], "round": item["round"], "status": status,
                        "offer_id": package["offer_id"], "primary": package["primary"],
                        "terms": terms, "total_cost": cost,
                        "calculated_score": score(c_open, cost, c_best) * 100,
                        "qualification_note": "Quoted package, not proof it was eligible or accepted.",
                    }
                    current.append(row)
                    offers.append(row)
                    run_offers.append(row)
            if buyer and buyer["move"].get("type") == "message":
                claims = buyer["move"].get("interpreted", {}).get("claims", [])
                checked = []
                for check in move.get("claim_results", []):
                    index = check["index"]
                    assert 0 <= index < len(claims), (run["id"], item["round"], check)
                    claim = claims[index]
                    checked.append({**check, "claim": claim})
                    claim_counts[claim_key(claim)][check["result"]] += 1
                responses.append({
                    "run_id": run["id"], "round": item["round"], "status": status,
                    "buyer_body": buyer["move"].get("body"),
                    "buyer_packages": buyer["move"].get("term_sheets", []),
                    "checks": checked, "supplier_body": move.get("text"),
                    "supplier_reasons": item.get("reasons", []), "offers": current,
                    "previous_supplier_status": previous["move"].get("status") if previous else None,
                })
            previous = item
            buyer = None
        summaries.append({
            "run_id": run["id"], "created_at": run["created_at"],
            "source_file": str(ROOT / "data/local/runs" / (run["id"] + ".json")),
            "session_snapshot": str(snapshot_path),
            "session_snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            "model": run.get("model"), "effort": run.get("effort"),
            "protocol_version": run.get("prompt", {}).get("protocol_version"),
            "official_result": {k: v for k, v in result.items() if k != "trace"},
            "local_run_status": run.get("status"), "session_end_reason": session.get("end_reason"),
            "server_rounds": session.get("rounds"), "first_final_round": first_final,
            "best_quoted_package": max(run_offers, key=lambda x: x["calculated_score"], default=None),
        })
    unique_ids = {(o["run_id"], o["offer_id"]) for o in offers}
    assert len(unique_ids) == len(offers), "Unexpected repeated supplier quote in server trace"
    negotiated = [o for o in offers if o["round"] > 0]
    off_grid = [o for o in negotiated if Decimal(str(o["terms"]["unit_price"])) % Decimal("0.1")]
    accepted = [r for r in summaries if r["official_result"].get("deal")]
    latest = runs[-1]
    c_open = latest["result"]["c_open"]
    c_best = latest["result"]["c_best"]
    budget = 244400.60
    clause = {"series": "AS-14404", "material_share": 0.45, "deadband_pct": 5, "base_month": "2026-08"}
    economics = []
    for days in public_case["term_options"]["payment_days"]:
        t = {"unit_price": 1.0, "payment_days": days, "contract_months": 36, "index_clause": clause}
        per_euro = total_cost(public_case, t)
        cap = min(Decimal(str(budget)) / Decimal(str(per_euro)), Decimal("5.10"))
        cents = cap.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        tenths = cap.quantize(Decimal("0.1"), rounding=ROUND_FLOOR)
        prices = sorted({5.00, 5.09, 5.10, 5.20, float(cents), float(tenths)})
        packages = []
        for price in prices:
            terms = dict(t, unit_price=price)
            cost = total_cost(public_case, terms)
            packages.append({
                "unit_price": price, "total_cost": cost,
                "calculated_score": score(c_open, cost, c_best) * 100,
                "qualifies": cost <= budget and price <= 5.10,
            })
        economics.append({
            "payment_days": days, "highest_qualifying_cent_price": float(cents),
            "highest_qualifying_tenth_price": float(tenths), "packages": packages,
        })
    counts = {
        "hosted_runs": len(runs), "official_deals": len(accepted),
        "official_no_deals": len(runs) - len(accepted),
        "supplier_withdrawals": sum(r["session_end_reason"] == "no_deal" for r in summaries),
        "buyer_api_closures": sum(r["session_end_reason"] == "walked_away" for r in summaries),
        "negotiated_quoted_packages": len(negotiated), "off_ten_cent_grid": len(off_grid),
        "first_final_round_counts": dict(sorted(final_rounds.items())),
        "first_final_validity_days": dict(final_validity),
        "post_final_email_count": sum(p["buyer_type"] == "message" for p in post_final),
        "post_final_email_withdrawals": sum(
            p["buyer_type"] == "message" and p["supplier_response_status"] == "walked_away" for p in post_final),
    }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "method": "Public case and cost function; own saved runs; read-only snapshots of owned hosted sessions. "
                  "Claim/result mapping comes from server-interpreted claims, not keyword guesses. "
                  "Offer counts include primary and advertised alternatives, not accept confirmations. "
                  "Changing strategies/packages confound causal attribution. No private supplier instructions used.",
        "counts": counts,
        "best_quoted_package": max(offers, key=lambda x: x["calculated_score"]),
        "best_accepted_result": max(accepted, key=lambda r: r["official_result"]["score"]),
        "claim_result_counts": dict(sorted((k, dict(v)) for k, v in claim_counts.items())),
        "economics_basis": {"c_open": c_open, "c_best": c_best, "maximum_total_cost": budget,
                            "nominal_cap": 5.10, "minimum_score": 70.01, "contract_months": [24, 36],
                            "index_clause": clause, "units_scored": 47400,
                            "tenth_grid_note": "Observed historical pattern, not a documented hard restriction."},
        "economics": economics, "runs": summaries, "responses": responses,
        "post_final_actions": post_final, "off_grid_offers": off_grid,
        "paid_model_calls": 0, "hosted_sessions_started": 0,
    }
    (output / "observations.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"counts": counts, "claims": report["claim_result_counts"],
                      "best_quoted": report["best_quoted_package"], "economics": economics}, indent=2))


if __name__ == "__main__":
    main()
