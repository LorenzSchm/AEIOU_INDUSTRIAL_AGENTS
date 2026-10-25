import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from arena_kit import ArenaClient
from arena_kit.play import hosted, play_waves

from . import config


def main():
    p = argparse.ArgumentParser(description="AEIOU negotiation workbench")
    sub = p.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    bench = sub.add_parser("benchmark")
    bench.add_argument("--agent", choices=["adaptive", "guarded", "claude", "original"], default="original")
    bench.add_argument("--seeds", type=int, default=1)
    bench.add_argument("--label", default="local")
    bench.add_argument("--seed-start", type=int, default=1101)
    bench.add_argument("--parallel", type=int, default=1)
    compare = sub.add_parser("compare-prompts", help="Local only; uses model API credits, zero arena sessions")
    compare.add_argument("--model", help="Buyer model override; does not edit .env")
    compare.add_argument("--supplier", choices=["language"], default="language")
    compare.add_argument("--supplier-model", help="Hold fixed when comparing buyer models")
    compare.add_argument("--seed", type=int, default=71)
    compare.add_argument("--label", default="prompt-screen")
    compare.add_argument("--parallel", type=int, default=2)
    play = sub.add_parser("play")
    play.add_argument("case_id")
    play.add_argument("--mode", choices=["local", "hosted"], default="local")
    play.add_argument("--agent", choices=["baseline", "guarded", "adaptive", "claude", "original"], default="original")
    play.add_argument("--opponent", default="relationship")
    play.add_argument("--tactic", choices=["prepared", "voss"], default="prepared")
    practice = sub.add_parser("practice")
    practice.add_argument("--parallel", type=int, default=3)
    practice.add_argument("--agent", choices=["baseline", "adaptive", "claude", "original"],
                          help="Explicitly opt into a legacy agent instead of an eval prompt")
    practice_prompt = practice.add_mutually_exclusive_group()
    practice_prompt.add_argument("--prompt-file", help="UTF-8 prompt, using the eval suite's neutral controller")
    practice_prompt.add_argument("--eval-batch", help="Use the exact prompt and model saved in this eval batch")
    practice.add_argument("--repeats", type=int, choices=[1, 2, 3], default=1)
    practice.add_argument("--max-sessions", type=int, default=2, help="Hard cap on new hosted attempts (default: 2)")
    final = sub.add_parser("final")
    final.add_argument("--parallel", type=int, default=10)
    final.add_argument("--waves", type=int, default=5)
    final.add_argument("--agent", choices=["original", "claude"], default="original")
    sub.add_parser("doctor")
    sub.add_parser("report")
    replay = sub.add_parser(
        "verify-replay", help="Verify an identical recorded hosted path offline; zero arena/model calls"
    )
    replay.add_argument("run_id")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.command == "serve":
        import uvicorn

        uvicorn.run("aeiou.service:app", host="127.0.0.1", port=a.port)
    elif a.command == "doctor":
        from .agent import make_backend
        from .research import package

        backend = make_backend()
        response = backend.complete("Reply with the word ready.", "Connection test.")
        print(
            json.dumps(
                {
                    "claude": response.strip(),
                    "model": backend.model,
                    "facts": len(package().facts),
                    "arena": ArenaClient().me(),
                },
                indent=2,
            )
        )
    elif a.command == "benchmark":
        from .benchmark import benchmark

        print(json.dumps(benchmark(a.agent, a.seeds, a.label, a.seed_start, a.parallel), indent=2))
    elif a.command == "compare-prompts":
        from .prompt_comparison import compare_prompts

        report = compare_prompts(a.model, a.supplier, a.supplier_model, a.seed, a.label, a.parallel)
        print(json.dumps({k: report[k] for k in ("status", "arena_sessions_used", "summary", "report_path")}, indent=2))
    elif a.command == "verify-replay":
        from .hosted_replay import verify_replay
        from .store import get_run

        print(json.dumps(verify_replay(get_run(a.run_id)), indent=2))
    elif a.command == "report":
        from .hosted_evaluation import hosted_evaluation

        report = hosted_evaluation()
        path = config.ROOT / "reports" / "hosted-practice.json"
        path.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    elif a.command == "play":
        from .runner import hosted_play

        if a.mode == "hosted":
            r = hosted_play(a.case_id, a.agent, tactic=a.tactic)
        else:
            from .simulation import local_play

            r = local_play(a.case_id, a.agent, a.opponent)
        print(
            json.dumps(
                {
                    "run_id": r["id"],
                    "status": r["status"],
                    "result": r.get("result"),
                    "metrics": r.get("metrics"),
                    "error": r.get("error"),
                },
                indent=2,
            )
        )
    elif a.command == "practice":
        from .runner import hosted_play

        if not 1 <= a.max_sessions <= 200:
            p.error("--max-sessions must be between 1 and 200")
        if a.prompt_file or a.eval_batch:
            from pathlib import Path

            from .eval_suite import prepare_evaluation, refresh_practice_catalog, run_evaluation

            if a.agent:
                p.error("Choose an eval prompt or an explicit legacy --agent, not both")
            saved = None
            if a.eval_batch:
                if not a.eval_batch.isalnum():
                    p.error("Invalid evaluation batch ID")
                saved = json.loads((config.state_root() / "eval-suite" / f"{a.eval_batch}.json").read_text())
                prompt = saved["prompt_text"]
            else:
                prompt = Path(a.prompt_file).read_text(encoding="utf-8")
            catalog = refresh_practice_catalog()
            limit = min(a.max_sessions, (catalog.get("quota") or {}).get("left", 0)) // a.repeats
            selected = [case["case_id"] for case in catalog["cases"][:limit]]
            if not selected:
                p.error("Not enough quota or --max-sessions for the requested repeats")
            report = prepare_evaluation(prompt, saved["model"] if saved else None, "practice",
                                        case_ids=selected, repeats=a.repeats, catalog=catalog)
            if saved:
                report.update({key: saved[key] for key in ("effort", "speed") if key in saved})
            report["parallel_runs"] = min(6, max(1, a.parallel))
            result = run_evaluation(report)
            print(json.dumps({k: result[k] for k in ("id", "status", "completed", "mean_score")}))
            return
        if not a.agent:
            p.error("Choose --eval-batch or --prompt-file to use your tested prompt (or explicitly choose --agent)")
        c = ArenaClient()
        cases = hosted(c.cases("practice"))
        left = c.me().get("practice", {}).get("left", 0)
        with ThreadPoolExecutor(max_workers=min(10, max(1, a.parallel))) as pool:
            for r in pool.map(
                lambda case: hosted_play(case["case_id"], a.agent, client=c), cases[: min(left, a.max_sessions)]
            ):
                print(
                    json.dumps(
                        {
                            "case": r["case_id"],
                            "status": r["status"],
                            "result": r.get("result"),
                            "metrics": r.get("metrics"),
                        }
                    )
                )
    elif a.command == "final":
        import time

        from .runner import RecordingTable, make_agent
        from .store import new_run

        # play_waves handles 403 waiting, 409 resume, cap extensions and supplier latency.
        # Wrap tables for durable replay without adding prohibited live-feed endpoints.
        class FinalTable(RecordingTable):
            def __init__(self, client, case=None):
                super().__init__(client, new_run(case["case_id"], "final", a.agent), case)

        # The kit owns polling / resume; the wrapper only adds local audit persistence.
        started = time.monotonic()
        results = play_waves(
            ArenaClient(),
            lambda: make_agent(a.agent),
            table_factory=FinalTable,
            waves=a.waves,
            parallel=min(10, max(1, a.parallel)),
        )
        path = config.state_root() / "final-results.json"
        path.write_text(json.dumps(results, default=str, indent=2))
        print(f"Final completed in {time.monotonic() - started:.0f}s; results: {path}")
