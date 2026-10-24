"""Observable strategy selection, adaptive packages, and bounded two-step lookahead."""

import copy
import math
import time
from dataclasses import asdict, dataclass

from arena_kit.cost import complete_offer, total_cost
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left, standing

from .opponent_model import supplier_utility
from .optimizer import candidate_packages, estimate, valid_terms
from .supplier_signals import recent_kinds


@dataclass(frozen=True)
class StrategicFeatures:
    adaptive_bidding: bool = True
    portfolio: bool = True
    opponent_learning: bool = True
    lookahead: bool = True
    retrieval: bool = True
    grounded_language: bool = True

    def metadata(self):
        return asdict(self)


def select_strategy(case, events, state, belief, enabled=True):
    if not enabled:
        return {"name": "balanced", "reason": "Portfolio disabled for ablation", "anchor": 0.18}
    if moves_left(case, events) <= 2:
        return {"name": "closing", "reason": "Few executable turns remain", "anchor": 0.12}
    signals = recent_kinds(state)
    if "liquidity" in signals:
        return {
            "name": "cash_exchange",
            "reason": "Public supplier statement prioritizes liquidity; unverified preference",
            "anchor": 0.14 if case["buyer"].get("category_goal") in {"stabilise", "secure_supply"} else 0.18,
        }
    if state["identical_price_replies"] >= 3:
        return {"name": "trade_terms", "reason": "Repeated price suggests exploring compensated terms", "anchor": 0.18}
    probable = max(belief["hypotheses"], key=lambda h: h["weight"])
    if belief["observations"] >= 2 and probable["finance"] >= 0.4:
        return {
            "name": "cash_exchange",
            "reason": "Observed package choices favor liquidity hypotheses",
            "anchor": 0.18,
        }
    if case["buyer"].get("category_goal") in {"stabilise", "secure_supply"}:
        return {"name": "continuity", "reason": "Published buyer goal values sustainable supply", "anchor": 0.14}
    return {"name": "balanced", "reason": "Insufficient evidence for specialization", "anchor": 0.18}


def adaptive_packages(case, events, sid, intelligence, state, belief, features):
    strategy = select_strategy(case, events, state, belief, features.portfolio)
    packages = candidate_packages(case, events, sid, intelligence, anchor=strategy["anchor"])
    if not features.adaptive_bidding or not packages:
        if not features.adaptive_bidding:
            packages = candidate_packages(case, events, sid, intelligence)
        return packages, strategy
    # Do not demand faster delivery than the buyer needs when it has no buyer
    # cost benefit. A supplier may charge overtime for that unnecessary demand.
    if "lead_time_days" in case["negotiable"]:
        std = standard_terms(events)
        supplier = estimate(case, sid, std, intelligence)
        need = case["buyer"]["need_lead_time_days"]
        for package in packages:
            for days in sorted(case.get("term_options", {}).get("lead_time_days", []), reverse=True):
                if package["terms"]["lead_time_days"] <= days <= need:
                    terms = {**package["terms"], "lead_time_days": days}
                    cost = total_cost(case, terms, std, supplier)
                    if valid_terms(case, terms) and cost <= package["cost"] + 0.001:
                        package.update(terms=terms, cost=cost)
                        break
    _, current = standing(events)
    rounds = sum(m.get("from") == "buyer" for m in events)
    remaining = moves_left(case, events)
    std = standard_terms(events)
    supplier = estimate(case, sid, std, intelligence)
    previous = [
        complete_offer(case, {**std, **sheet.get("terms", {})})
        for message in events
        if message.get("from") == "buyer"
        for sheet in message.get("term_sheets", [])
    ]
    signals = recent_kinds(state)
    for package in packages:
        terms = package["terms"]
        comparable = [
            p["unit_price"]
            for p in previous
            if all(p.get(f) == terms.get(f) for f in case["negotiable"] if f != "unit_price")
        ]
        if comparable:
            # Switching tactics cannot silently withdraw our previous concession.
            terms["unit_price"] = max(terms["unit_price"], max(comparable))
        if rounds and "price_resistance" in signals:
            # Measured movement toward an observed quote, not a guessed floor.
            gap = max(0, current["unit_price"] - terms["unit_price"])
            terms["unit_price"] = round(terms["unit_price"] + gap * 0.20, 2)
        package["cost"] = total_cost(case, terms, std, supplier)
    # Resolve a complete package before a last price-only counter resets its terms.
    # A stagnant quote alone never justifies matching an opening ask immediately.
    if rounds >= 3 and (remaining <= 2 or state["identical_price_replies"] >= 3):
        std = standard_terms(events)
        supplier = estimate(case, sid, std, intelligence)
        improved = copy.deepcopy(packages)
        closing_price = round(
            current["unit_price"] - min(belief["observed_price_step"], current["unit_price"] * 0.03), 2
        )
        for package in improved:
            package["terms"]["unit_price"] = max(package["terms"]["unit_price"], closing_price)
            package["cost"] = total_cost(case, package["terms"], std, supplier)
        if all(valid_terms(case, p["terms"]) for p in improved):
            strategy = {**strategy, "reason": strategy["reason"] + "; resolve non-price terms at the standing ask"}
            packages = improved
    return packages, strategy


