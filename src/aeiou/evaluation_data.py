"""Evaluation views over persisted attempts, without mixing execution revisions."""

from collections import defaultdict
from statistics import mean, median, pstdev

from .config import model_name
from .language_sim import SUPPLIER_VERSION
from .prompt_protocol import prompt_specs
from .public_setups import public_setups
from .store import list_runs
from .supplier_profiles import profile_cases


def stats(runs):
    completed = [r for r in runs if r["status"] == "completed" and r.get("result", {}).get("score") is not None]
    scores = [r["result"]["score"] for r in completed]
    failed = [r for r in runs if r["status"] == "failed"]
    supplier_failures = sum(r.get("failure_origin") == "supplier" for r in failed)
    eligible = len(completed) + len(failed) - supplier_failures
    return {
        "attempts": len(runs),
        "completed": len(completed),
        "failures": len(failed),
        "supplier_failures": supplier_failures,
        "cancelled": sum(r["status"] == "cancelled" for r in runs),
        "running": sum(r["status"] == "running" for r in runs),
        "deals": sum(bool(r.get("result", {}).get("deal")) for r in completed),
        "mean_score": mean(scores) if scores else None,
        "median_score": median(scores) if scores else None,
        "score_stddev": pstdev(scores) if scores else None,
        "best_score": max(scores) if scores else None,
        "refusals": sum(r.get("metrics", {}).get("refusals", 0) for r in runs),
        # Keep execution reliability visible without inventing a negotiation outcome for errors.
        "reliability_score": sum(scores) / eligible if eligible else None,
    }


def local_dashboard():
    attempts = [r for r in list_runs(limit=10000) if r["mode"] == "local"]
    current = prompt_specs()["original"].metadata()["system_sha256"]
    groups = defaultdict(list)
    for run in attempts:
        prompt = run.get("prompt", {})
        key = "|".join(
            [
                run["agent"],
                run.get("model", "unknown"),
                prompt.get("system_sha256", "unrecorded"),
                run.get("supplier_version", "unrecorded"),
                run.get("supplier_model", "unrecorded"),
            ]
        )
        groups[key].append(run)
    profiles = profile_cases(public_setups())
    versions = []
    for key, runs in groups.items():
        first = runs[0]
        profile_rows = []
        for profile in profiles:
            subset = [r for r in runs if r.get("opponent", "").removeprefix("language:") == profile["id"]]
            profile_rows.append(
                {
                    **profile,
                    "summary": stats(subset),
                    "cases": [
                        {"case_id": cid, "required": 3, **stats([r for r in subset if r["case_id"] == cid])}
                        for cid in profile["case_ids"]
                    ],
                }
            )
        versions.append(
            {
                "id": key,
                "agent": first["agent"],
                "model": first.get("model"),
                "prompt": first.get("prompt", {}),
                "supplier_version": first.get("supplier_version"),
                "supplier_model": first.get("supplier_model"),
                "current": first.get("prompt", {}).get("system_sha256") == current
                and first.get("supplier_version") == SUPPLIER_VERSION
                and first.get("model") == model_name()
                and not first.get("history"),
                "summary": stats(runs),
                "profiles": profile_rows,
                "run_ids": [r["id"] for r in runs],
            }
        )
    return {
        "attempts": attempts,
        "summary": stats(attempts),
        "versions": versions,
        "profiles": profiles,
        "cases": len(public_setups()),
        "repeats": 3,
        "planned_runs": len(public_setups()) * 3,
        "grouping_note": (
            "Ownership from the public supplier dataset. These are company groups, not official agent personalities."
        ),
        "scoring_note": (
            "Outcome scores use completed runs only. Reliability score counts failed executions as "
            "zero; supplier simulation failures and cancelled runs are excluded. "
            "Compare identical model, prompt and simulator versions."
        ),
    }
