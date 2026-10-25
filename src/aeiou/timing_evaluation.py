"""Offline RFQ clock stress: supplier response time pauses only its own clock.

Hidden concession schedules are evaluator-only. No network or LLM calls.
Run as a script with --agent-source to compare a frozen checkout.
"""

import argparse
import copy
import json
import os
import random
import sys
from pathlib import Path
from statistics import mean
from unittest.mock import patch


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class TimedSuppliers:
    def __init__(self, count, delay, seed, profile="stable"):
        from arena_kit.cost import complete_offer

        from aeiou.simulation import local_cases

        rng = random.Random(seed)
        self.clock, self.delay, self.profile = Clock(), delay, profile
        self.case = copy.deepcopy(local_cases()["case:S01"])
        self.case.update(
            case_id=f"case:clock-{count}-{delay}-{seed}",
            suppliers=[f"sup:clock-{i}" for i in range(count)],
            parts=[],
            refs=[],
            facts={},
            trigger_doc=None,
            round_limit=8,
        )
        self.case["buyer"].update(alternative={"kind": "none", "terms": {}}, category_goal="leverage")
        price = rng.uniform(20, 80)
        discovery_winner = rng.randrange(count)
        self.state, self.opens, self.done, self.timeouts, self.accepts = {}, 0, None, 0, 0
        for i, sid in enumerate(self.case["suppliers"]):
            opening = price * (1 + 0.06 * i)
            self.state[sid] = {
                "round": 0,
                "version": 0,
                "last": self.clock.now,
                "timeout_streak": 0,
                "closed": False,
                "status": "open",
                "opening": opening,
                "floor": opening
                * (
                    (0.70 if i == discovery_winner else 0.98)
                    if profile == "discovery"
                    else (0.82 if profile == "stable" else 0.97 - 0.19 * i / max(1, count - 1))
                ),
                "terms": complete_offer(self.case, {"unit_price": opening, "payment_days": 30}),
                "messages": [],
            }
            self.state[sid]["messages"].append(self.message(sid))

    def message(self, sid):
        state = self.state[sid]
        return {
            "from": "seller",
            "status": "walked_away" if state["closed"] else state["status"],
            "body": "Please review our complete offer.",
            "term_sheets": []
            if state["closed"]
            else [{"term_sheet_id": f"offer:{sid}-{state['version']}", "terms": dict(state["terms"])}],
        }

    def tick(self):
        for sid, state in self.state.items():
            while not state["closed"] and self.clock.now > state["last"] + 60:
                self.timeouts += 1
                state["last"] += 60
                state["timeout_streak"] += 1
                state["version"] += 1
                state["round"] += 1
                state["closed"] = (
                    state["timeout_streak"] >= 3 or state["status"] == "final_offer" or state["round"] >= 8
                )
                state["messages"].append(self.message(sid))

    def open_session(self, case_id):
        assert case_id == self.case["case_id"]
        self.opens += 1
        return {
            "session_id": "offline-clock",
            "case": self.case,
            "threads": {sid: copy.deepcopy(s["messages"]) for sid, s in self.state.items()},
        }

    def session(self, session_id):
        self.tick()
        return {
            "status": "ended" if self.done else "open",
            "result": self.done,
            "threads": {
                sid: {
                    "offer_id": f"offer:{sid}-{s['version']}",
                    "status": s["status"],
                    "closed": s["closed"],
                    "seconds_left": max(0, s["last"] + 60 - self.clock.now),
                }
                for sid, s in self.state.items()
            },
        }

    def thread(self, session_id, sid):
        self.tick()
        return {"messages": copy.deepcopy(self.state[sid]["messages"])}

    def send_message(self, session_id, sid, message):
        from arena_kit.client import ArenaError
        from arena_kit.cost import must_haves_ok, total_cost

        self.tick()
        state = self.state[sid]
        if state["closed"]:
            raise ArenaError(409, "thread ended")
        action = message.get("action", {})
        if action:
            deal = action["type"] == "accept"
            if deal and action.get("term_sheet_id") != f"offer:{sid}-{state['version']}":
                raise ArenaError(422, "not a standing offer")
            self.accepts += deal
            opening = min(
                total_cost(self.case, {**s["terms"], "unit_price": s["opening"]}) for s in self.state.values()
            )
            best = min(
                total_cost(self.case, {**s["terms"], "unit_price": s["floor"], "payment_days": 90})
                for s in self.state.values()
            )
            cost = total_cost(self.case, state["terms"]) if deal else opening
            self.done = {
                "deal": deal,
                "score": max(0, min(1, (opening - cost) / max(0.01, opening - best))),
                "c_deal": cost,
                "must_haves_ok": must_haves_ok(self.case, state["terms"]) if deal else None,
            }
            return {"result": self.done}
        sheets = message.get("term_sheets", [])
        assert sheets and all(must_haves_ok(self.case, s["terms"]) for s in sheets)
        chosen = max(sheets, key=lambda s: s["terms"]["unit_price"])["terms"]
        state["round"] += 1
        target = max(state["floor"], state["opening"] * (1 - 0.04 * state["round"]))
        if self.profile == "discovery":
            # Some suppliers reveal a substantially better feasible position on
            # first contact, while others have almost no discount capacity.
            target = state["floor"]
        state["status"] = (
            "agreed" if chosen["unit_price"] >= target else ("final_offer" if state["round"] >= 5 else "open")
        )
        state["terms"] = {**chosen, "unit_price": round(max(target, chosen["unit_price"]), 2)}
        state["messages"].append({**copy.deepcopy(message), "from": "buyer"})
        # Waiting for this supplier does not spend its own reply budget.
        self.clock.now += self.delay
        state.update(last=self.clock.now, timeout_streak=0, version=state["version"] + 1)
        reply = self.message(sid)
        state["messages"].append(reply)
        return reply


