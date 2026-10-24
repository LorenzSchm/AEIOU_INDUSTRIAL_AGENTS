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
    agents = ["baseline", "guarded", "adaptive", "claude", "strategic", "strategic-policy"]
    bench.add_argument("--agent", choices=agents[1:], default="adaptive")
    bench.add_argument("--seeds", type=int, default=3)
    bench.add_argument("--label", default="local")
    bench.add_argument("--seed-start", type=int, default=0)
    bench.add_argument("--parallel", type=int, default=1)
    compare = sub.add_parser("compare-prompts", help="Local only; uses model API credits, zero arena sessions")
    compare.add_argument("--model", help="Buyer model override; does not edit .env")
    compare.add_argument("--supplier", choices=["scripted", "language"], default="scripted")
    compare.add_argument("--supplier-model", help="Hold fixed when comparing buyer models")
    compare.add_argument("--seed", type=int, default=71)
    compare.add_argument("--label", default="prompt-screen")
    compare.add_argument("--parallel", type=int, default=2)
    play = sub.add_parser("play")
    play.add_argument("case_id")
    play.add_argument("--mode", choices=["local", "hosted"], default="hosted")
    play.add_argument("--agent", choices=agents, default="claude")
    play.add_argument("--opponent", default="scripted")
    play.add_argument("--tactic", choices=["prepared", "voss"], default="prepared")
    practice = sub.add_parser("practice")
    practice.add_argument("--parallel", type=int, default=3)
    practice.add_argument("--agent", choices=agents, default="claude")
    final = sub.add_parser("final")
    final.add_argument("--agent", choices=agents[1:], default="claude")
    final.add_argument("--parallel", type=int, default=10)
    final.add_argument("--waves", type=int, default=5)
    sub.add_parser("doctor")
    sub.add_parser("report")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.command == "serve":
        import uvicorn

        uvicorn.run("aeiou.service:app", host="127.0.0.1", port=a.port)
    elif a.command == "doctor":
        from .agent import ClaudeBackend
        from .research import package

        backend = ClaudeBackend()
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

        c = ArenaClient()
        cases = hosted(c.cases("practice"))
        left = c.me().get("practice", {}).get("left", 0)
        with ThreadPoolExecutor(max_workers=min(10, max(1, a.parallel))) as pool:
            for r in pool.map(lambda case: hosted_play(case["case_id"], a.agent, client=c), cases[:left]):
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
