import copy
import logging
import re
import time

from arena_kit import ArenaClient
from arena_kit.agents.naive import NaiveAgent
from arena_kit.play import RemoteTable, fetch_documents, play

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
        self.run["metrics"] = {
            "elapsed_s": round(time.monotonic() - self.started, 2),
            "fallbacks": getattr(agent, "fallbacks", 0),
            **{k: getattr(backend, k, 0) for k in ("calls", "errors", "input_tokens", "output_tokens")},
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
    if name in {"strategic", "strategic-policy"}:
        from .strategic import StrategicNegotiator

        return StrategicNegotiator(pkg or package(), ClaudeBackend() if name == "strategic" else None, tactic=tactic)
    return Negotiator(pkg or package(), ClaudeBackend() if name == "claude" else None, variant=name, tactic=tactic)


def finish_run(run, agent, result, started):
    from .handoff import procurement_handoff

    run.update(status="completed", result=result, decisions=getattr(agent, "decisions", []))
    backend = getattr(agent, "backend", None)
    run["metrics"] = {
        "elapsed_s": round(time.monotonic() - started, 2),
        "fallbacks": getattr(agent, "fallbacks", 0),
        **{k: getattr(backend, k, 0) for k in ("calls", "errors", "input_tokens", "output_tokens")},
    }
    run["handoff"] = procurement_handoff(run)
    save_run(run)
    return run


def hosted_play(case_id, name="claude", run=None, client=None, tactic="prepared"):
    run = run or new_run(case_id, "hosted", name)
    save_run(run)
    started = time.monotonic()
    agent = make_agent(name, tactic=tactic)
    try:
        result = play(RecordingTable(client or ArenaClient(), run), case_id, agent)
        return finish_run(run, agent, result, started)
    except Exception as e:
        logging.getLogger("aeiou").exception("Negotiation failed for %s", case_id)
        detail = re.sub(r"sk-ant-[\w-]+", "[redacted]", str(getattr(e, "detail", e)))[:500]
        run.update(status="failed", error=f"{type(e).__name__}: {detail}")
        finish_run(run, agent, {}, started)
        run["status"] = "failed"
        save_run(run)
        return run