def run(output, seeds=(613, 981), profile="stable", delays=(8, 25, 45, 55)):
    from arena_kit.play import play

    from aeiou.research import package
    from aeiou.runner import RecordingTable
    from aeiou.store import new_run
    from aeiou.strategic import StrategicNegotiator

    rows = []
    for count in (2, 3, 4):
        for delay in delays:
            for seed in seeds:
                client = TimedSuppliers(count, delay, seed, profile)
                with patch("time.monotonic", client.clock):
                    record = new_run(client.case["case_id"], "local", "clock-stress")
                    table = RecordingTable(client, record)
                    result = play(table, client.case["case_id"], StrategicNegotiator(package()))
                rows.append(
                    {
                        "profile": profile,
                        "suppliers": count,
                        "delay_s": delay,
                        "seed": seed,
                        **result,
                        "starts": client.opens,
                        "accepts": client.accepts,
                        "timeouts": client.timeouts,
                        "suppliers_contacted": sum(
                            any(m.get("from") == "buyer" for m in s["messages"]) for s in client.state.values()
                        ),
                        "exploratory_turns": sum(
                            e["type"] == "deadline_explore" for e in record.get("transport_events", [])
                        ),
                        "focused_turns": sum(e["type"] == "deadline_focus" for e in record.get("transport_events", [])),
                    }
                )
    report = {
        "conditions": len(rows),
        "rows": rows,
        "mean_score": mean(r["score"] for r in rows),
        "no_deals": sum(not r["deal"] for r in rows),
        "must_have_violations": sum(r["must_haves_ok"] is False for r in rows),
        "llm_calls": 0,
        "arena_sessions": 0,
        "limitation": (
            "Synthetic timing and concessions, not Arena predictions. "
            "Incumbent protection can lose undiscovered alternatives; exploration can lose incumbent concessions."
        ),
    }
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--agent-source")
    parser.add_argument("--data", default="data/package")
    parser.add_argument("--profile", choices=["stable", "reversed", "discovery"], default="stable")
    parser.add_argument("--seeds", type=int, nargs="+")
    parser.add_argument("--delays", type=float, nargs="+")
    args = parser.parse_args()
    os.environ["ARENA_DATA"] = str(Path(args.data).resolve())
    if args.agent_source:
        sys.path.insert(0, str(Path(args.agent_source).resolve()))
    run(
        args.output,
        seeds=args.seeds
        or (
            (37591, 96017)
            if args.profile == "discovery"
            else ((1259, 8102) if args.profile == "reversed" else (613, 981))
        ),
        profile=args.profile,
        delays=args.delays
        or (
            (20, 40, 55)
            if args.profile == "discovery"
            else ((12, 32, 48) if args.profile == "reversed" else (8, 25, 45, 55))
        ),
    )
