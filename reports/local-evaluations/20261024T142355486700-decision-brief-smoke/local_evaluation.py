"""Bounded evaluation of the current user prompt against model-driven suppliers."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from .agent import ClaudeBackend
from .config import ROOT, model_name
from .language_sim import SUPPLIER_SYSTEM, SUPPLIER_VERSION
from .prompt_comparison import lab_play, summarize
from .prompt_protocol import digest, prompt_specs
from .public_setups import public_setups
from .research import package
from .store import clean

SCENARIOS = [
    ("case:P01", "relationship"),
    ("case:P02", "cashflow"),
    ("case:P03", "hardliner"),
    ("case:P04", "index_skeptic"),
    ("case:P05", "adversarial"),
    ("case:P07", "relationship"),
    ("case:P08", "cashflow"),
    ("case:P10", "hardliner"),
    ("case:P11", "index_skeptic"),
    ("case:P18", "adversarial"),
]


def evaluate_local(
    label="local-20",
    seed_start=501,
    parallel=2,
    plan=None,
    existing=None,
    backend_factory=ClaudeBackend,
    prior_attempts=0,
):
    plan = plan if plan is not None else [(c, o, seed_start + i) for i, (c, o) in enumerate(SCENARIOS * 2)]
    if not 1 <= len(plan) <= 20 or prior_attempts + len(plan) > 20:
        raise ValueError("At most 20 total attempts, including prior attempts")
    setups = public_setups()
    if any(c not in setups for c, _, _ in plan):
        raise ValueError("Every evaluation case requires a recorded public opening")
    spec, model, pkg = prompt_specs()["original"], model_name(), package()
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
    (folder / "original.system.txt").write_text(spec.system)
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
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 4))) as pool:
        tasks = {
            pool.submit(lab_play, c, o, seed, spec, model, "language", model, pkg, backend_factory): i + len(existing)
            for i, (c, o, seed) in enumerate(plan)
        }
        for task in as_completed(tasks):
            rows[tasks[task]]["run"] = task.result()
            save()
    report.update(status="completed", completed_at=datetime.now(UTC).isoformat())
    save()
    return report
