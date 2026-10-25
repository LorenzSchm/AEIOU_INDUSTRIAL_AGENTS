"""Matched local prompt/model comparisons. Hosted arena sessions cannot be started here."""

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from statistics import mean

from arena_kit.play import play

from .action_policy import NegotiationBlocked
from .agent import ClaudeBackend, ProviderRefusalError
from .config import ROOT, model_name
from .language_sim import SUPPLIER_SYSTEM, SUPPLIER_VERSION, LanguageSim, SupplierSimulationError
from .negotiation_state import PROTOCOL_VERSION
from .prompt_protocol import PromptLabAgent, digest, prompt_specs
from .research import package
from .runner import finish_run
from .simulation import LocalTable, local_cases
from .store import clean, new_run, save_run

# Small model-driven screen; no scripted supplier path is available.
LANGUAGE_SCREEN = [("case:S01", "relationship"), ("case:T02", "cashflow")]


def lab_play(
    case_id,
    opponent,
    seed,
    spec,
    model,
    supplier_kind="language",
    supplier_model=None,
    pkg=None,
    backend_factory=ClaudeBackend,
):
    if supplier_kind != "language":
        raise ValueError("Only model-driven language suppliers are available")
    pkg = pkg or package()
    run = new_run(case_id, "local", f"prompt-{spec.name}")
    run.update(opponent=f"{supplier_kind}:{opponent}", seed=seed, model=model, prompt=spec.metadata())
    started = time.monotonic()
    buyer_backend = backend_factory(model=model, max_tokens=12000, timeout=120)
    agent = PromptLabAgent(pkg, buyer_backend, spec)
    cases = local_cases()
    supplier_backend = backend_factory(model=supplier_model or model, max_tokens=2200, timeout=90)
    sim = LanguageSim(cases, opponent, seed, pkg.facts, supplier_backend)
    from .public_setups import public_setups

    setup = public_setups().get(case_id)
    run.update(supplier_kind="language", supplier_version=SUPPLIER_VERSION)
    if setup:
        run["public_setup_sha256"] = setup["sha256"]
    save_run(run)
    table = LocalTable(sim, run)
    try:
        result = play(table, case_id, agent)
        finish_run(run, agent, result, started)
    except NegotiationBlocked as exc:
        session = sim.sessions[table.session_id]
        sim._end(session, "protocol_limit", None)
        run["termination"] = {"origin": "protocol", "reason": exc.reason, "buyer_action_sent": False}
        finish_run(run, agent, session["result"], started)
    except Exception as exc:
        logging.getLogger("aeiou").warning("Local prompt comparison failed: %s", type(exc).__name__)
        finish_run(run, agent, {}, started)
        run.update(
            status="failed",
            error=type(exc).__name__ + ": " + str(exc)[:300],
            failure_origin=(
                "supplier"
                if isinstance(exc, SupplierSimulationError)
                else "model_refusal"
                if isinstance(exc, ProviderRefusalError)
                else "buyer_or_transport"
            ),
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
        run["supplier_metrics"].update(
            format_repairs=sim.repairs, constraint_rejections=sim.rejections, version=SUPPLIER_VERSION
        )
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
        "provider_refusals": sum(r["metrics"].get("refusals", 0) for r in runs),
        "output_tokens": sum(r["metrics"]["output_tokens"] for r in runs),
        "format_repairs": sum(r["audit"]["format_repairs"] for r in runs),
        "fallbacks": sum(r["metrics"]["fallbacks"] for r in runs),
        "verbatim_request_runs": sum(r["audit"]["verbatim_request_sent"] for r in runs),
        "verbatim_ignoring_whitespace_runs": sum(
            r["audit"].get("verbatim_request_ignoring_whitespace", False) for r in runs
        ),
        "calibrated_question_messages": sum(r["audit"].get("calibrated_question_messages", 0) for r in runs),
        "confirmation_messages": sum(r["audit"].get("confirmation_messages", 0) for r in runs),
        "walk_away_reasons": {
            code: sum(d.get("walk_away", {}).get("reason_code") == code for r in runs for d in r["decisions"])
            for code in sorted(
                {d["walk_away"]["reason_code"] for r in runs for d in r["decisions"] if d.get("walk_away")}
            )
        },
        "accepts_before_five_repeats": sum(r["audit"]["accepts_before_five_repeats"] for r in runs),
        "decisions_over_60s": sum(d["elapsed_s"] > 60 for r in runs for d in r["decisions"]),
        "mean_runtime_s": mean(r["metrics"]["elapsed_s"] for r in runs) if runs else None,
    }


def compare_prompts(
    model=None,
    supplier="language",
    supplier_model=None,
    seed=71,
    label="prompt-screen",
    parallel=2,
    plan=None,
    backend_factory=ClaudeBackend,
):
    model, supplier_model = model or model_name(), supplier_model or model_name()
    if supplier != "language":
        raise ValueError("Only model-driven language suppliers are available")
    plan = plan if plan is not None else LANGUAGE_SCREEN
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
    for name in (
        "prompt_protocol.py",
        "negotiation_state.py",
        "agent.py",
        "language_sim.py",
        "simulation.py",
        "research.py",
        "optimizer.py",
        "prompt_comparison.py",
    ):
        source = ROOT / "src/aeiou" / name
        if source.is_file():
            (folder / name).write_bytes(source.read_bytes())
    rows = []
    for index, (case_id, opponent) in enumerate(plan):
        rows.append({"case_id": case_id, "opponent": opponent, "seed": seed + index})
    conditions = {
        "scenarios": [dict(row) for row in rows],
        "buyer_model": model,
        "supplier_kind": supplier,
        "supplier_model": supplier_model if supplier == "language" else None,
        "buyer_max_tokens": 12000,
        "timeout_s": 120,
        "clock_enforced": False,
        "protocol_version": PROTOCOL_VERSION,
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
        "scope": "Matched LOCAL comparison of frozen strategy texts and their explicit protocol requirements",
        "limitations": [
            "Synthetic suppliers cannot establish results against Tacto's hosted agents.",
            "Same seed fixes seller economics; model outputs are stochastic, not seed-reproducible.",
            "Small screening sample; no best-of-attempt selection and no statistical winner claim.",
            "Buyer/transport failures count zero. A supplier simulation failure excludes both sides of that pair.",
            "The earlier production harness used automatic closes and fallbacks; this harness does not.",
            "The original requires five quotations of the selected package; the controller enforces this restriction.",
            "Protocol v2 adds literal text insertion, per-package counts, reserved confirmations and live IDs.",
            "This compares executable strategies; reference rules are enforced for the original.",
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
