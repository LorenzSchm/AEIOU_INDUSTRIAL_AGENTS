"""Paired local evaluation, single-feature ablations and case-cluster uncertainty.

No hosted Arena client is instantiated here. Model-backed runs require explicit
API opt-in. Seller failures exclude the entire paired condition, not just one arm.
"""

import hashlib
import json
import math
import random
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

from arena_kit.play import play
from arena_kit.sim import Sim

from .agent import make_backend
from .config import state_root
from .experimental_simulation import OPPONENTS, StressSim
from .language_sim import LanguageSim, SupplierSimulationError
from .research import package
from .runner import finish_run, make_agent
from .simulation import LocalTable, local_cases
from .store import clean, new_run, save_run
from .strategic import StrategicNegotiator
from .strategy_policy import StrategicFeatures


def quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def paired_interval(rows, reference, challenger, samples=1000):
    groups = {}
    for row in rows:
        if not row.get("excluded") and reference in row["scores"] and challenger in row["scores"]:
            groups.setdefault(row["case_id"], []).append(row["scores"][challenger] - row["scores"][reference])
    if not groups:
        return None
    rng, keys, draws = random.Random(71), list(groups), []
    # Cluster by base case so repeated seeds/profiles do not masquerade as new cases.
    for _ in range(samples):
        picked = [rng.choice(keys) for _ in keys]
        draws.append(mean(mean(groups[k]) for k in picked))
    return {
        "mean_case_delta": mean(mean(v) for v in groups.values()),
        "low_95": quantile(draws, 0.025),
        "high_95": quantile(draws, 0.975),
        "case_clusters": len(keys),
        "bootstrap_samples": samples,
        "limitation": "Case-cluster bootstrap; few base cases produce limited evidence of generalization.",
    }


def features_for(label):
    name, _, ablation = label.partition(":")
    features = StrategicFeatures()
    if ablation:
        enabled = ablation.startswith("with-")
        field = ablation.removeprefix("with-" if enabled else "no-").replace("-", "_")
        if (
            not ablation.startswith(("with-", "no-"))
            or name not in {"strategic", "strategic-policy"}
            or field not in features.metadata()
        ):
            raise ValueError("Unknown strategic ablation")
        features = replace(features, **{field: enabled})
    return name, features


def evaluation_agent(label, pkg, buyer_model=None, backend_factory=make_backend, writer_instruction=""):
    name, features = features_for(label)
    if name in {"strategic", "strategic-policy"}:
        return StrategicNegotiator(
            pkg,
            backend_factory(model=buyer_model) if name == "strategic" else None,
            features=features,
            writer_instruction=writer_instruction if not features.strategic_planner else "",
            planner_instruction=writer_instruction if features.strategic_planner else "",
        )
    if name == "claude" and buyer_model:
        from .agent import Negotiator

        return Negotiator(pkg, backend_factory(model=buyer_model))
    if name == "claude":
        from .agent import Negotiator

        return Negotiator(pkg, backend_factory())
    if name not in {"adaptive", "guarded", "baseline"}:
        raise ValueError("Unknown agent")
    return make_agent(name, pkg)


def evaluate_run(
    case_id,
    label,
    opponent,
    seed,
    pkg,
    supplier_mode,
    supplier_model,
    buyer_model,
    backend_factory,
    supplier_backend_factory,
    writer_instruction="",
):
    cases = local_cases()
    agent = evaluation_agent(label, pkg, buyer_model, backend_factory, writer_instruction)
    supplier_backend = None
    if supplier_mode == "language":
        supplier_backend = supplier_backend_factory(model=supplier_model)
        sim = LanguageSim(cases, opponent, seed, pkg.facts, supplier_backend)
    else:
        sim = (
            Sim(cases, pkg.facts, no_clock=True)
            if opponent == "scripted"
            else StressSim(cases, opponent, seed, pkg.facts)
        )
    run = new_run(case_id, "local", label)
    run.update(opponent=opponent, seed=seed, evaluation=True)
    table = LocalTable(sim, run)
    started = time.monotonic()
    try:
        result = play(table, case_id, agent)
        run["threads"] = {s: t["messages"] for s, t in sim.sessions[table.session_id]["threads"].items()}
        finish_run(run, agent, result, started)
    except Exception as exc:
        finish_run(run, agent, {}, started)
        run.update(
            status="failed",
            failure_origin="supplier" if isinstance(exc, SupplierSimulationError) else "buyer",
            error=type(exc).__name__,
        )
        save_run(run)
    run["supplier_metrics"] = {
        k: getattr(supplier_backend, k, 0) for k in ("calls", "errors", "input_tokens", "output_tokens")
    }
    save_run(run)
    return run


