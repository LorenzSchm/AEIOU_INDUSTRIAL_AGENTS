"""Matched local prompt/model comparisons. Hosted arena sessions cannot be started here."""

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from statistics import mean

from arena_kit.play import play
from arena_kit.sim import Sim

from .agent import ClaudeBackend
from .config import ROOT, model_name
from .language_sim import SUPPLIER_SYSTEM, LanguageSim, SupplierSimulationError
from .prompt_protocol import PromptLabAgent, digest, prompt_specs
from .research import package
from .runner import finish_run
from .simulation import LocalTable, StressSim, local_cases
from .store import clean, new_run, save_run

# Frozen screening plan includes the two losses previously observed and diverse seller objectives.
SCREEN = [
    ("case:S03", "scripted"),
    ("case:T02", "scripted"),
    ("case:S01", "hardliner"),
    ("case:S03", "cashflow"),
    ("case:S01", "relationship"),
    ("case:S02", "index_skeptic"),
]
LANGUAGE_SCREEN = [("case:S01", "relationship"), ("case:T02", "cashflow")]


def lab_play(
    case_id,
    opponent,
    seed,
    spec,
    model,
    supplier_kind="scripted",
    supplier_model=None,
    pkg=None,
    backend_factory=ClaudeBackend,
):
    pkg = pkg or package()
    run = new_run(case_id, "local", f"prompt-{spec.name}")
    run.update(opponent=f"{supplier_kind}:{opponent}", seed=seed, model=model, prompt=spec.metadata())
    started = time.monotonic()
    buyer_backend = backend_factory(model=model, max_tokens=12000, timeout=120)
    agent = PromptLabAgent(pkg, buyer_backend, spec)
    cases = local_cases()
    if supplier_kind == "language":
        supplier_backend = backend_factory(model=supplier_model, max_tokens=2200, timeout=90)
        sim = LanguageSim(cases, opponent, seed, pkg.facts, supplier_backend)
    elif supplier_kind == "scripted":
        sim = (
            Sim(cases, pkg.facts, no_clock=True)
            if opponent == "scripted"
            else StressSim(cases, opponent, seed, pkg.facts)
        )
    else:
        raise ValueError("Supplier kind must be scripted or language; hosted evaluation is unavailable here")
    table = LocalTable(sim, run)
    try:
        result = play(table, case_id, agent)
        finish_run(run, agent, result, started)
    except Exception as exc:
        logging.getLogger("aeiou").warning("Local prompt comparison failed: %s", type(exc).__name__)
        finish_run(run, agent, {}, started)
        run.update(
            status="failed",
            error=type(exc).__name__ + ": " + str(exc)[:300],
            failure_origin="supplier" if isinstance(exc, SupplierSimulationError) else "buyer_or_transport",
        )
    if table.session_id:
        session = sim.sessions[table.session_id]
        run["threads"] = {sid: t["messages"] for sid, t in session["threads"].items()}
        run["end_reason"] = session.get("end_reason")
    run["audit"] = {**agent.audit, "format_repairs": agent.repairs}
    if supplier_kind == "language":
        run["supplier_metrics"] = {
            k: getattr(sim.backend, k, 0) for k in ("calls", "errors", "input_tokens", "output_tokens")
        }
        run["supplier_metrics"].update(format_repairs=sim.repairs, constraint_rejections=sim.rejections)
    save_run(run)
    return run


def summarize(runs, scored=None):
    completed = [r for r in runs if r["status"] == "completed"]
    scored = runs if scored is None else scored
    # Buyer/transport failures count zero; failed supplier simulations invalidate the whole pair.
    return {
        "runs": len(runs),
        "completed": len(completed),
        "failures": len(runs) - len(completed),
        "scored_runs": len(scored),
        "mean_score": mean(r.get("result", {}).get("score", 0) for r in scored) if scored else None,
        "completed_mean_score": mean(r["result"]["score"] for r in completed) if completed else None,
        "deals": sum(r.get("result", {}).get("deal", False) for r in scored),
        "must_have_violations": sum(
            r.get("result", {}).get("deal", False) and r["result"].get("must_haves_ok") is False for r in runs
        ),
        "calls": sum(r["metrics"]["calls"] for r in runs),
        "provider_errors": sum(r["metrics"]["errors"] for r in runs),
        "output_tokens": sum(r["metrics"]["output_tokens"] for r in runs),
        "format_repairs": sum(r["audit"]["format_repairs"] for r in runs),
        "fallbacks": sum(r["metrics"]["fallbacks"] for r in runs),
        "verbatim_request_runs": sum(r["audit"]["verbatim_request_sent"] for r in runs),
        "accepts_before_five_repeats": sum(r["audit"]["accepts_before_five_repeats"] for r in runs),
        "decisions_over_60s": sum(d["elapsed_s"] > 60 for r in runs for d in r["decisions"]),
        "mean_runtime_s": mean(r["metrics"]["elapsed_s"] for r in runs),
    }


