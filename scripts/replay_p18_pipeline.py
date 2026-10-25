"""Offline counterfactual decisions against saved P18 threads. No hosted or model calls."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.execution_policy import controlled_action
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package


class NoModel:
    def complete(self, *args):
        raise AssertionError("Replay expected a deterministic closing decision; model calls are disabled")


def replay(path, pkg, strategy, policy, through_message=None):
    run = json.loads(path.read_text())
    case = run["case"]
    if case["case_id"] != "case:P18" or len(run["threads"]) != 1:
        raise ValueError("This replay is scoped to saved P18 runs")
    sid, events = next(iter(run["threads"].items()))
    live_indices = [i for i, m in enumerate(events)
                    if m.get("from") != "buyer" and m.get("status") in ("open", "final_offer")
                    and m.get("term_sheets")]
    final_index = max(live_indices) if through_message is None else through_message - 1
    if final_index not in live_indices:
        raise ValueError("--through-message must identify an open or final supplier offer (one-based)")
    threads = {sid: events[:final_index + 1]}
    spec = PromptSpec("offline-p18-replay", strategy, assessment_enabled=True, execution_policy=policy)
    agent = PromptLabAgent(pkg, NoModel(), spec)
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    control = controlled_action(case, threads, payload["protocol_state"], agent.acceptance_floor, policy)
    move, local_stop = None, None
    if control:
        try:
            move = agent.decide(case, threads)
        except NegotiationBlocked as exc:
            local_stop = {"reason": exc.reason, "audit": agent.audit["terminal_limits"]}
    decision = agent.decisions[0] if agent.decisions else {
        "source": "model:required", "note": "No deterministic action; no model was called.",
        "legal_actions": payload["protocol_state"]["suppliers"][sid]["legal_actions"]}
    if local_stop:
        decision = {"source": "policy:final-offer-stop", "action": "stop", "local_stop": local_stop,
                    "note": "Offline local stop; no supplier message or cleanup request was sent."}
    terminal = None
    if run.get("result", {}).get("ended") and run["result"].get("deal"):
        # The production play loop checks table.done/the result before another buyer call.
        # A thread's 'agreed' label alone is not the server's ended-session envelope.
        terminal = "Saved server result confirms a completed deal; no further buyer decision requested."
    else:
        try:
            agent.decide(case, run["threads"])
        except ContractError as exc:
            terminal = str(exc)
        if terminal != "No live supplier remains":
            raise AssertionError("Saved full thread was expected to remain terminal")
    transport = payload["evidence_transport"]
    return {
        "source_run": run["id"], "source_file": str(path.resolve()),
        "replay_through_message": final_index + 1,
        "then_live_offer_ids": payload["protocol_state"]["suppliers"][sid]["eligible_offer_ids"],
        "counterfactual_action": move, "decision": decision,
        "local_stop": local_stop,
        "price_plan": payload["price_plan"],
        "payload": {**transport, "reduction_pct": round(100 * (
            1 - transport["delivered_payload_chars"] / transport["raw_payload_chars"]), 2),
            "measurement": "Initial JSON characters, not tokens, latency or billing. Raw size precedes new signals."},
        "terminal_replay": terminal,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, help="Saved P18 run; repeat for multiple files")
    parser.add_argument("--strategy", type=Path, default=ROOT / "prompts/p18-diagnostic-v14.txt")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/negotiation-policies.example.json")
    parser.add_argument("--through-message", type=int,
                        help="Replay only through this one-based supplier message; defaults to the last live offer")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/p18-code-pipeline/offline-replay.json")
    args = parser.parse_args()
    paths = args.run or [ROOT / "data/local/runs" / f"{run}.json" for run in (
        "7e756bd88f304d81a4a70a6c4fabac64", "7cd1987baac74f5a94b36e3a5359185b")]
    strategy = args.strategy.read_text()
    policy = json.loads(args.policy.read_text())["case:P18"]
    pkg = package()
    report = {
        "created_at": datetime.now(UTC).isoformat(), "mode": "offline_counterfactual",
        "limitations": "Verifies what the buyer code would choose on recorded live states. "
                        "Does not simulate supplier acceptance or predict the score of a new run.",
        "hosted_sessions_used": 0, "model_calls": 0, "execution_policy": policy,
        "strategy_file": str(args.strategy.resolve()), "policy_file": str(args.policy.resolve()),
        "replays": [replay(path, pkg, strategy, policy, args.through_message) for path in paths],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output.resolve()), "results": [
        {"source_run": r["source_run"], "action": r["decision"]["source"],
         "payload_reduction_pct": r["payload"]["reduction_pct"]} for r in report["replays"]]}, indent=2))


if __name__ == "__main__":
    main()
