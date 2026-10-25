"""Prefix-only training examples from hosted conversations, never simulator rewards.

Labels describe the immediate observed response to the entire offered menu. They
are not per-package acceptance probabilities or counterfactual policy values.
"""

import hashlib
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean

from arena_kit.cost import closing_terms, total_cost
from arena_kit.docs import standard_terms

FEATURES = (
    "round_fraction",
    "menu_size",
    "min_price_gap",
    "max_price_gap",
    "price_spread",
    "min_payment_delta",
    "max_payment_delta",
    "min_date_delta",
    "max_date_delta",
    "min_duration_delta",
    "max_duration_delta",
    "min_index_delta",
    "max_index_delta",
    "best_direct_cost_gap",
    "prior_supplier_concession",
    "buyer_price_movement",
    "supplier_asks_evidence",
    "supplier_price_resistance",
    "supplier_cash_concern",
    "supplier_closing_warning",
    "buyer_supplies_refs",
    "buyer_warm_acknowledgment",
)
TARGETS = ("package_matched", "direct_cost_improved")
TERMINAL = {"final", "final_offer", "agreed", "walked_away", "no_bid"}


def _sheets(message):
    return [s["terms"] for s in message.get("term_sheets", []) if s.get("terms", {}).get("unit_price")]


def _index(t):
    clause = t.get("index_clause") or {}
    return clause.get("material_share", 0) / (1 + clause.get("deadband_pct", 0) / 10)


def _date_delta(a, b):
    # Quote attachments serialize absent dates as the literal string "none".
    if not a or not b or str(a).lower() in {"none", "null"} or str(b).lower() in {"none", "null"}:
        return 0.0
    return (date.fromisoformat(a) - date.fromisoformat(b)).days / 365


def features(case, prefix, proposal):
    """Only public case fields, prior messages and the proposed outgoing menu."""
    quotes = [m for m in prefix if m.get("from") != "buyer" and _sheets(m)]
    if not quotes or not _sheets(proposal):
        raise ValueError("A standing price and an outgoing menu are required")
    std = standard_terms(prefix)
    reference = closing_terms(case, _sheets(quotes[-1])[0], std)
    packages = [closing_terms(case, p, std) for p in _sheets(proposal)]
    price = reference["unit_price"]
    direct = total_cost(case, reference, std)
    prices = [(p["unit_price"] - price) / price for p in packages]
    payment = [(p["payment_days"] - reference["payment_days"]) / 90 for p in packages]
    dates = [_date_delta(p.get("effective_date"), reference.get("effective_date")) for p in packages]
    duration = [(p["contract_months"] - reference["contract_months"]) / 36 for p in packages]
    index = [_index(p) - _index(reference) for p in packages]
    prior_step = 0.0
    if len(quotes) > 1:
        previous = total_cost(case, _sheets(quotes[-2])[0], std)
        prior_step = (previous - direct) / max(previous, 1)
    buyer = [m for m in prefix if m.get("from") == "buyer" and _sheets(m)]
    movement = 0.0
    if buyer:
        movement = (mean(p["unit_price"] for p in packages) - mean(p["unit_price"] for p in _sheets(buyer[-1]))) / price
    text = quotes[-1].get("body", "").lower()
    outgoing = proposal.get("body", "").lower()
    values = [
        sum(m.get("from") == "buyer" for m in prefix) / max(case.get("round_limit", 10), 1),
        len(packages) / 3,
        min(prices),
        max(prices),
        max(prices) - min(prices),
        min(payment),
        max(payment),
        min(dates),
        max(dates),
        min(duration),
        max(duration),
        min(index),
        max(index),
        (min(total_cost(case, p, std) for p in packages) - direct) / max(direct, 1),
        prior_step,
        movement,
        bool(re.search(r"send.*(?:data|figures)|which figures|supporting evidence", text)),
        bool(re.search(r"price.*(?:frozen|final)|no further|not feasible|unrealistic", text)),
        bool(re.search(r"cash flow|liquidity|liquidit|faster payment", text)),
        bool(re.search(r"final offer|withdraw|last offer|letztes angebot", text)),
        bool(proposal.get("refs")),
        bool(re.search(r"thank|appreciate|danke", outgoing)),
    ]
    if len(values) != len(FEATURES) or not all(math.isfinite(float(x)) for x in values):
        raise ValueError("Invalid features")
    return [max(-3.0, min(3.0, float(x))) for x in values]


