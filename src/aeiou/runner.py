import copy
import logging
import re
import time

from arena_kit import ArenaClient
from arena_kit.agents.naive import NaiveAgent
from arena_kit.play import RemoteTable, closing_move, fetch_documents, latest, live_threads, moves_left, play, standing

from .agent import ClaudeBackend, Negotiator
from .optimizer import evaluate
from .research import package
from .store import new_run, save_run


class RecordingTable(RemoteTable):
    def __init__(self, client, run, case=None):
        super().__init__(client, case)
        self.run = run
        self.started = time.monotonic()
        self.deadlines = {}
        self.slowest_reply = 0.0

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
            threads[sid] = refreshed
            self.run.setdefault("transport_events", []).append(
                {"type": "thread_refreshed", "supplier_id": sid, "offer_id": meta.get("offer_id")}
            )
        self.run["threads"] = copy.deepcopy(threads)
        save_run(self.run)
        return None

    def deadline_close(self, agent, threads):
        """Protect an existing deal before another slow supplier reply can expire it."""
        if hasattr(agent, "set_runtime_context"):
            agent.set_runtime_context({})
        if not any(m.get("from") == "buyer" for events in threads.values() for m in events):
            return None
        choice = (
            agent.close(self.run["case"], threads)
            if hasattr(agent, "close")
            else closing_move(self.run["case"], threads)
        )
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
        started = time.monotonic()
        try:
            reply = super().send(supplier_id, message)
        except Exception as exc:
            self.run.setdefault("transport_events", []).append(
                {
                    "type": "send_failed",
                    "supplier_id": supplier_id,
                    "action": message.get("action"),
                    "status": getattr(exc, "status", None),
                }
            )
            save_run(self.run)
            raise
        self.slowest_reply = max(self.slowest_reply, time.monotonic() - started)
        self.run["threads"][supplier_id].append({**message, "from": "buyer"})
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