def compare_prompts(
    model=None,
    supplier="scripted",
    supplier_model=None,
    seed=71,
    label="prompt-screen",
    parallel=2,
    plan=None,
    backend_factory=ClaudeBackend,
):
    model, supplier_model = model or model_name(), supplier_model or model_name()
    plan = plan if plan is not None else (LANGUAGE_SCREEN if supplier == "language" else SCREEN)
    if not plan or len(plan) > 30:
        raise ValueError("Select between 1 and 30 matched scenarios")
    specs, pkg = prompt_specs(), package()
    folder = (
        ROOT
        / "reports"
        / "prompt-comparisons"
        / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            + "-"
            + "".join(c for c in label if c.isalnum() or c in "-_")[:60]
        )
    )
    folder.mkdir(parents=True)
    for name, spec in specs.items():
        (folder / f"{name}.strategy.txt").write_text(spec.strategy)
        (folder / f"{name}.system.txt").write_text(spec.system)
    for name in ("prompt_protocol.py", "agent.py", "language_sim.py", "simulation.py", "research.py", "optimizer.py"):
        source = ROOT / "src/aeiou" / name
        if source.is_file():
            (folder / name).write_bytes(source.read_bytes())
    rows = []
    for index, (case_id, opponent) in enumerate(plan):
        rows.append({"case_id": case_id, "opponent": opponent, "seed": seed + index})
    conditions = {
        "scenarios": rows,
        "buyer_model": model,
        "supplier_kind": supplier,
        "supplier_model": supplier_model if supplier == "language" else None,
        "buyer_max_tokens": 12000,
        "timeout_s": 120,
        "clock_enforced": False,
    }
    conditions["sha256"] = digest(json.dumps(conditions, sort_keys=True))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "label": label,
        "status": "running",
        "arena_sessions_used": 0,
        "conditions": conditions,
        "prompts": {n: s.metadata() for n, s in specs.items()},
        "harness_sha256": hashlib.sha256((ROOT / "src/aeiou/prompt_protocol.py").read_bytes()).hexdigest(),
        "supplier_prompt_sha256": digest(SUPPLIER_SYSTEM) if supplier == "language" else None,
        "scope": "Matched LOCAL prompt comparison under one shared transport harness",
        "limitations": [
            "Synthetic suppliers cannot establish results against Tacto's hosted agents.",
            "Same seed fixes seller economics; model outputs are stochastic, not seed-reproducible.",
            "Small screening sample; no best-of-attempt selection and no statistical winner claim.",
            "Buyer/transport failures count zero. A supplier simulation failure excludes both sides of that pair.",
            "The earlier production harness used automatic closes and fallbacks; this harness does not.",
            "The original third-offer and five-repeat acceptance instructions are both preserved; they can conflict.",
        ],
        "rows": rows,
    }
    path = folder / "report.json"
    path.write_text(json.dumps(clean(report), indent=2))
    tasks = [(i, name) for i in range(len(rows)) for name in ("original", "adapted")]

    def one(task):
        i, name = task
        row = rows[i]
        return (
            i,
            name,
            lab_play(
                row["case_id"],
                row["opponent"],
                row["seed"],
                specs[name],
                model,
                supplier,
                supplier_model,
                pkg,
                backend_factory,
            ),
        )

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 4))) as pool:
        for i, name, run in pool.map(one, tasks):
            rows[i][name] = {
                "run_id": run["id"],
                "status": run["status"],
                "result": run.get("result"),
                "metrics": run["metrics"],
                "audit": run["audit"],
                "decisions": run["decisions"],
                "supplier_metrics": run.get("supplier_metrics"),
                "error": run.get("error"),
                "failure_origin": run.get("failure_origin"),
            }
            path.write_text(json.dumps(clean(report), indent=2))
    report.update(status="completed")
    assess_report(report)
    report["report_path"] = str(path)
    path.write_text(json.dumps(clean(report), indent=2))
    return report


def assess_report(report):
    """Derive paired scores without attributing a broken opponent to the buyer strategy."""
    rows = report["rows"]
    eligible = [r for r in rows if not any(r[n].get("failure_origin") == "supplier" for n in ("original", "adapted"))]
    report["excluded_pairs"] = len(rows) - len(eligible)
    report["summary"] = {n: summarize([r[n] for r in rows], [r[n] for r in eligible]) for n in ("original", "adapted")}
    report["complete_pairs"] = sum(all(r[n]["status"] == "completed" for n in ("original", "adapted")) for r in rows)
