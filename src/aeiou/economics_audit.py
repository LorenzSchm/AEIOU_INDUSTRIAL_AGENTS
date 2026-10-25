"""Offline cost-ranking diagnostics with hidden economics confined to evaluator.

No Arena or model calls. Errors/regret are EUR, not Arena score predictions.
"""

import argparse
import copy
import itertools
import json
import random
from pathlib import Path
from statistics import mean

from arena_kit.cost import closing_terms, complete_offer
from arena_kit.cost import total_cost as published_cost

from .economic_beliefs import infer_economics, total_cost
from .simulation import local_cases


def run(seed=190481, cases=100):
    rng = random.Random(seed)
    rows = []
    for index in range(cases):
        case = copy.deepcopy(local_cases()["case:S01"])
        case["buyer"].update(
            category_goal="stabilise", sustainable_margin_pct=3, relationship_eur_per_point=rng.uniform(500, 5000)
        )
        true_cost = rng.uniform(10, 100)
        band = rng.choice([5, 10, 15])
        quoted = true_cost * (1 + rng.uniform(-0.01, 0.15))
        # The prior is noisy independently of the offers/actions being evaluated.
        prior_cost = true_cost / (1 + rng.uniform(-band / 100, band / 100))
        opening = {"unit_price": quoted, "payment_days": 30}
        standard = closing_terms(case, opening)
        hidden = {"full_cost": true_cost, "interest_rate": rng.uniform(0.02, 0.35), "standard": standard}
        prior = {"full_cost": prior_cost, "confidence_pct": band}
        events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": opening}]}]
        posterior = infer_economics(case, events, prior, standard)
        baseline = {"full_cost": prior_cost, "interest_rate": 0.12, "standard": standard}
        expected = {**baseline, **posterior}
        options = [
            complete_offer(case, {"unit_price": quoted * discount, "payment_days": pay})
            for discount, pay in itertools.product((0.87, 0.93, 0.99), (14, 60, 90))
        ]
        truth = [published_cost(case, t, standard, hidden) for t in options]
        for method, supplier in (("prior_point", baseline), ("scenario_expectation", expected)):
            estimates = [total_cost(case, t, standard, supplier) for t in options]
            choice = min(range(len(options)), key=lambda i: estimates[i])
            pairs = [
                (i, j) for i, j in itertools.combinations(range(len(options)), 2) if abs(truth[i] - truth[j]) > 0.01
            ]
            rows.append(
                {
                    "case": index,
                    "method": method,
                    "absolute_error": mean(abs(a - b) for a, b in zip(estimates, truth, strict=True)),
                    "ranking_error": mean((estimates[i] - estimates[j]) * (truth[i] - truth[j]) < 0 for i, j in pairs),
                    "decision_regret": truth[choice] - min(truth),
                }
            )
    return {
        "seed": seed,
        "cases": cases,
        "arena_sessions": 0,
        "llm_calls": 0,
        "summary": {
            method: {
                metric: mean(r[metric] for r in rows if r["method"] == method)
                for metric in ("absolute_error", "ranking_error", "decision_regret")
            }
            for method in ("prior_point", "scenario_expectation")
        },
        "rows": rows,
        "limitation": "Synthetic economics and fixed candidate menus; does not establish negotiation or Arena gains.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=190481)
    parser.add_argument("--cases", type=int, default=100)
    args = parser.parse_args()
    report = run(args.seed, args.cases)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
