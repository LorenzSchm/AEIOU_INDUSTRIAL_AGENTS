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
    evaluation = sub.add_parser("evaluate", help="Paired local evaluation, uncertainty intervals and ablations")
    evaluation.add_argument("--agents", nargs="+", default=["adaptive", "strategic-policy"])
    evaluation.add_argument("--seeds", nargs="+", type=int, default=[99, 100])
    evaluation.add_argument("--cases", nargs="+")
    evaluation.add_argument("--opponents", nargs="+")
    evaluation.add_argument("--repeats", type=int, default=1)
    evaluation.add_argument("--supplier", choices=["scripted", "language"], default="scripted")
    evaluation.add_argument("--supplier-models", nargs="+", default=[None])
    evaluation.add_argument("--buyer-model")
    evaluation.add_argument("--allow-api", action="store_true")
    evaluation.add_argument("--max-sessions", type=int, default=120)
    evaluation.add_argument("--output")
    evaluation.add_argument("--ablations", action="store_true", help="Add one disabled feature at a time")
    optimize = sub.add_parser("optimize-writer", help="Offline prompt search with disjoint case splits")
    optimize.add_argument("--agent", choices=["strategic", "strategic-policy"], default="strategic")
    optimize.add_argument("--rounds", type=int, default=2)
    optimize.add_argument("--train", nargs="+", default=["case:S01", "case:S02", "case:T01"])
    optimize.add_argument("--validation", nargs="+", default=["case:S03"])
    optimize.add_argument("--test", nargs="+", default=["case:T02"])
    optimize.add_argument("--opponents", nargs="+", default=["hardliner"])
    optimize.add_argument("--supplier", choices=["scripted", "language"], default="scripted")
    optimize.add_argument("--allow-api", action="store_true")
    optimize.add_argument("--max-sessions", type=int, default=40)
    optimize.add_argument("--output")
    retrieve = sub.add_parser("research", help="Search case-scoped supplier evidence locally")
    retrieve.add_argument("case_id")
    retrieve.add_argument("query")
    retrieve.add_argument("--supplier-id")
    retrieval_eval = sub.add_parser("evaluate-retrieval", help="Measure recall on a labeled local query set")
    retrieval_eval.add_argument("gold_file")
    retrieval_eval.add_argument("--output", default="data/local/retrieval-evaluation.json")
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
    elif a.command == "evaluate":
        from .evaluation import evaluate_matrix
        from .strategy_policy import StrategicFeatures

        labels = list(a.agents)
        if a.ablations:
            base = next((name for name in labels if name in {"strategic", "strategic-policy"}), None)
            if base is None:
                p.error("--ablations requires strategic or strategic-policy in --agents")
            labels.extend(f"{base}:no-{field.replace('_', '-')}" for field in StrategicFeatures().metadata())
        report = evaluate_matrix(
            agents=labels,
            seeds=a.seeds,
            case_ids=a.cases,
            opponents=a.opponents,
            repeats=a.repeats,
            supplier_mode=a.supplier,
            supplier_models=a.supplier_models,
            buyer_model=a.buyer_model,
            allow_api=a.allow_api,
            max_sessions=a.max_sessions,
            output=a.output,
        )
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    elif a.command == "optimize-writer":
        from .prompt_optimization import optimize_writer

        report = optimize_writer(
            train=a.train,
            validation=a.validation,
            test=a.test,
            rounds=a.rounds,
            agent=a.agent,
            supplier_mode=a.supplier,
            opponents=a.opponents,
            allow_api=a.allow_api,
            max_sessions=a.max_sessions,
            output=a.output,
        )
        print(json.dumps(report, indent=2))
    elif a.command == "research":
        from .research import package
        from .research_tools import decision_research
        from .simulation import local_cases

        pkg = package()
        cases = {**pkg.cases, **local_cases()}
        if a.case_id not in cases:
            p.error("Unknown case")
        print(json.dumps(decision_research(cases[a.case_id], pkg, a.query, a.supplier_id), indent=2))
    elif a.command == "evaluate-retrieval":
        from pathlib import Path

        from .research_tools import evaluate_retrieval

        gold = json.loads(Path(a.gold_file).read_text(encoding="utf-8"))
        print(json.dumps(evaluate_retrieval(gold, a.output), indent=2))
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
