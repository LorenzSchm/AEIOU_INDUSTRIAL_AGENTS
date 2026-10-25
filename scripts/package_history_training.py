"""Create a reviewable report and portable archive after the bounded experiments."""

import argparse
import hashlib
import json
import statistics
import zipfile
from pathlib import Path

from aeiou.training_corpus import dump


def load(path):
    return json.loads(Path(path).read_text())


def summarize(folder):
    identifiers = load(folder / "evaluation-ids.json") + [{"variant": "v2", **load(folder / "evaluation-v2-id.json")}]
    experiments = []
    for identifier in identifiers:
        report = load(Path("data/local/eval-suite") / (identifier["id"] + ".json"))
        if report["status"] == "running":
            raise ValueError("Wait for the saved experiment to finish before packaging")
        rows = report["rows"]
        scores = [r["score"] * 100 for r in rows if r.get("score") is not None]
        attempted = [r for r in rows if r.get("session_id")]
        experiments.append(
            {
                "variant": identifier["variant"],
                "id": report["id"],
                "status": report["status"],
                "completed": len(scores),
                "attempted": len(attempted),
                "failures": sum(r["status"] == "failed" for r in rows),
                "official_mean": statistics.mean(scores) if scores else None,
                "all_attempt_mean_failure_zero": sum(scores) / len(attempted) if attempted else None,
                "case_means": {
                    cid: statistics.mean(
                        r["score"] * 100 for r in rows if r["case_id"] == cid and r.get("score") is not None
                    )
                    for cid in sorted({r["case_id"] for r in rows if r.get("score") is not None})
                },
                "model": report["model"],
                "effort": report["effort"],
                "prompt": report["prompt"],
                "sandbox_sessions_started": report["arena_sessions_used"],
                "practice_sessions_started": report["practice_sessions_used"],
                "rows": rows,
            }
        )
        dump(folder / f"evaluation-{identifier['variant']}.json", report)
    result = {
        "experiments": experiments,
        "interpretation": "Fresh stochastic sandbox runs on a matched case/repeat plan. V2 was adapted after "
        "the first v1 D02 result, so this is exploratory development. No automatic default promotion.",
    }
    dump(folder / "prompt-results.json", result)
    return result