def evaluate_matrix(
    agents=("adaptive", "strategic-policy"),
    seeds=(99, 100),
    case_ids=None,
    opponents=None,
    repeats=1,
    supplier_mode="scripted",
    supplier_models=(None,),
    buyer_model=None,
    allow_api=False,
    max_sessions=120,
    output=None,
    backend_factory=make_backend,
    supplier_backend_factory=make_backend,
    writer_instructions=None,
):
    agents, seeds = list(agents), list(seeds)
    cases = local_cases()
    case_ids = list(case_ids or cases)
    if not agents or len(set(agents)) != len(agents) or not seeds or not 1 <= repeats <= 20:
        raise ValueError("Distinct agents, seeds and 1–20 repetitions are required")
    for label in agents:
        name, _ = features_for(label)
        if name not in {"baseline", "adaptive", "guarded", "claude", "strategic", "strategic-policy"}:
            raise ValueError("Unknown agent")
    if supplier_mode not in {"scripted", "language"} or set(case_ids) - set(cases):
        raise ValueError("Unknown supplier mode or case")
    opponents = list(opponents or [o for o in OPPONENTS if supplier_mode != "language" or o != "scripted"])
    if not opponents or set(opponents) - set(OPPONENTS) or (supplier_mode == "language" and "scripted" in opponents):
        raise ValueError("Unknown or incompatible opponent")
    models = list(supplier_models) if supplier_mode == "language" else [None]
    if not models:
        raise ValueError("At least one supplier model is required")
    uses_api = supplier_mode == "language" or any(features_for(a)[0] in {"claude", "strategic"} for a in agents)
    planned = len(agents) * len(seeds) * len(case_ids) * len(opponents) * repeats * len(models)
    if uses_api and not allow_api:
        raise ValueError("Model-backed evaluation requires explicit allow_api=True / --allow-api")
    if planned > max_sessions or max_sessions < 1:
        raise ValueError(f"Evaluation plans {planned} sessions; explicit limit is {max_sessions}")
    folder = Path(output) if output else state_root() / "evaluations" / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    pkg, rows, runs_by_agent = package(), [], {a: [] for a in agents}
    all_attempts = {a: [] for a in agents}
    for model in models:
        for seed in seeds:
            for case_id in case_ids:
                for opponent in opponents:
                    for repeat in range(repeats):
                        outcomes = {}
                        for label in agents:
                            outcomes[label] = evaluate_run(
                                case_id,
                                label,
                                opponent,
                                seed,
                                pkg,
                                supplier_mode,
                                model,
                                buyer_model,
                                backend_factory,
                                supplier_backend_factory,
                                (writer_instructions or {}).get(label, ""),
                            )
                        excluded = any(r.get("failure_origin") == "supplier" for r in outcomes.values())
                        for label, run in outcomes.items():
                            all_attempts[label].append(run)
                        row = {
                            "case_id": case_id,
                            "opponent": opponent,
                            "seed": seed,
                            "repeat": repeat,
                            "supplier_model": model,
                            "excluded": excluded,
                            "scores": {a: r.get("result", {}).get("score", 0) for a, r in outcomes.items()},
                            "run_ids": {a: r["id"] for a, r in outcomes.items()},
                        }
                        rows.append(row)
                        if not excluded:
                            for label, run in outcomes.items():
                                runs_by_agent[label].append(run)
                        # Durable progress without repeatedly overwriting versioned benchmark reports.
                        (folder / "checkpoint.json").write_text(json.dumps(clean(rows), indent=2), encoding="utf-8")
    summary = {}
    for label, runs in runs_by_agent.items():
        scores = [r.get("result", {}).get("score", 0) for r in runs]
        times = [d["elapsed_s"] for r in runs for d in r.get("decisions", [])]
        summary[label] = {
            "mean_score": mean(scores) if scores else None,
            "median_score": median(scores) if scores else None,
            "p10_score": quantile(scores, 0.10),
            "sessions": len(runs),
            "failures": sum(r["status"] != "completed" for r in runs),
            "must_have_violations": sum(r.get("result", {}).get("must_haves_ok") is False for r in runs),
            "decision_p95_s": quantile(times, 0.95),
            "message_timeouts": sum(t > 60 for t in times),
            "llm_calls": sum(r["metrics"].get("calls", 0) for r in runs),
            "llm_errors": sum(r["metrics"].get("errors", 0) for r in runs),
            "fallbacks": sum(r["metrics"].get("fallbacks", 0) for r in runs),
            "supplier_calls": sum(r["supplier_metrics"]["calls"] for r in runs),
            "input_tokens": sum(
                r["metrics"].get("input_tokens", 0) + r["supplier_metrics"]["input_tokens"] for r in runs
            ),
            "output_tokens": sum(
                r["metrics"].get("output_tokens", 0) + r["supplier_metrics"]["output_tokens"] for r in runs
            ),
        }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "arena_sessions_used": 0,
        "conditions": {
            "agents": agents,
            "seeds": seeds,
            "case_ids": case_ids,
            "opponents": opponents,
            "repeats": repeats,
            "supplier_mode": supplier_mode,
            "supplier_models": models,
            "buyer_model": buyer_model,
            "writer_instruction_hashes": {
                label: hashlib.sha256((writer_instructions or {}).get(label, "").encode()).hexdigest()
                for label in agents
            },
            "planned_sessions": planned,
        },
        "summary": summary,
        "attempted_usage": {
            label: {
                "sessions": len(runs),
                "buyer_calls": sum(r["metrics"].get("calls", 0) for r in runs),
                "supplier_calls": sum(r["supplier_metrics"]["calls"] for r in runs),
                "input_tokens": sum(
                    r["metrics"].get("input_tokens", 0) + r["supplier_metrics"]["input_tokens"] for r in runs
                ),
                "output_tokens": sum(
                    r["metrics"].get("output_tokens", 0) + r["supplier_metrics"]["output_tokens"] for r in runs
                ),
            }
            for label, runs in all_attempts.items()
        },
        "excluded_pairs": sum(r["excluded"] for r in rows),
        "paired_comparisons": {a: paired_interval(rows, agents[0], a) for a in agents[1:]},
        "rows": rows,
        "limitation": (
            "Local synthetic evaluation. Seeds fix economics, not model outputs. No best-of-attempt selection."
        ),
    }
    (folder / "report.json").write_text(json.dumps(clean(report), indent=2), encoding="utf-8")
    return {**report, "report_path": str(folder / "report.json")}
