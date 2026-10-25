import copy
import logging
import re
import time

from arena_kit import ArenaClient
from arena_kit.agents.naive import NaiveAgent
from arena_kit.client import ArenaError
from arena_kit.play import RemoteTable, closing_move, fetch_documents, latest, live_threads, moves_left, play, standing

from .acceptance_floor import AcceptanceFloor
from .action_policy import NegotiationBlocked, validate_buyer_message
from .agent import Negotiator, make_backend
from .config import model_provider
from .negotiation_state import offer_state
from .optimizer import evaluate
from .research import package
from .store import new_run, save_run


class RecordingTable(RemoteTable):
    def __init__(self, client, run, case=None):
        super().__init__(client, case)
        self.run = run
        self.started = time.monotonic()
        self.acceptance_recoveries = 0
        self.deadlines = {}
        self.slowest_reply = 0.0
        self.rejected_acceptances = set()

    def discard_rejected_alternatives(self, supplier_id, events, primary_id):
        """A rejected advertised alternative must not win the next close decision.

        Keep the server's current primary even after a race, and let the ordinary
        economic close policy re-evaluate all surviving offers. Never substitute
        an ID while silently accepting different terms.
        """
        message = latest(events)
        removed = [
            s
            for s in message.get("term_sheets", [])
            if (supplier_id, s.get("term_sheet_id")) in self.rejected_acceptances
            and s.get("term_sheet_id") != primary_id
        ]
        if removed:
            message["term_sheets"] = [s for s in message["term_sheets"] if s not in removed]
            self.run.setdefault("transport_events", []).append(
                {
                    "type": "rejected_alternative_excluded",
                    "supplier_id": supplier_id,
                    "primary_offer_id": primary_id,
                    "rejected_term_sheets": copy.deepcopy(removed),
                }
            )

    def refresh(self, threads):
        """Reconcile server-side timeout moves before planning or accepting."""
        state = self.client.session(self.session_id)
        if state.get("status") == "ended":
            result = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
            self.run.update(status="completed", result=result)
            save_run(self.run)
            return result
        now = time.monotonic()
        self.deadlines = {
            sid: now + meta["seconds_left"]
            for sid, meta in state.get("threads", {}).items()
            if meta.get("seconds_left") is not None and not meta.get("closed")
        }
        for sid, meta in state.get("threads", {}).items():
            events = threads.get(sid, [])
            if (
                events
                and not meta.get("closed")
                and (standing(events)[0], latest(events).get("status")) == (meta.get("offer_id"), meta.get("status"))
            ):
                self.discard_rejected_alternatives(sid, events, meta.get("offer_id"))
                continue
            refreshed = copy.deepcopy(self.client.thread(self.session_id, sid)["messages"])
            cached = {
                a.get("attachment_id"): a["text"] for m in events for a in m.get("attachments", []) if "text" in a
            }
            for message in refreshed:
                for attachment in message.get("attachments", []):
                    if attachment.get("attachment_id") in cached:
                        attachment["text"] = cached[attachment["attachment_id"]]
                fetch_documents(self, message)
            if meta.get("closed") and latest(refreshed).get("status") not in {"walked_away", "no_bid"}:
                refreshed.append(
                    {
                        "from": "arena",
                        "status": "walked_away",
                        "term_sheets": [],
                        "body": "Server reports this supplier thread closed.",
                    }
                )
            self.discard_rejected_alternatives(sid, refreshed, meta.get("offer_id"))
            threads[sid] = refreshed
            self.run.setdefault("transport_events", []).append(
                {"type": "thread_refreshed", "supplier_id": sid, "offer_id": meta.get("offer_id")}
            )
        self.run["threads"] = copy.deepcopy(threads)
        save_run(self.run)
        return None

    def deadline_close(self, agent, threads):
        """Protect an existing deal before another slow supplier reply can expire it."""
        if getattr(agent, "manages_execution", False):
            return None
        if hasattr(agent, "set_runtime_context"):
            agent.set_runtime_context({})
        if not any(m.get("from") == "buyer" for events in threads.values() for m in events):
            return None
        try:
            choice = (
                agent.close(self.run["case"], threads)
                if hasattr(agent, "close")
                else closing_move(self.run["case"], threads)
            )
        except NegotiationBlocked:
            # No eligible close is not a reason to stop an ordinary negotiation.
            return None
        if choice["message"].get("action", {}).get("type") != "accept":
            return None
        # With several suppliers, their clocks continue while another one replies.
        reserve = max(15.0, self.slowest_reply + 12.0) if len(live_threads(threads)) > 1 else 15.0
        deadline = self.deadlines.get(choice["supplier_id"])
        if deadline is not None and deadline - time.monotonic() <= reserve:
            sid = choice["supplier_id"]
            seconds_left = deadline - time.monotonic()
            # A long reply must not lock the whole RFQ onto the first incumbent.
            # Explore each untouched open supplier once while the incumbent can
            # survive at most one timeout. Final/last-round offers and an already
            # timed-out incumbent still take priority. This is bounded exploration,
            # not a guarantee that the incumbent's next quote stays unchanged.
            last_buyer = max((i for i, m in enumerate(threads[sid]) if m.get("from") == "buyer"), default=-1)
            unanswered = sum(m.get("from") != "buyer" for m in threads[sid][last_buyer + 1 :])
            replies = [i for i, m in enumerate(threads[sid]) if m.get("from") != "buyer" and m.get("term_sheets")]
            progressing = False
            if len(replies) >= 2:
                economics = getattr(agent, "intelligence", {})
                previous = evaluate(self.run["case"], {sid: threads[sid][: replies[-2] + 1]}, economics)
                current = evaluate(self.run["case"], {sid: threads[sid]}, economics)
                old_cost = min((r["cost"] for r in previous if r["acceptable"]), default=None)
                new_cost = min((r["cost"] for r in current if r["acceptable"]), default=None)
                progressing = old_cost is not None and new_cost is not None and new_cost < old_cost * 0.999
            untouched = [
                s
                for s in live_threads(threads)
                if s != sid
                and latest(threads[s]).get("status") == "open"
                and not any(m.get("from") == "buyer" for m in threads[s])
                and self.deadlines.get(s, 0) - time.monotonic() > 8
            ]
            if (
                untouched
                and not progressing
                and hasattr(agent, "set_runtime_context")
                and latest(threads[sid]).get("status") == "open"
                and moves_left(self.run["case"], threads[sid]) > 3
                and unanswered <= 1
                and seconds_left > 8
                and max(60.0, self.slowest_reply) + 5 < seconds_left + 60
            ):
                target = min(untouched, key=lambda s: self.deadlines[s])
                agent.set_runtime_context(
                    {
                        "focus_supplier": target,
                        "policy_only": True,
                        "seconds_left": self.deadlines[target] - time.monotonic(),
                        "reason": "Bounded first contact before alternatives expire",
                    }
                )
                self.run.setdefault("transport_events", []).append(
                    {
                        "type": "deadline_explore",
                        "supplier_id": target,
                        "incumbent": sid,
                        "incumbent_seconds_left": seconds_left,
                        "maximum_expected_timeouts": 1,
                    }
                )
                return None
            if (
                hasattr(agent, "set_runtime_context")
                and latest(threads[sid]).get("status") == "open"
                and moves_left(self.run["case"], threads[sid]) > 1
                and seconds_left > 8.0
            ):
                # A reply to THIS supplier pauses its own message clock. Only
                # switching to another supplier needs a whole reply-time reserve.
                # Use a bounded code-only decision here, not a potentially slow LLM.
                agent.set_runtime_context(
                    {
                        "focus_supplier": sid,
                        "policy_only": True,
                        "seconds_left": seconds_left,
                        "reason": "Protect incumbent by replying now",
                    }
                )
                self.run.setdefault("transport_events", []).append(
                    {
                        "type": "deadline_focus",
                        "supplier_id": sid,
                        "seconds_left": seconds_left,
                        "avoided_reply_reserve_s": reserve,
                    }
                )
                return None
            self.run.setdefault("transport_events", []).append(
                {"type": "deadline_close", "supplier_id": choice["supplier_id"], "reserve_s": reserve}
            )
            return choice
        return None

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
                for k in (
                    "calls",
                    "errors",
                    "input_tokens",
                    "output_tokens",
                    "refusals",
                    "empty_responses",
                    "reasoning_tokens",
                    "cached_input_tokens",
                )
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
            selected = next(
                (
                    o
                    for o in offer_state(self.run["case"], self.run["threads"][supplier_id])
                    if o["term_sheet_id"] == action.get("term_sheet_id")
                ),
                None,
            )
            if selected and not floor.allows_unit_price(selected["terms"]):
                raise NegotiationBlocked("ACCEPTANCE_UNIT_PRICE_LIMIT_EXCEEDED")
            if not selected or not floor.allows_cost(selected["cost"]):
                raise NegotiationBlocked("ACCEPTANCE_SCORE_FLOOR_NOT_MET")
        outgoing = {**message, "from": "buyer"}
        self.run["threads"][supplier_id].append(outgoing)
        save_run(self.run)
        started = time.monotonic()
        try:
            reply = super().send(supplier_id, message)
        except Exception as exc:
            action = message.get("action", {})
            if (
                getattr(exc, "status", None) == 422
                and "not a standing offer" in str(getattr(exc, "detail", ""))
                and action.get("type") == "accept"
                and action.get("term_sheet_id")
            ):
                self.rejected_acceptances.add((supplier_id, action["term_sheet_id"]))
            self.run.setdefault("transport_events", []).append(
                {
                    "type": "send_failed",
                    "supplier_id": supplier_id,
                    "action": message.get("action"),
                    "status": getattr(exc, "status", None),
                }
            )
            if isinstance(exc, ArenaError) and exc.status == 422:
                self.run["threads"][supplier_id].pop()
                self.run.setdefault("rejected_actions", []).append(
                    {
                        "supplier_id": supplier_id,
                        "message": copy.deepcopy(message),
                        "http_status": exc.status,
                        "detail": str(exc.detail),
                    }
                )
            save_run(self.run)
            raise
        self.slowest_reply = max(self.slowest_reply, time.monotonic() - started)
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
            "supplier_id": supplier_id,
            "rejected_offer_id": action.get("term_sheet_id"),
            "reason": str(error.detail),
            "attempt": self.acceptance_recoveries,
            "session_status": state.get("status"),
        }
        self.run.setdefault("acceptance_recoveries", []).append(recovery)
        if state.get("status") == "ended":
            self.done = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
            save_run(self.run)
            return {}
        cached = {
            att["attachment_id"]: att["text"]
            for events in self.run["threads"].values()
            for event in events
            for att in event.get("attachments", [])
            if att.get("attachment_id") and "text" in att
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
    if name == "custom-p10-p18":
        from .custom_agent import CustomBuyer

        return CustomBuyer(pkg or package(), make_backend(max_tokens=12000))
    if name == "baseline":
        return NaiveAgent()
    if name in {"strategic", "strategic-policy"}:
        from .strategic import StrategicNegotiator

        return StrategicNegotiator(pkg or package(), make_backend() if name == "strategic" else None, tactic=tactic)
    if name == "original":
        from .prompt_protocol import PromptLabAgent, prompt_specs

        return PromptLabAgent(pkg or package(), make_backend(max_tokens=12000), prompt_specs()["original"])
    if name not in {"guarded", "adaptive", "claude"}:
        raise ValueError("Unknown agent")
    return Negotiator(pkg or package(), make_backend() if name == "claude" else None, variant=name, tactic=tactic)


def finish_run(run, agent, result, started):
    from .handoff import procurement_handoff

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
            for k in (
                "calls",
                "errors",
                "input_tokens",
                "output_tokens",
                "refusals",
                "empty_responses",
                "reasoning_tokens",
                "cached_input_tokens",
            )
        },
        "resolved_model": getattr(backend, "resolved_model", None),
        "stop_reasons": getattr(backend, "stop_reasons", {}),
        "refusal_details": getattr(backend, "refusal_details", []),
    }
    run["handoff"] = procurement_handoff(run)
    save_run(run)
    return run


def hosted_play(case_id, name="original", run=None, client=None, tactic="prepared"):
    if name == "custom-p10-p18":
        from .custom_agent import validate_custom_case

        validate_custom_case(case_id)
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
