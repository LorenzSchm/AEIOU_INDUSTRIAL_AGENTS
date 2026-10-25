"""Bounded, matched hosted sandbox comparison. Requires explicit --run; uses model credits."""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from aeiou.eval_suite import prepare_evaluation, run_evaluation
from aeiou.training_corpus import dump


def main(args):
    paths = [Path(args.baseline), Path(args.candidate)]
    count = 2 * 2 * args.repeats
    if count > args.max_sessions:
        raise ValueError(f"Plan requires {count} sessions, above the explicit cap")
    plan = {
        "set": "sandbox",
        "cases": ["case:D01", "case:D02"],
        "repeats": args.repeats,
        "total_new_sessions": count,
        "model": args.model,
        "effort": args.effort,
        "prompt_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "primary_metric": "official scores; failures zero in all-attempt mean; show completed-only separately",
    }
    if not args.run:
        print(json.dumps(plan, indent=2))
        return
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=False)
    dump(folder / "prospective-plan.json", plan)
    reports = []
    for variant, path in zip(("a", "b"), paths, strict=True):
        text = path.read_text()
        (folder / f"prompt-{variant}.txt").write_text(text)
        report = prepare_evaluation(
            text,
            model=args.model,
            effort=args.effort,
            variant_id=variant,
            comparison_id=folder.name,
            repeats=args.repeats,
        )
        report["parallel_runs"] = 2
        reports.append(report)
    dump(folder / "evaluation-ids.json", [{"variant": r["variant_id"], "id": r["id"]} for r in reports])
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run_evaluation, r) for r in reports]):
            report = future.result()
            dump(folder / f"evaluation-{report['variant_id']}.json", report)
            print(json.dumps({"variant": report["variant_id"], "status": report["status"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--max-sessions", type=int, default=12)
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--run", action="store_true")
    main(parser.parse_args())
