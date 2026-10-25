import copy
import logging
import re
import time

from arena_kit import ArenaClient
from arena_kit.agents.naive import NaiveAgent
from arena_kit.client import ArenaError
from arena_kit.play import RemoteTable, fetch_documents, latest, play

from .acceptance_floor import AcceptanceFloor
from .action_policy import NegotiationBlocked, validate_buyer_message
from .agent import Negotiator, make_backend
from .config import model_provider
from .negotiation_state import offer_state
from .research import package
from .store import new_run, save_run


class RecordingTable(RemoteTable):
    def __init__(self, client, run, case=None):
        super().__init__(client, case)
        self.run = run
        self.started = time.monotonic()
        self.acceptance_recoveries = 0

    def observe_decision(self, agent):
        self.run["decisions"] = getattr(agent, "decisions", [])
        backend = getattr(agent, "backend", None)
        if getattr(backend, "model", None):
            self.run["model"] = backend.model
            self.run["provider"] = getattr(backend, "provider", model_provider(backend.model))
        if hasattr(agent, "audit"):
            self.run["audit"] = agent.audit
        if hasattr(agent, "spec"):
            self.run["prompt"] = agent.spec.metadata()
        self.run["metrics"] = {
            "elapsed_s": round(time.monotonic() - self.started, 2),
            "fallbacks": getattr(agent, "fallbacks", 0),
            **{
                k: getattr(backend, k, 0)
                for k in ("calls", "errors", "input_tokens", "output_tokens", "refusals", "empty_responses",
                          "reasoning_tokens", "cached_input_tokens")
            },
            "resolved_model": getattr(backend, "resolved_model", None),
            "stop_reasons": getattr(backend, "stop_reasons", {}),
            "refusal_details": getattr(backend, "refusal_details", []),
        }
        save_run(self.run)

    def open(self, case_id):
        case, threads = super().open(case_id)
        self.run.update(case=case, session_id=self.session_id)
        for sid, ev in threads.items():
            for m in ev:
                fetch_documents(self, m)
            self.confirm_standing_offer(sid, latest(ev))
        self.run["threads"] = copy.deepcopy(threads)
        if self.done:
            self.run.update(status="completed", result=self.done)
        save_run(self.run)
        return case, threads

    def confirm_standing_offer(self, supplier_id, message):
        # Supplier prose may confirm an alternative without registering it as acceptable.
        # Recheck every new menu, including after a successful clarification exchange.
        if len(message.get("term_sheets") or []) > 1:
            state = self.client.thread(self.session_id, supplier_id)
            message["standing_offer_id"] = state.get("offer_id")

    def send(self, supplier_id, message):
        validate_buyer_message(message)
        action = message.get("action") or {}
        policy = self.run.get("prompt", {}).get("acceptance_floor")
        floor = AcceptanceFloor.from_dict(policy) if policy is not None else None
        if floor:
            for sheet in message.get("term_sheets", []):
                if not floor.allows_unit_price(sheet.get("terms", {})):
                    raise NegotiationBlocked("ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED")
        if action.get("type") == "accept" and floor:
            floor.check_case(self.run["case"], self.run["threads"])
            selected = next((o for o in offer_state(self.run["case"], self.run["threads"][supplier_id])
                             if o["term_sheet_id"] == action.get("term_sheet_id")), None)
            if selected and not floor.allows_unit_price(selected["terms"]):
                raise NegotiationBlocked("ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED")
            if not selected or not floor.allows_cost(selected["cost"]):
                raise NegotiationBlocked("ACCEPTANCE_SCORE_FLOOR_NOT_MET")
        outgoing = {**message, "from": "buyer"}
        self.run["threads"][supplier_id].append(outgoing)
        save_run(self.run)
        try:
            reply = super().send(supplier_id, message)
        except ArenaError as exc:
            if exc.status == 422:
                # The server did not deliver this request or consume a negotiation round.
                self.run["threads"][supplier_id].pop()
                self.run.setdefault("rejected_actions", []).append({
                    "supplier_id": supplier_id, "message": copy.deepcopy(message),
                    "http_status": exc.status, "detail": str(exc.detail),
                })
                save_run(self.run)
            raise
        fetch_documents(self, reply)
        self.confirm_standing_offer(supplier_id, reply)
        self.run["threads"][supplier_id].append(copy.deepcopy(reply))
        save_run(self.run)
        return reply

    def recover_acceptance(self, supplier_id, message, error):
        """Refresh all threads after a rejected ID; let the agent choose the next action."""
        action = message.get("action") or {}
        if (
            action.get("type") != "accept"
            or "not a standing offer" not in str(error.detail).lower()
            or self.acceptance_recoveries >= 2
        ):
            return None
        self.acceptance_recoveries += 1
        state = self.client.session(self.session_id)
        recovery = {
            "supplier_id": supplier_id, "rejected_offer_id": action.get("term_sheet_id"),
            "reason": str(error.detail), "attempt": self.acceptance_recoveries,
            "session_status": state.get("status"),
        }
        self.run.setdefault("acceptance_recoveries", []).append(recovery)
        if state.get("status") == "ended":
            self.done = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
            save_run(self.run)
            return {}
        cached = {
            att["attachment_id"]: att["text"]
            for events in self.run["threads"].values() for event in events
            for att in event.get("attachments", []) if att.get("attachment_id") and "text" in att
        }
        threads = {}
        for sid in self.run["case"]["suppliers"]:
            remote = self.client.thread(self.session_id, sid)
            events = copy.deepcopy(remote["messages"])
            for event in events:
                for att in event.get("attachments", []):
                    if att.get("attachment_id") in cached:
                        att["text"] = cached[att["attachment_id"]]
                fetch_documents(self, event)
            current = latest(events)
            current["standing_offer_id"] = remote.get("offer_id")
            if sid == supplier_id:
                current["acceptance_rejection"] = {
                    "rejected_offer_id": action.get("term_sheet_id"),
                    "reason": str(error.detail),
                    "next_step": "Only server-confirmed standing offers can be accepted. "
                    "To choose a quoted alternative, request confirmation as the standing offer first. "
                    "No negotiation round was consumed by the rejected acceptance.",
                }
                recovery["standing_offer_id"] = remote.get("offer_id")
            threads[sid] = events
        self.run["threads"] = copy.deepcopy(threads)
        save_run(self.run)
        return threads

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

        return PromptLabAgent(pkg or package(), make_backend(max_tokens=12000), prompt_specs()["original"])
    if name not in {"guarded", "adaptive", "claude"}:
        raise ValueError("Unknown agent")
    return Negotiator(pkg or package(), make_backend() if name == "claude" else None, variant=name, tactic=tactic)


def finish_run(run, agent, result, started):
    run.update(status="completed", result=result, decisions=getattr(agent, "decisions", []))
    backend = getattr(agent, "backend", None)
    if getattr(backend, "model", None):
        run["model"] = backend.model
        run["provider"] = getattr(backend, "provider", model_provider(backend.model))
    if hasattr(agent, "audit"):
        run["audit"] = agent.audit
    if hasattr(agent, "spec"):
        run["prompt"] = agent.spec.metadata()
    run["metrics"] = {
        "elapsed_s": round(time.monotonic() - started, 2),
        "fallbacks": getattr(agent, "fallbacks", 0),
        **{
            k: getattr(backend, k, 0)
            for k in ("calls", "errors", "input_tokens", "output_tokens", "refusals", "empty_responses",
                      "reasoning_tokens", "cached_input_tokens")
        },
        "resolved_model": getattr(backend, "resolved_model", None),
        "stop_reasons": getattr(backend, "stop_reasons", {}),
        "refusal_details": getattr(backend, "refusal_details", []),
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