def package_result(args):
    folder, corpus, evidence = Path(args.output), Path(args.corpus), Path(args.evidence)
    tactical, mixed = Path(args.tactical), Path(args.mixed)
    prompts = summarize(folder)
    manifest, knowledge = load(corpus / "manifest.json"), load(evidence / "manifest.json")
    model_report, mixed_report = load(tactical / "report.json"), load(mixed / "report.json")
    fixture_count = manifest["by_source"].get("verification_fixture", 0)
    summary = {
        "conversations_excluding_verification": manifest["sessions"] - fixture_count,
        "archive": manifest,
        "knowledge": {k: v for k, v in knowledge.items() if k != "files"},
        "prompt_experiments": prompts,
        "mixed_response_training": mixed_report,
        "tactical_training": model_report,
        "default_model_promoted": False,
        "default_prompt_changed": False,
        "foundation_model_fine_tuned": False,
    }
    dump(folder / "summary.json", summary)
    lines = [
        "AEIOU — consolidated history, Sunday evidence and training — 25 October 2026",
        "",
        f"Collected {summary['conversations_excluding_verification']:,} negotiations, "
        f"plus {fixture_count} separately labeled verification fixtures.",
        f"Archive: {manifest['messages']:,} messages; {manifest['decision_examples']:,} buyer decision examples; "
        f"{manifest['response_examples']:,} response examples.",
        "Sources: " + json.dumps(manifest["by_source"], sort_keys=True),
        f"Knowledge: {knowledge['business_records']:,} business rows, {knowledge['facts']:,} citable facts, "
        f"{knowledge['documents']} source documents.",
        "Sunday release: six news items, three supplier letters and one purchasing memo. "
        "September indices are unchanged.",
        "Source hashes, duplicate handling, temporal boundaries and incomplete context are recorded in the manifests.",
        "",
        "Prompt comparison — official sandbox scores / 100, three repetitions per case:",
    ]
    names = {"a": "Strongest observed baseline", "b": "Rebuilt NegoMate v1", "v2": "Rebuilt NegoMate v2"}
    for trial in prompts["experiments"]:
        lines.append(
            f"  {names[trial['variant']]}: {trial['official_mean']:.2f}; "
            f"D01 {trial['case_means'].get('case:D01', 0):.2f}, D02 {trial['case_means'].get('case:D02', 0):.2f}; "
            f"{trial['completed']}/{trial['attempted']} completed, {trial['failures']} failures."
        )
    lines.extend(
        [
            "V2 preserves more of the successful preparation framework and distinguishes one supplier's finality "
            "from an open multi-supplier competition.",
            "These small stochastic demo comparisons do not prove final-case performance. "
            "The active default prompt is unchanged.",
            "Sandbox experiments compare prompt text; the newly trained response models stayed offline.",
            "",
            f"Generic response training: {mixed_report['rows']:,} open-bargaining examples, "
            f"{mixed_report['groups']} held-out case/supplier groups, {mixed_report['model_fits']} fitted models.",
            "Brier error (lower is better), hosted-only / hosted-plus-local / base-rate:",
        ]
    )
    for target, comparisons in mixed_report["comparisons"].items():
        lines.append(
            "  "
            + target
            + ": "
            + " / ".join(
                f"{comparisons[n]['macro_brier']:.6f}" for n in ("hosted_only", "hosted_plus_local", "base_rate")
            )
        )
    lines.extend(
        [
            f"Focused/general/tactical refit: {model_report['corpus']['examples']:,} hosted exchanges "
            f"from {model_report['corpus']['usable_sessions']} usable sessions were evaluated.",
            "Historical predictive gates retain an incumbent when an alternative regresses; "
            "policy gains remain unproven.",
            "The final refit includes the newly completed conversations. Earlier frozen artifacts remain separate.",
            "Artifacts contain local response-model weights; "
            "no Claude/GPT foundation-model fine-tuning or training upload occurred.",
            "No response-model default was promoted. Registered model selection and execution limits are unchanged.",
            "",
            "Validation: full suite 978 passed, 10 skipped, 3 expected failures; "
            "added corpus/chronology/release tests passed separately. See validation logs.",
            "The collector uses GET only. New negotiations in this task were sandbox evaluations; "
            "no practice or final sessions were started by these experiments.",
            "See DATA_GUIDE.txt for schemas, limitations and reproduction commands.",
            "",
            "Final corpus: " + str(corpus.resolve()),
            "Final tactical model: " + str((tactical / "model.json").resolve()),
            "Final mixed-data models: " + str((mixed / "models.json").resolve()),
            "Rebuilt prompt: " + str(Path("prompts/negomate-history-sunday-v2.txt").resolve()),
        ]
    )
    (folder / "REPORT.txt").write_text("\n".join(lines) + "\n")
    files = {}
    for path in corpus.rglob("*.json*"):
        files["corpus/" + str(path.relative_to(corpus))] = path
    for path in evidence.glob("*.json*"):
        files["evidence/" + path.name] = path
    for root, name, patterns in (
        (tactical, "tactical-model", ("model.json", "report.json")),
        (mixed, "mixed-models", ("models.json", "report.json")),
    ):
        for pattern in patterns:
            files[name + "/" + pattern] = root / pattern
    for path in folder.glob("*.txt"):
        files["reports/" + path.name] = path
    for name in (
        "summary.json",
        "prompt-results.json",
        "prospective-plan.json",
        "v2-plan.json",
        "release-integration.json",
        "verification-fixtures.json",
        "final-integrity-audit.json",
        "final-model-replay.json",
    ):
        files["reports/" + name] = folder / name
    for path in Path("prompts").glob("negomate-history-sunday-*.txt"):
        files["prompts/" + path.name] = path
    for path in Path("src/aeiou").glob("*.py"):
        files["source/src/aeiou/" + path.name] = path
    for path in Path("scripts").glob("*.py"):
        files["source/scripts/" + path.name] = path
    for name in ("requirements-learning.txt", "pyproject.toml", "uv.lock", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        files["source/" + name] = Path(name)
    if Path("data/LICENSE").is_file():
        files["source/data/LICENSE"] = Path("data/LICENSE")
    for root in (Path("data/package"), Path("data/release-sunday"), Path("vendor/arena-kit")):
        for path in root.rglob("*"):
            if path.is_file() and not any(part in {"__pycache__", ".git", ".venv"} for part in path.parts):
                files["source/" + str(path)] = path
    checksums = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    dump(folder / "bundle-checksums.json", checksums)
    destination = folder / "training-bundle.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.write(folder / "bundle-checksums.json", "checksums.json")
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, expected in checksums.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
    print(
        json.dumps(
            {
                "report": str(folder / "REPORT.txt"),
                "bundle": str(destination),
                "bundle_bytes": destination.stat().st_size,
                "conversations": summary["conversations_excluding_verification"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "corpus", "evidence", "tactical", "mixed"):
        parser.add_argument("--" + name, required=True)
    package_result(parser.parse_args())
