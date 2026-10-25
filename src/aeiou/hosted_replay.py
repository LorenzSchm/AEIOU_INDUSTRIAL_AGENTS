"""Exact recorded exchanges for offline regression, with no invented supplier replies."""

import copy
import hashlib
import json
from collections import Counter


class ReplayDivergenceError(ValueError):
    """The recording contains no supplier response to this new buyer action."""


def public_setup(run):
    """Only information available at session opening; no result or post-run supplier data."""
    case = run.get("case")
    if run.get("mode") != "hosted" or not case or not run.get("threads"):
        raise ValueError("A hosted case and its recorded opening are required")
    threads = {}
    for sid, messages in run["threads"].items():
        opening = []
        for message in messages:
            if message.get("from") == "buyer":
                break
            if "result" in message:
                raise ValueError("Opening messages must not contain post-run results")
            opening.append(copy.deepcopy(message))
        if not opening:
            raise ValueError(f"Missing supplier opening for {sid}")
        threads[sid] = opening
    value = {"case": copy.deepcopy(case), "threads": threads}
    return {
        "schema_version": 1,
        "kind": "hosted-public-setup",
        "source_run_id": run["id"],
        "sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
        **value,
        "parity": {
            "exact": ["public case", "opening messages", "opening binding offers", "captured attachment text"],
            "not_available": ["hidden seller policy", "private constraints", "future counterfactual responses"],
            "clock": "Recorded replay does not reproduce wall-clock deadlines or supplier latency.",
            "score": "The public cost formula can be reused; private supplier economics and the true best attainable "
            "cost are not opening inputs.",
        },
    }


class RecordedTable:
    """Replay identical buyer requests only. Stop on any changed wording, terms or live ID.

    This is a regression fixture, not a simulator and not evidence of model improvement.
    Recorded final scores are available only after the whole recorded path is consumed.
    """

    def __init__(self, run):
        if run.get("mode") != "hosted" or run.get("status") != "completed" or not run.get("result"):
            raise ValueError("Replay requires a completed hosted recording with a result")
        self._run = copy.deepcopy(run)
        self._setup = public_setup(run)
        self._positions = {sid: len(messages) for sid, messages in self._setup["threads"].items()}
        self._opened = False
        counts = {}
        for sid, messages in self._run["threads"].items():
            tail = messages[self._positions[sid] :]
            if len(tail) % 2:
                raise ValueError("Recording lacks complete buyer/reply pairs")
            for index in range(0, len(tail), 2):
                if tail[index].get("from") != "buyer" or tail[index + 1].get("from") == "buyer":
                    raise ValueError("Recording does not alternate buyer requests and supplier replies")
            if tail:
                counts[sid] = len(tail) // 2
        self._order = [decision["supplier_id"] for decision in run.get("decisions", [])]
        if self._order:
            if Counter(self._order) != counts:
                raise ValueError("Recorded decision order does not match the exchanges")
        elif len(counts) == 1:
            sid, count = next(iter(counts.items()))
            self._order = [sid] * count
        else:
            raise ValueError("A recording with multiple contacted suppliers requires its global decision order")
        self._turn = 0

    def open(self, case_id):
        if self._opened:
            raise ValueError("A replay can be opened only once")
        if case_id != self._setup["case"]["case_id"]:
            raise ValueError("Case does not match the recording")
        self._opened = True
        return copy.deepcopy(self._setup["case"]), copy.deepcopy(self._setup["threads"])

    def send(self, supplier_id, message):
        if not self._opened:
            raise ValueError("Open the replay first")
        if supplier_id not in self._positions:
            raise ReplayDivergenceError("No recorded thread for this supplier")
        if self._turn >= len(self._order) or supplier_id != self._order[self._turn]:
            raise ReplayDivergenceError("Supplier order differs from the recorded negotiation")
        position = self._positions[supplier_id]
        messages = self._run["threads"][supplier_id]
        if position >= len(messages):
            raise ReplayDivergenceError("The recorded thread is exhausted")
        expected = {k: v for k, v in messages[position].items() if k != "from"}
        if message != expected:
            raise ReplayDivergenceError(
                "Buyer action differs from the recording; no known supplier reply. "
                "Use a separately labelled synthetic seller to explore this branch."
            )
        self._positions[supplier_id] += 2
        self._turn += 1
        return copy.deepcopy(messages[position + 1])

    def close(self, supplier_id, message):
        reply = self.send(supplier_id, message)
        return reply.get("result", reply)

    def result(self):
        if not self._opened or any(
            self._positions[sid] != len(messages) for sid, messages in self._run["threads"].items()
        ):
            raise ReplayDivergenceError("The final recorded result is unavailable before the full path is replayed")
        return copy.deepcopy(self._run["result"])


def verify_replay(run):
    """Exercise the saved path without an agent backend, network access or arena quota."""
    table = RecordedTable(run)
    table.open(run["case_id"])
    exchanges = 0
    for sid in table._order:
        messages = run["threads"][sid]
        position = table._positions[sid]
        request = {k: v for k, v in messages[position].items() if k != "from"}
        reply = table.send(sid, request)
        if reply != messages[position + 1]:
            raise ValueError("Recorded response changed during replay")
        exchanges += 1
    return {
        "kind": "recorded-path-regression",
        "source_run_id": run["id"],
        "case_id": run["case_id"],
        "exchanges": exchanges,
        "arena_sessions_used": 0,
        "model_calls": 0,
        "recorded_result": table.result(),
        "limitation": "Identical recorded requests only; no new negotiation or counterfactual performance estimate.",
    }
