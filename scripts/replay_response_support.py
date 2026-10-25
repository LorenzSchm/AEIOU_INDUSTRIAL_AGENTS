"""Offline integration replay; no supplier messages, Arena sessions or LLM calls."""

import argparse
import json
from pathlib import Path

from aeiou.action_policy import NegotiationBlocked
from aeiou.custom_model import CustomResponseModel
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package


class NoBackend:
    def complete(self, *args, **kwargs):
        raise AssertionError("Offline replay must not request a language model")


def replay(model_path, run_path, policy_path):
    run = json.loads(Path(run_path).read_text())
    case = run["case"]
    model = CustomResponseModel(model_path)
    policy = json.loads(Path(policy_path).read_text())[case["case_id"]]
    buyer = PromptLabAgent(
        package(),
        NoBackend(),
        PromptSpec(
            "support-replay",
            "Compare evidence and whole packages.",
            assessment_enabled=True,
            execution_policy=policy,
        ),
    )
    buyer.response_model = model
    buyer.prepare(case)
    decisions = []
    for sid, events in run["threads"].items():
        for end, event in enumerate(events, 1):
            if event.get("from") == "buyer":
                continue
            threads = {sid: events[:end]}
            payload = json.loads(buyer.render_lab(case, threads))
            support = buyer.last_response_support
            state = support["suppliers"].get(sid, {})
            row = {
                "supplier_id": sid,
                "message_count": end,
                "supplier_status": event.get("status"),
                "support": support,
                "forecast_in_buyer_input": "learned_response_support" in payload,
                "supported_menus": sum(bool(m.get("predictions")) for m in state.get("menus", [])),
            }
            if event.get("status") in {"final", "final_offer"}:
                try:
                    row["controller_action"] = buyer.decide(case, threads)["message"].get("action")
                except NegotiationBlocked as exc:
                    row["controller_stop"] = str(exc)
            decisions.append(row)
    return {
        "session_id": run["session_id"],
        "case_id": case["case_id"],
        "model_sha256": model.sha256,
        "training_examples": model.artifact["training_examples"],
        "training_sessions": model.artifact["training_sessions"],
        "routes": model.artifact["routes"],
        "test_metrics": model.artifact.get("test_metrics"),
        "general_data_comparison": model.artifact.get("general_data_comparison"),
        "decisions": decisions,
        "new_arena_sessions": 0,
        "model_api_calls": 0,
        "limitation": "Historical integration replay, including training observations; no outcome improvement claim.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = replay(args.model, args.run, args.policy)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "output": str(output),
                "decisions": [{k: v for k, v in row.items() if k != "support"} for row in report["decisions"]],
            },
            indent=2,
        )
    )