def extract_run(run):
    """Exclude action-only closes, terminal over-negotiation and transport recoveries."""
    rows, excluded = [], Counter()
    case = run.get("case")
    if run.get("mode") != "hosted" or not run.get("session_id") or not case:
        return rows, {"not_hosted_or_missing_case": 1}
    for sid, events in run.get("threads", {}).items():
        for i, outgoing in enumerate(events):
            if outgoing.get("from") != "buyer" or outgoing.get("action") or not _sheets(outgoing):
                continue
            prefix = events[:i]
            previous = next((m for m in reversed(prefix) if m.get("from") != "buyer"), {})
            if previous.get("status") in TERMINAL:
                excluded["proposal_after_terminal"] += 1
                continue
            if i + 1 >= len(events):
                excluded["missing_response"] += 1
                continue
            response = events[i + 1]
            if response.get("from") in {"buyer", "arena"} or previous.get("from") == "arena":
                excluded["transport_or_unpaired_response"] += 1
                continue
            # A recorded refresh/timeout is not a clean response to this action.
            if "timeout" in response.get("body", "").lower():
                excluded["timeout_response"] += 1
                continue
            try:
                vector = features(case, prefix, outgoing)
                std = standard_terms(prefix)  # never future attachment terms
                proposals = _sheets(outgoing)
                replies = (
                    []
                    if response.get("status") in {"walked_away", "no_bid"}
                    else [closing_terms(case, t, std) for t in _sheets(response)]
                )
                # Match ALL explicitly offered negotiable fields. Other menu choices
                # are not negative examples: the supplier can choose only one.
                matched = any(all(r.get(k) == v for k, v in p.items()) for p in proposals for r in replies)
                old = min(total_cost(case, p, std) for p in _sheets(previous))
                improved = bool(replies) and min(total_cost(case, r, std) for r in replies) < old - 0.01
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                excluded["invalid_or_incomplete_terms"] += 1
                continue
            rows.append(
                {
                    "session_id": run["session_id"],
                    "case_id": case["case_id"],
                    "supplier_id": sid,
                    "turn": i,
                    "x": vector,
                    "y": {"package_matched": int(matched), "direct_cost_improved": int(improved)},
                    "response_status": response.get("status"),
                }
            )
    return rows, dict(excluded)


def assign_groups(rows):
    """Connected case/supplier components prevent supplier leakage across cases."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    for row in rows:
        a, b = find("case:" + row["case_id"]), find("supplier:" + row["supplier_id"])
        parent[max(a, b)] = min(a, b)
    for row in rows:
        row["group"] = find("case:" + row["case_id"])
    return rows


def load_dataset(folder):
    runs, excluded = {}, Counter()
    for path in sorted(Path(folder).glob("*.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            excluded["unreadable"] += 1
            continue
        if run.get("mode") != "hosted" or not run.get("session_id") or not run.get("case"):
            excluded["not_hosted_or_missing_case"] += 1
            continue
        key = run["session_id"]

        def count(r):
            return sum(len(ev) for ev in r.get("threads", {}).values())

        if key in runs:
            excluded["duplicate_session"] += 1
        if key not in runs or count(run) > count(runs[key]):
            runs[key] = run
    rows = []
    for run in runs.values():
        examples, skipped = extract_run(run)
        rows.extend(examples)
        excluded.update(skipped)
    rows.sort(key=lambda r: (r["session_id"], r["supplier_id"], r["turn"]))
    assign_groups(rows)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return rows, {"hosted_sessions": len(runs), "examples": len(rows), "excluded": dict(excluded), "sha256": digest}