def plan_next_action(case, events, packages, standing_rows, belief, enabled=True, budget_ms=100, supplier=None):
    """Compare actual acceptance with hypothetical send continuations.

    Public price movement provides a range, not a private floor. Hypothesis weights
    and rejection risks are explicit assumptions. This is finite scenario lookahead,
    not an accurate digital twin or an optimality claim.
    """
    started = time.monotonic()
    std = standard_terms(events)
    _, current = standing(events)
    remaining = moves_left(case, events)
    rounds = sum(m.get("from") == "buyer" for m in events)
    safe = [r for r in standing_rows if r["acceptable"]]
    current_cost = min((r["cost"] for r in safe), default=math.inf)
    terminal = latest(events).get("status") in {"agreed", "final_offer"} or remaining <= 0
    if terminal or not packages:
        return {
            "action": "accept" if safe else "walk_away",
            "reason": "Terminal offer or no legal package",
            "traces": [],
        }
    default = {"action": "send", "candidate_ids": [p["id"] for p in packages], "reason": "Controlled package exchange"}
    if not enabled:
        return {**default, "traces": [], "limitation": "Lookahead disabled"}
    traces = []
    for package in packages[:3]:
        if (time.monotonic() - started) * 1000 >= budget_ms:
            return {**default, "traces": traces, "budget_exhausted": True}
        weighted, risk_weight = 0.0, 0.0
        for hypothesis in belief["hypotheses"]:
            utility = supplier_utility(case, package["terms"], hypothesis, std)
            threshold = supplier_utility(case, current, hypothesis, std)
            # Next counter prices bracket an extrapolation of observed concessions.
            for factor in (0.0, 1.0, 2.0):
                predicted = {
                    **package["terms"],
                    "unit_price": max(0.01, current["unit_price"] - factor * belief["observed_price_step"]),
                }
                accepts = utility >= threshold - 1e-6
                response_cost = package["cost"] if accepts else total_cost(case, predicted, std, supplier)
                # If rejected, one more controlled attempt may obtain the predicted package.
                final_cost = response_cost if remaining > 1 else min(current_cost, response_cost)
                if not math.isfinite(final_cost):
                    final_cost = package["cost"] * 1.25
                risk = 0.01 if accepts else (0.08 if remaining <= 2 else 0.02)
                fallback = current_cost if math.isfinite(current_cost) else package["cost"] * 1.25
                value = (1 - risk) * final_cost + risk * fallback
                weight = hypothesis["weight"] / 3
                weighted += weight * value
                risk_weight += weight * risk
        traces.append(
            {"candidate_id": package["id"], "modeled_continuation_cost": weighted, "assumed_loss_risk": risk_weight}
        )
    best = min(traces, key=lambda t: t["modeled_continuation_cost"])
    # Preserve exploration and never accept an opening quote. Accept only when
    # continuation scenarios provide no material economic improvement.
    accept = rounds >= 2 and safe and current_cost <= best["modeled_continuation_cost"] * 1.0005
    return {
        **default,
        "action": "accept" if accept else "send",
        "preferred_candidate": best["candidate_id"],
        "reason": "Standing package beats modeled continuation" if accept else "Continuation retains modeled upside",
        "traces": traces,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "limitation": (
            "Two-step hypothetical response scenarios; supplier utilities and loss risks are uncalibrated assumptions."
        ),
    }
