"""Bounded evaluation of the current user prompt against model-driven suppliers."""

import hashlib
import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime

from .agent import make_backend
from .config import ROOT, model_name
from .language_sim import SUPPLIER_SYSTEM, SUPPLIER_VERSION
from .prompt_comparison import lab_play, summarize
from .prompt_protocol import digest, prompt_specs
from .public_setups import public_setups
from .research import package
from .simulation import OPPONENTS
from .store import clean
from .supplier_profiles import case_profile


def evaluation_plan(case_ids=None, profiles=None, repeats=3, seed_start=1101):
    """Matched seeds across profiles; each case/profile has three independent repetitions."""
    cases = sorted(case_ids if case_ids is not None else public_setups())
    setups = public_setups()
    case_index = {c: i for i, c in enumerate(sorted(setups))}
    selected = [(c, case_profile(setups[c]["case"])) for c in cases]
    if profiles is not None:
        selected = [(c, p) for c, p in selected if p in profiles]
    if repeats != 3 or not selected or any(p not in OPPONENTS for _, p in selected):
        raise ValueError("Select known supplier profiles and three repeats per case")
    return [(c, p, seed_start + case_index[c] * repeats + repeat) for repeat in range(repeats) for c, p in selected]


def evaluate_local(
    label="local-three-repeats",
    seed_start=1101,
    parallel=2,
    plan=None,
    existing=None,
    backend_factory=make_backend,
    prior_attempts=0,
    attempt_limit=20,
    variant="original",
):
    if plan is None:
        plan = evaluation_plan(seed_start=seed_start)
        attempt_limit = len(plan) + prior_attempts
    if not 1 <= attempt_limit <= 540 or not 1 <= len(plan) or prior_attempts + len(plan) > attempt_limit:
        raise ValueError(f"At most {attempt_limit} total attempts, including prior attempts")
    setups = public_setups()
    if any(c not in setups for c, _, _ in plan):
        raise ValueError("Every evaluation case requires a recorded public opening")
    spec, model, pkg = prompt_specs()[variant], model_name(), package()
    folder = (
        ROOT
        / "reports/local-evaluations"
        / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            + "-"
            + "".join(c for c in label if c.isalnum() or c in "-_")[:60]
        )
    )
    folder.mkdir(parents=True)
    existing = list(existing or [])
    for run in existing:
        if (
            run.get("agent") != "prompt-original"
            or run.get("prompt") != spec.metadata()
            or run["status"] != "completed"
        ):
            raise ValueError("Only completed runs of the identical current prompt may be retained")
    rows = [
        {"case_id": r["case_id"], "opponent": r["opponent"], "seed": r["seed"], "run": r, "retained": True}
        for r in existing
    ]
    rows += [{"case_id": c, "opponent": o, "seed": seed} for c, o, seed in plan]
    report = {
        "label": label,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "arena_sessions_used": 0,
        "prior_attempts": prior_attempts,
        "new_attempt_limit": len(plan),
        "prompt": spec.metadata(),
        "rows": rows,
        "conditions": {
            "buyer_model": model,
            "supplier_model": model,
            "supplier_version": SUPPLIER_VERSION,
            "clock_enforced": False,
            "supplier_prompt_sha256": digest(SUPPLIER_SYSTEM),
            "public_setup_sha256": {c: setups[c]["sha256"] for c in {r["case_id"] for r in rows}},
        },
        "limitations": [
            "Public cases and opening offers are recorded; subsequent seller responses are synthetic.",
            "Seller personas, price catalog and reservation prices are assumptions, not recovered hosted policies.",
            "Normalized scores use synthetic floors and cannot be compared directly with hosted scores.",
            "Model outputs are stochastic. These familiar cases are exploratory, not an unseen holdout.",
            "Supplier simulation failures are excluded; buyer failures score zero. No automatic reruns.",
        ],
    }
    hashes = {}
    for name in (
        "local_evaluation.py",
        "supplier_profiles.py",
        "action_policy.py",
        "runner.py",
        "language_sim.py",
        "simulation.py",
        "public_setups.py",
        "prompt_comparison.py",
        "prompt_protocol.py",
        "negotiation_state.py",
        "agent.py",
        "optimizer.py",
        "research.py",
    ):
        source = ROOT / "src/aeiou" / name
        if source.exists():
            data = source.read_bytes()
            (folder / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
    report["source_sha256"] = hashes
    (folder / f"{variant}.system.txt").write_text(spec.system)
    (folder / "public-setups.json").write_text(
        json.dumps({c: setups[c] for c in report["conditions"]["public_setup_sha256"]}, indent=2)
    )
    path = folder / "report.json"
    report["report_path"] = str(path)

    def save():
        done = [r["run"] for r in rows if "run" in r]
        scored = [r for r in done if r.get("failure_origin") != "supplier"]
        report["summary"] = summarize(done, scored)
        report["summary"]["supplier_failures"] = len(done) - len(scored)
        report["summary"]["supplier_calls"] = sum(r.get("supplier_metrics", {}).get("calls", 0) for r in done)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(clean(report), indent=2))
        temp.replace(path)

    save()
    # Start one canary, then at most `parallel` active sessions. Never enqueue the whole paid batch.
    pending = iter(enumerate(plan))
    stop = None
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 4))) as pool:
        tasks = {}
        exhausted = False
        canary_passed = False
        while tasks or not exhausted:
            capacity = max(1, min(parallel, 4)) if canary_passed else 1
            while not stop and not exhausted and len(tasks) < capacity:
                item = next(pending, None)
                if item is None:
                    exhausted = True
                    break
                i, (c, o, seed) = item
                tasks[pool.submit(lab_play, c, o, seed, spec, model, "language", model, pkg, backend_factory)] = (
                    i + len(existing)
                )
            if not tasks:
                break
            ready, _ = wait(tasks, return_when=FIRST_COMPLETED)
            for task in ready:
                index = tasks.pop(task)
                try:
                    run = task.result()
                except Exception as exc:
                    rows[index]["error"] = type(exc).__name__
                    stop = "Evaluation setup failed; remaining attempts were not started"
                    continue
                rows[index]["run"] = run
                if run["status"] == "failed":
                    stop = (
                        "Execution failure; remaining attempts were not started. "
                        "Inspect the failed run before retrying."
                    )
                elif not stop:
                    canary_passed = True
            save()
            if stop:
                exhausted = True
    for row in rows:
        if "run" not in row:
            row["status"] = "not_started"
    report.update(
        status="stopped" if stop else "completed", stop_reason=stop, completed_at=datetime.now(UTC).isoformat()
    )
    save()
    return report
