import copy
import logging
import re
import time

from arena_kit import ArenaClient
from arena_kit.agents.naive import NaiveAgent
from arena_kit.play import RemoteTable, fetch_documents, play

from .action_policy import NegotiationBlocked, validate_buyer_message
from .agent import ClaudeBackend, Negotiator
from .research import package
from .store import new_run, save_run


class RecordingTable(RemoteTable):
    def __init__(self, client, run, case=None):
        super().__init__(client, case)
        self.run = run
        self.started = time.monotonic()

    def observe_decision(self, agent):
        self.run["decisions"] = getattr(agent, "decisions", [])
        backend = getattr(agent, "backend", None)
        if getattr(backend, "model", None):
            self.run["model"] = backend.model
        if hasattr(agent, "audit"):
            self.run["audit"] = agent.audit
        if hasattr(agent, "spec"):
            self.run["prompt"] = agent.spec.metadata()
        self.run["metrics"] = {
            "elapsed_s": round(time.monotonic() - self.started, 2),
            "fallbacks": getattr(agent, "fallbacks", 0),
            **{
                k: getattr(backend, k, 0)
                for k in ("calls", "errors", "input_tokens", "output_tokens", "refusals", "empty_responses")
            },
            "stop_reasons": getattr(backend, "stop_reasons", {}),
        }
        save_run(self.run)

    def open(self, case_id):
        case, threads = super().open(case_id)
        self.run.update(case=case, session_id=self.session_id)
        for ev in threads.values():
            for m in ev:
                fetch_documents(self, m)
        self.run["threads"] = copy.deepcopy(threads)
        if self.done:
            self.run.update(status="completed", result=self.done)
        save_run(self.run)
        return case, threads

    def send(self, supplier_id, message):
        validate_buyer_message(message)
        self.run["threads"][supplier_id].append({**message, "from": "buyer"})
        save_run(self.run)
        reply = super().send(supplier_id, message)
        fetch_documents(self, reply)
        self.run["threads"][supplier_id].append(copy.deepcopy(reply))
        save_run(self.run)
        return reply

    def close(self, supplier_id, message):
        reply = self.send(supplier_id, message)
        result = reply.get("result", reply)
        self.run.update(status="completed", result=result)
        save_run(self.run)
        return result

    def result(self):
        result = super().result()
        self.run.update(status="completed", result=result)
        save_run(self.run)
        return result


def make_agent(name, pkg=None, tactic="prepared"):
    if name == "baseline":
        return NaiveAgent()
    if name == "original":
        from .prompt_protocol import PromptLabAgent, prompt_specs

        return PromptLabAgent(pkg or package(), ClaudeBackend(max_tokens=12000), prompt_specs()["original"])
    if name not in {"guarded", "adaptive", "claude"}:
        raise ValueError("Unknown agent")
    return Negotiator(pkg or package(), ClaudeBackend() if name == "claude" else None, variant=name, tactic=tactic)


def finish_run(run, agent, result, started):
    run.update(status="completed", result=result, decisions=getattr(agent, "decisions", []))
    backend = getattr(agent, "backend", None)
    if getattr(backend, "model", None):
        run["model"] = backend.model
    if hasattr(agent, "audit"):
        run["audit"] = agent.audit
    if hasattr(agent, "spec"):
        run["prompt"] = agent.spec.metadata()
    run["metrics"] = {
        "elapsed_s": round(time.monotonic() - started, 2),
        "fallbacks": getattr(agent, "fallbacks", 0),
        **{
            k: getattr(backend, k, 0)
            for k in ("calls", "errors", "input_tokens", "output_tokens", "refusals", "empty_responses")
        },
        "stop_reasons": getattr(backend, "stop_reasons", {}),
    }
    save_run(run)
    return run


def hosted_play(case_id, name="original", run=None, client=None, tactic="prepared"):
    run = run or new_run(case_id, "hosted", name)
    save_run(run)
    started = time.monotonic()
    agent = make_agent(name, tactic=tactic)
    try:
        result = play(RecordingTable(client or ArenaClient(), run), case_id, agent)
        return finish_run(run, agent, result, started)
    except NegotiationBlocked as exc:
        finish_run(run, agent, {}, started)
        run.update(
            status="failed",
            failure_origin="protocol_limit",
            error=exc.reason,
            termination={
                "origin": "protocol",
                "reason": exc.reason,
                "buyer_action_sent": False,
                "remote_session_may_remain_open": True,
            },
        )
        save_run(run)
        return run
    except Exception as e:
        logging.getLogger("aeiou").exception("Negotiation failed for %s", case_id)
        detail = re.sub(r"sk-ant-[\w-]+", "[redacted]", str(getattr(e, "detail", e)))[:500]
        run.update(status="failed", error=f"{type(e).__name__}: {detail}")
        finish_run(run, agent, {}, started)
        run["status"] = "failed"
        save_run(run)
        return run
