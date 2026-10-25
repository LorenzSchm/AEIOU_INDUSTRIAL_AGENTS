"""Run one explicit P10 practice trial and preserve its inputs and hosted result."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from aeiou.eval_suite import prepare_evaluation, refresh_practice_catalog, run_evaluation, save_evaluation

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", type=int)
    parser.add_argument("prompt", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="medium")
    args = parser.parse_args()
    folder = ROOT / "reports" / "p10-target72" / f"v{args.variant}"
    folder.mkdir(exist_ok=False)
    prompt = args.prompt.resolve()
    text = prompt.read_text()
    (folder / "prompt.txt").write_text(text)
    sources = [prompt, *[ROOT / "src" / "aeiou" / name for name in (
        "p10_evidence.py", "price_planner.py", "evidence_transport.py", "prompt_protocol.py", "research.py",
        "optimizer.py", "forecast_evidence.py",
    )]]
    hashes = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        destination = folder / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
    report = prepare_evaluation(text, "claude-opus-5-5", "practice", case_ids=["case:P10"],
                                repeats=1, catalog=refresh_practice_catalog(), effort=args.effort)
    report["parallel_runs"] = 1
    report["experiment"] = {"label": args.label, "target_score": 72, "source_hashes": hashes}
    save_evaluation(report)
    (folder / "evaluation-id.txt").write_text(report["id"])
    print(json.dumps({"evaluation_id": report["id"], "variant": args.variant}), flush=True)
    result = run_evaluation(report)
    (folder / "evaluation.json").write_text(json.dumps(result, indent=2))
    for row in result["rows"]:
        if row.get("run_id"):
            shutil.copy2(ROOT / "data" / "local" / "runs" / f"{row['run_id']}.json", folder / "run.json")
    print(json.dumps({"status": result["status"], "rows": result["rows"]}), flush=True)


if __name__ == "__main__":
    main()
