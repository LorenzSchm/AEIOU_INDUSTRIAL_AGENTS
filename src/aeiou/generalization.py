"""Offline out-of-distribution checks. No hosted sessions or model calls.

Run this file directly with --agent-source to evaluate a frozen source snapshot.
Generated identities are unrelated to the Arena cases; private seller economics
remain in StressSim. Scores are synthetic diagnostics, not expected Arena scores.
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


def generate_cases(split):
    from aeiou.simulation import local_cases

    seed = {"development": 1873, "holdout": 91009}[split]
    rng = random.Random(seed)
    templates = local_cases()
    indexed = copy.deepcopy(next(iter(templates.values())))
    indexed.update(
        case_id="synthetic:index", type="raw_material", negotiable=["unit_price", "payment_days", "index_clause"]
    )
    indexed["fixed_terms"].pop("index_clause", None)
    indexed["term_options"]["index_clause"] = [
        None,
        {"series": "SYNTHETIC-INDEX", "material_share": 0.35, "deadband_pct": 3, "base_month": "2026-01"},
    ]
    indexed["buyer"].update(
        category_goal="stabilise",
        sustainable_margin_pct=3,
        relationship_eur_per_point=1000,
        index_paths={"SYNTHETIC-INDEX": 0.06},
    )
    templates[indexed["case_id"]] = indexed
    result = []
    for base in templates.values():
        for variant in range(2 if split == "development" else 4):
            case = copy.deepcopy(base)
            label = hashlib.sha256(f"{seed}:{base['case_id']}:{variant}".encode()).hexdigest()[:12]
            case.update(
                case_id=f"case:G{label}",
                title="Synthetic procurement negotiation",
                briefing="Negotiate the published need and legal terms.",
                refs=[],
                facts={},
                trigger_doc=None,
                parts=[f"part:G{label}"],
                suppliers=[f"sup:G{label}-{i}" for i in range(len(base["suppliers"]))],
            )
            b = case["buyer"]
            price_scale = rng.uniform(0.45, 2.7)
            volume_scale = rng.uniform(0.6, 1.8)
            for k in (
                "current_unit_price",
                "fallback_unit_price",
                "cost_per_defect",
                "late_cost_per_day",
                "relationship_eur_per_point",
            ):
                if b.get(k) is not None:
                    b[k] = round(b[k] * price_scale, 4)
            b["need_per_year"] = max(1, round(b["need_per_year"] * volume_scale))
            b["interest_rate"] = rng.choice([0.03, 0.09, 0.16])
            b["freight_eur_per_unit"] = {k: round(v * price_scale, 4) for k, v in b["freight_eur_per_unit"].items()}
            b["alternative"] = {"kind": "none", "ref": None, "terms": {}}
            for term in case.get("term_options", {}):
                if term in ("volume_per_year", "chargeback_eur_per_complaint"):
                    factor = volume_scale if term == "volume_per_year" else price_scale
                    case["term_options"][term] = [round(v * factor) for v in case["term_options"][term]]
            if "volume_per_year" in case["fixed_terms"]:
                case["fixed_terms"]["volume_per_year"] = b["need_per_year"]
            for rule in b.get("must_haves", []):
                if rule["field"] == "volume_per_year":
                    rule["value"] = round(rule["value"] * volume_scale)
            case["round_limit"] = rng.choice([6, 8, 10])
            estimate_ratio = rng.uniform(0.75, 1.15)
            result.append(
                {
                    "case": case,
                    "base_family": base["type"],
                    "seed": rng.randrange(100000, 999999),
                    "estimate_ratio": estimate_ratio,
                    "confidence_pct": rng.choice([10, 20, 30]),
                }
            )
    return result


def run_arm(split, output):
    from arena_kit import ArenaClient
    from arena_kit.play import play

    from aeiou.research import package
    from aeiou.simulation import LocalTable, StressSim
    from aeiou.store import new_run
    from aeiou.strategic import StrategicNegotiator

    def no_network(*args, **kwargs):
        raise AssertionError("Hosted API disabled in generalization evaluation")

    ArenaClient.__init__ = no_network
    specifications = generate_cases(split)
    pkg, rows = package(), []
    for spec in specifications:
        case = spec["case"]
        for opponent in ("hardliner", "cashflow", "relationship", "adversarial"):

            class EstimatedAgent(StrategicNegotiator):
                def prepare(
                    self, incoming, estimate_ratio=spec["estimate_ratio"], confidence_pct=spec["confidence_pct"]
                ):
                    super().prepare(incoming)
                    # A noisy procurement estimate, created independently of the
                    # hidden simulator floor. Both arms receive exactly this prior.
                    price = incoming["buyer"].get("current_unit_price") or incoming["buyer"]["fallback_unit_price"]
                    self.intelligence["estimates"] = {
                        sid: {
                            "full_cost": price * estimate_ratio,
                            "confidence_pct": confidence_pct,
                            "source": "synthetic-buyer-estimate",
                        }
                        for sid in incoming["suppliers"]
                    }

            agent = EstimatedAgent(pkg, backend=None)
            sim = StressSim({case["case_id"]: case}, opponent, spec["seed"], {})
            table = LocalTable(sim, new_run(case["case_id"], "local", "generalization"))
            row = {
                "case_id": case["case_id"],
                "family": spec["base_family"],
                "opponent": opponent,
                "seed": spec["seed"],
            }
            try:
                outcome = play(table, case["case_id"], agent)
                row.update(
                    score=outcome["score"],
                    deal=outcome.get("deal"),
                    must_haves_ok=outcome.get("must_haves_ok"),
                    error=None,
                    uncertainty_probes=sum(
                        bool(p.get("uncertainty_probe"))
                        for d in agent.decisions
                        for s in d.get("decision_support", {}).values()
                        for p in s.get("plan", {}).get("packages", [])
                    ),
                )
            except Exception as exc:
                row.update(score=0, deal=False, must_haves_ok=None, error=f"{type(exc).__name__}: {exc}")
            rows.append(row)
        if len(rows) % 20 == 0:
            print(json.dumps({"completed": len(rows), "planned": len(specifications) * 4}), flush=True)
    report = {
        "split": split,
        "rows": rows,
        "case_count": len(specifications),
        "sessions": len(rows),
        "dataset_hash": hashlib.sha256(json.dumps(specifications, sort_keys=True).encode()).hexdigest(),
        "mean_score": mean(r["score"] for r in rows),
        "failures": sum(bool(r["error"]) for r in rows),
        "must_have_violations": sum(r["must_haves_ok"] is False for r in rows),
        "arena_sessions_used": 0,
        "llm_calls": 0,
        "limitation": "Synthetic supplier policies and generated variations of six case templates. "
        "No LLM variation, live timeouts or independent real-world case families. "
        "Supplier sustainability estimates may differ from the simplified simulator economics.",
    }
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "holdout"], required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agent-source")
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    os.environ["ARENA_DATA"] = str(Path(args.data).resolve())
    if args.agent_source:
        sys.path.insert(0, str(Path(args.agent_source).resolve()))
    run_arm(args.split, args.output)
