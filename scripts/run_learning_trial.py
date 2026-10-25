"""One authorized trial from the fixed twelve-session P10/P18 budget."""

import argparse
import fcntl
import hashlib
import json
import math
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from arena_kit.play import play

from aeiou.agent import make_backend
from aeiou.arena_sessions import AdmittedClient, close_owned_session
from aeiou.config import ROOT, data_root, model_settings
from aeiou.custom_model import CustomResponseModel
from aeiou.custom_model_data import digest
from aeiou.eval_suite import error_text, sandbox_client
from aeiou.learning_buyer import LearningBuyer
from aeiou.prompt_protocol import PromptSpec
from aeiou.research import package
from aeiou.runner import RecordingTable, finish_run
from aeiou.store import new_run, save_run


def update_ledger(folder, entry, reserve=False):
    with (folder / "budget.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "trials.json"
        ledger = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "authorized_total": 12,
                "authorized_per_case": 6,
                "target": 72,
                "validation_reserved_per_case": 3,
                "trials": [],
            }
        )
        if reserve:
            if len(ledger["trials"]) >= 12:
                raise ValueError("Authorized total trial budget exhausted")
            current = [r for r in ledger["trials"] if r["case_id"] == entry["case_id"]]
            if len(current) >= 6:
                raise ValueError("Authorized per-case budget exhausted")
            if entry["phase"] == "development":
                if sum(r["phase"] == "development" for r in current) >= 3:
                    raise ValueError("Validation reserve cannot be spent on tuning")
            if entry["trial"] in {r["trial"] for r in ledger["trials"]}:
                raise ValueError("Trial already reserved")
            ledger["trials"].append(entry)
        else:
            next(r for r in ledger["trials"] if r["trial"] == entry["trial"]).update(entry)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, indent=2))
        tmp.replace(path)


def tree_digest(root, pattern):
    return digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob(pattern))
            if p.is_file() and not p.name.startswith(".")
        }
    )


def candidate_fingerprint(spec, model, settings):
    return {
        "model_sha256": model.sha256,
        "system_sha256": spec.metadata()["system_sha256"],
        "policy_sha256": digest(spec.execution_policy),
        "settings": settings,
        "max_tokens": 12000,
        "timeout": 50,
        "buyer_source_sha256": tree_digest(ROOT / "src/aeiou", "*.py"),
        "reference_data_sha256": tree_digest(data_root(), "*"),
    }


def check_frozen(folder, case_id, fingerprint):
    path = folder / "freeze.json"
    if not path.exists():
        raise ValueError("Freeze the candidate before validation")
    expected = json.loads(path.read_text())["candidates"].get(case_id)
    if expected != fingerprint:
        raise ValueError("Candidate changed after freeze; validation cannot start")


def main(args):
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    strategy = Path(args.prompt).read_text()
    if args.keyword_brief:
        brief = json.loads(Path(args.keyword_brief).read_text())
        if brief.get("case_id") != args.case:
            raise ValueError("Keyword evidence belongs to a different case")
        strategy += "\n\nSOURCE-LINKED KEYWORD BRIEF\n" + json.dumps(brief, ensure_ascii=False, indent=2)
    policy = json.loads(Path(args.policy).read_text()).get(args.case) if args.policy else None
    model = CustomResponseModel(args.model)
    spec = PromptSpec(
        "learning-v4",
        strategy,
        revision=args.trial,
        assessment_enabled=True,
        execution_policy=policy,
        response_support_mode=args.mode,
    )
    settings = model_settings(effort="high")
    fingerprint = candidate_fingerprint(spec, model, settings)
    if args.phase == "validation":
        check_frozen(folder, args.case, fingerprint)
    backend = make_backend(**{k: v for k, v in settings.items() if k != "provider"}, max_tokens=12000, timeout=50)
    buyer = LearningBuyer(package(), backend, spec)
    buyer.response_model = model
    client = sandbox_client()
    cases = client.cases("practice")
    case = next(c for c in cases if c["case_id"] == args.case)
    buyer.prepare(case)
    run = new_run(args.case, "hosted", "learning-v4")
    run.update(
        case_set="practice",
        evaluation_id="p10-p18-target72-v4",
        **settings,
        prompt=spec.metadata(),
        learned_model_sha256=model.sha256,
        trial=args.trial,
    )
    entry = {
        "trial": args.trial,
        "case_id": args.case,
        "phase": args.phase,
        "mode": args.mode,
        "status": "reserved",
        "run_id": run["id"],
        "model_sha256": model.sha256,
        "prompt": spec.metadata(),
        "candidate_fingerprint": fingerprint,
        "created_at": datetime.now(UTC).isoformat(),
    }
    update_ledger(folder, entry, reserve=True)
    (folder / (args.trial + ".prompt.txt")).write_text(strategy)
    save_run(run)
    started = time.monotonic()
    table = RecordingTable(AdmittedClient(client, lambda: None, lambda: None, threading.Event()), run)
    try:
        result = play(table, args.case, buyer)
        score = result.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("Missing official result; do not substitute local scoring")
        finish_run(run, buyer, result, started)
        entry.update(
            status="completed",
            score=score * 100,
            deal=result.get("deal"),
            must_haves_ok=result.get("must_haves_ok"),
            terms=result.get("terms"),
        )
    except Exception as exc:
        finish_run(run, buyer, {}, started)
        run.update(status="failed", error=error_text(exc))
        close_owned_session(client, run)
        entry.update(status="failed", score=None, error=run["error"])
        cleanup_result = run.get("remote_cleanup", {}).get("result") or {}
        score = cleanup_result.get("score")
        if (
            cleanup_result.get("ended")
            and isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
        ):
            entry.update(
                score=score * 100,
                deal=cleanup_result.get("deal"),
                must_haves_ok=cleanup_result.get("must_haves_ok"),
                outcome_source="official_cleanup",
            )
    finally:
        save_run(run)
        entry.update(session_id=run.get("session_id"), elapsed_s=round(time.monotonic() - started, 2))
        update_ledger(folder, entry)
        (folder / (args.trial + ".run.json")).write_text(json.dumps(run, indent=2))
        backend.client.close()
        client.http.close()
    print(json.dumps(entry, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["case:P10", "case:P18"], required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--phase", choices=["development", "validation", "diagnostic"], default="development")
    parser.add_argument("--mode", choices=["shadow", "advisory"], required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--policy")
    parser.add_argument("--keyword-brief", help="Optional scoped source-linked commercial keyword brief")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default="reports/p10-p18-target72-v4")
    main(parser.parse_args())
