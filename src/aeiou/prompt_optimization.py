"""Bounded offline writer optimization with disjoint train/validation/test cases.

Reflection proposes instruction text, never executable code. Only training failures
reach the reflector. Validation selects a candidate and the held-out test runs once.
"""

import hashlib
import json
import uuid
from pathlib import Path

from .agent import ClaudeBackend
from .config import state_root
from .evaluation import evaluate_matrix
from .simulation import local_cases

REFLECTION_SYSTEM = """Improve the communication instructions of an industrial negotiation writer.
Return one concise instruction string. Diagnose observed training failures. The economic
controller owns all actions, supplier selection and contract terms. You may improve diagnostic
questions, tone and evidence selection only. Preserve privacy and use current shareable cards.
Do not include executable code or ask to change budgets, sources, validation or protected rules.
"""


def load_writer_instruction(path):
    """Load a deliberately selected optimization result, never auto-promote it."""
    winner = json.loads(Path(path).read_text(encoding="utf-8"))["winner"]
    instruction = winner["instruction"]
    if not isinstance(instruction, str) or len(instruction) > 3000:
        raise ValueError("Writer instruction must be at most 3000 characters")
    if hashlib.sha256(instruction.encode()).hexdigest() != winner["sha256"]:
        raise ValueError("Writer instruction hash does not match the selected result")
    return instruction


def validate_split(train, validation, test):
    groups = [set(train), set(validation), set(test)]
    if any(not g for g in groups) or any(groups[i] & groups[j] for i in range(3) for j in range(i)):
        raise ValueError("Train, validation and test must be nonempty and disjoint by base case")
    if set.union(*groups) - set(local_cases()):
        raise ValueError("Unknown split case")


def optimize_writer(
    train=("case:S01", "case:S02", "case:T01"),
    validation=("case:S03",),
    test=("case:T02",),
    rounds=2,
    seed=71,
    agent="strategic",
    supplier_mode="scripted",
    opponents=("hardliner",),
    allow_api=False,
    max_sessions=40,
    output=None,
    backend_factory=ClaudeBackend,
    reflector=None,
    supplier_backend_factory=ClaudeBackend,
):
    validate_split(train, validation, test)
    if agent not in {"strategic", "strategic-policy"} or not 0 <= rounds <= 5:
        raise ValueError("Use a strategic agent and 0–5 bounded reflection rounds")
    required = (rounds + 1) * (len(train) + len(validation)) * len(opponents) + len(test) * len(opponents)
    if required > max_sessions:
        raise ValueError(f"Optimization needs {required} sessions; explicit budget is {max_sessions}")
    if (agent == "strategic" or supplier_mode == "language" or rounds) and not allow_api:
        raise ValueError("Optimization with models or reflection requires explicit --allow-api")
    folder = Path(output) if output else state_root() / "prompt-optimization" / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    if rounds and reflector is None:
        reflector = backend_factory()
    candidates, instruction = [], ""
    for trial in range(rounds + 1):
        common = dict(
            agents=[agent],
            seeds=[seed],
            opponents=opponents,
            supplier_mode=supplier_mode,
            allow_api=allow_api,
            max_sessions=max_sessions,
            backend_factory=backend_factory,
            supplier_backend_factory=supplier_backend_factory,
            writer_instructions={agent: instruction},
        )
        training = evaluate_matrix(case_ids=train, output=folder / f"trial-{trial}" / "train", **common)
        selection = evaluate_matrix(case_ids=validation, output=folder / f"trial-{trial}" / "validation", **common)
        candidate = {
            "trial": trial,
            "instruction": instruction,
            "sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "training_score": training["summary"][agent]["mean_score"],
            "validation_score": selection["summary"][agent]["mean_score"],
            "training_report": training["report_path"],
            "validation_report": selection["report_path"],
        }
        candidates.append(candidate)
        (folder / "candidates.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
        if trial < rounds:
            # Summarize only train traces. Never pass validation/test outcomes to reflection.
            from .store import get_run

            feedback = []
            for row in training["rows"]:
                run = get_run(row["run_ids"][agent])
                feedback.append(
                    {
                        "case_id": row["case_id"],
                        "score": row["scores"][agent],
                        "status": run["status"],
                        "decisions": [
                            {k: d.get(k) for k in ("action", "source", "guard_rejections")} for d in run["decisions"]
                        ],
                        "public_emails": [
                            m.get("body", "")[:1200]
                            for events in run.get("threads", {}).values()
                            for m in events
                            if m.get("from") == "buyer"
                        ][-4:],
                    }
                )
            instruction = reflector.complete(
                REFLECTION_SYSTEM, json.dumps({"current_instruction": instruction, "training_feedback": feedback})
            )
            if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 3000:
                raise ValueError("Reflection must produce a nonempty instruction of at most 3000 characters")
    # Ties keep the baseline / shorter instruction; no claim of improvement from a tie.
    winner = max(
        candidates,
        key=lambda c: (c["validation_score"] if c["validation_score"] is not None else -1, -len(c["instruction"])),
    )
    heldout = evaluate_matrix(
        agents=[agent],
        seeds=[seed + 1],
        case_ids=test,
        opponents=opponents,
        supplier_mode=supplier_mode,
        allow_api=allow_api,
        max_sessions=max_sessions,
        output=folder / "heldout-once",
        backend_factory=backend_factory,
        supplier_backend_factory=supplier_backend_factory,
        writer_instructions={agent: winner["instruction"]},
    )
    report = {
        "winner": winner,
        "candidates": candidates,
        "heldout_report": heldout["report_path"],
        "heldout_summary": heldout["summary"],
        "split": {"train": list(train), "validation": list(validation), "test": list(test)},
        "planned_sessions": required,
        "reflection_calls": rounds,
        "arena_sessions_used": 0,
        "limitation": (
            "Offline prompt search, not GEPA reproduction. A small case split is not evidence of broad generalization."
        ),
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {**report, "report_path": str(folder / "report.json")}
