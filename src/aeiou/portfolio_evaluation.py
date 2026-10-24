"""Offline multi-issue sensitivity experiment, not an Arena performance forecast.

Run as a script with --agent-source to compare frozen source on identical cases.
Hidden pricing policies exist only in this evaluator and never enter agent inputs.
"""

import argparse
import copy
import hashlib
import json
import os
import random
import sys
from pathlib import Path
from statistics import mean


def specifications(split):
    from aeiou.simulation import local_cases

    rng = random.Random({"development": 43172, "holdout": 831023, "sealed": 336451}[split])
    bases = local_cases()
    cases = []
    for family in ("quality", "index", "delivery"):
        for i in range(3 if split == "development" else 6):
            case = copy.deepcopy(bases["case:T01" if family == "quality" else "case:S01"])
            label = hashlib.sha256(f"{split}:{family}:{i}".encode()).hexdigest()[:12]
            case.update(
                case_id=f"case:M{label}",
                suppliers=[f"sup:M{label}"],
                parts=[f"part:M{label}"],
                title="Unseen multi-issue procurement",
                briefing="Negotiate the published requirements.",
                refs=[],
                facts={},
                trigger_doc=None,
                round_limit=rng.choice([6, 8, 10]),
            )
            b = case["buyer"]
            price = round(rng.uniform(18, 85), 2)
            need = rng.randrange(3000, 15000)
            b.update(
                current_unit_price=price,
                fallback_unit_price=price * 1.3,
                need_per_year=need,
                category_goal="leverage",
                horizon_years=2,
                alternative={"kind": "none", "terms": {}},
                interest_rate=rng.choice([0.04, 0.09, 0.15]),
                must_haves=[],
            )
            case["fixed_terms"].update(volume_per_year=need, contract_months=24)
            if family == "quality":
                quality = rng.choice([1000, 1600, 2000])
                liability = rng.choice([180, 300, 420])
                b.update(
                    cost_per_defect=liability, must_haves=[{"field": "ppm_commitment", "op": "<=", "value": quality}]
                )
                case["term_options"] = {
                    "ppm_commitment": [quality // 4, quality, quality * 2],
                    "chargeback_eur_per_complaint": [0, liability // 3, liability],
                }
            elif family == "index":
                case["negotiable"] = ["unit_price", "payment_days", "index_clause"]
                case["fixed_terms"].pop("index_clause", None)
                b.update(index_paths={"UNSEEN": rng.uniform(0.02, 0.10)}, index_risk_pct=rng.choice([1, 2, 4]))
                case["term_options"] = {
                    "payment_days": [14, 30, 60, 90],
                    "index_clause": [
                        None,
                        {"series": "UNSEEN", "material_share": 0.3, "deadband_pct": 3, "base_month": "2026-01"},
                        {"series": "UNSEEN", "material_share": 0.3, "deadband_pct": 5, "base_month": "2026-01"},
                    ],
                }
            else:
                case["negotiable"] = ["unit_price", "lead_time_days", "contract_months"]
                case["fixed_terms"].pop("lead_time_days", None)
                case["fixed_terms"].pop("contract_months", None)
                case["fixed_terms"]["payment_days"] = 30
                b.update(need_lead_time_days=28, late_cost_per_day=round(price * need * rng.uniform(0.001, 0.004)))
                case["term_options"] = {"lead_time_days": [21, 28, 49], "contract_months": [12, 24, 36]}
            cases.append({"case": case, "family": family, "seed": rng.randrange(1000000)})
    return cases


def run(split, output, disabled=()):
    from arena_kit import ArenaClient
    from arena_kit.play import play

    from aeiou.research import package
    from aeiou.simulation import LocalTable, StressSim
    from aeiou.store import new_run
    from aeiou.strategic import StrategicNegotiator
    from aeiou.strategy_policy import StrategicFeatures

    def no_network(*args, **kwargs):
        raise AssertionError("Arena API disabled in offline evaluation")

    ArenaClient.__init__ = no_network

    class MultiIssueSim(StressSim):
        def __init__(self, spec, sensitivity):
            super().__init__({spec["case"]["case_id"]: spec["case"]}, "relationship", spec["seed"], {})
            self.spec, self.sensitivity = spec, sensitivity

        def required_price(self, opening, terms):
            b = self.spec["case"]["buyer"]
            ratio = self.floor_ratio
            family, sensitivity = self.spec["family"], self.sensitivity
            if family == "quality":
                ratio += sensitivity * 0.09 * terms.get("chargeback_eur_per_complaint", 0) / b["cost_per_defect"]
                limit = b["must_haves"][0]["value"]
                ratio += sensitivity * 0.04 * max(0, 1 - terms.get("ppm_commitment", limit) / limit)
            elif family == "index":
                ratio -= sensitivity * 0.07 if terms.get("index_clause") else 0
                ratio += 0.10 * (terms.get("payment_days", 30) - 30) / 360
            else:
                ratio += sensitivity * 0.06 if terms.get("lead_time_days", 28) <= 28 else 0
                ratio -= 0.015 if terms.get("contract_months", 12) >= 24 else 0
            if split == "sealed":
                # New cross-issue interactions, kept inside the seller simulator.
                # Do not infer the supplier's real costs from these test policies.
                if family == "quality":
                    ratio += (
                        sensitivity
                        * 0.03
                        * max(0, 1 - terms.get("ppm_commitment", limit) / limit)
                        * (terms.get("chargeback_eur_per_complaint", 0) / b["cost_per_defect"])
                    )
                elif family == "index":
                    ratio -= (
                        sensitivity * 0.035 if terms.get("index_clause") and terms.get("payment_days", 30) <= 30 else 0
                    )
                elif terms.get("lead_time_days", 28) <= 28 and terms.get("contract_months", 12) < 24:
                    ratio += abs(sensitivity) * 0.025
            return round(opening * ratio, 2)

        def _answer(self, s, th, msg, sheets):
            # Supplier selects its best economic surplus, not blindly highest price.
            chosen = max(sheets, key=lambda t: t["unit_price"] - self.required_price(th["opening"], t), default=None)
            return super()._answer(s, th, msg, [chosen] if chosen else [])

    specs, pkg, rows = specifications(split), package(), []
    for spec in specs:
        for sensitivity in (-1.0, 0.65, 2.0) if split == "sealed" else (-0.5, 0.0, 1.5):
            case = spec["case"]
            agent = StrategicNegotiator(pkg, backend=None, features=StrategicFeatures(**dict.fromkeys(disabled, False)))
            table = LocalTable(MultiIssueSim(spec, sensitivity), new_run(case["case_id"], "local", "portfolio-audit"))
            row = {"case_id": case["case_id"], "family": spec["family"], "sensitivity": sensitivity}
            try:
                outcome = play(table, case["case_id"], agent)
                row.update(
                    score=outcome["score"],
                    deal=outcome.get("deal"),
                    cost=outcome.get("c_deal"),
                    must_haves_ok=outcome.get("must_haves_ok"),
                    error=None,
                )
            except Exception as exc:
                row.update(score=0, deal=False, cost=None, must_haves_ok=None, error=f"{type(exc).__name__}: {exc}")
            rows.append(row)
        print(json.dumps({"completed": len(rows), "planned": len(specs) * 3}), flush=True)
    report = {
        "split": split,
        "disabled_features": list(disabled),
        "rows": rows,
        "sessions": len(rows),
        "mean_score": mean(r["score"] for r in rows),
        "dataset_hash": hashlib.sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest(),
        "failures": sum(bool(r["error"]) for r in rows),
        "must_have_violations": sum(r["must_haves_ok"] is False for r in rows),
        "arena_sessions_used": 0,
        "llm_calls": 0,
        "limitation": "New scripted sensitivity policies, including indifferent and reversed preferences. "
        "Synthetic scores are not Arena predictions. No LLM, latency or supplier-cost-estimate uncertainty.",
    }
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "holdout", "sealed"], required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agent-source")
    parser.add_argument("--data", required=True)
    parser.add_argument("--disable", action="append", default=[])
    args = parser.parse_args()
    os.environ["ARENA_DATA"] = str(Path(args.data).resolve())
    if args.agent_source:
        sys.path.insert(0, str(Path(args.agent_source).resolve()))
    run(args.split, args.output, args.disable)
