"""Explicit single-run experiment: rank admitted menus by predicted cost gain.

Requires caller opt-in because predictive gates did not pass. No production
factory registration. Existing economic, terminal and deadline controls remain.
"""

from arena_kit.play import latest, moves_left

from .learned_portfolio import LearnedPortfolioNegotiator, menu_variants
from .response_value import SCHEMA, predict
from .response_value_features import live_row


class ValueSelector:
    def __init__(self, artifact, *, experimental=False):
        if not experimental:
            raise ValueError("Value selection requires explicit experimental opt-in")
        if artifact["schema"] != list(SCHEMA):
            raise ValueError("Feature schema mismatch")
        self.artifact = artifact

    def estimates(self, case, sid, events, packages, variants, body, refs):
        available = {p["id"]: p for p in packages}
        rows = [
            live_row(
                case,
                sid,
                events,
                {"body": body, "refs": refs or [], "term_sheets": [{"terms": available[i]["terms"]} for i in ids]},
            )
            for ids in variants
        ]
        targets = ("quoted_cost_gain", "direct_cost_improved")
        values = {t: predict(self.artifact["models"][t], self.artifact["selections"][t], rows) for t in targets}
        return [{"ids": ids, **{t: float(values[t][i]) for t in targets}} for i, ids in enumerate(variants)]

    def choose(self, case, sid, events, packages, original, body="Thank you", refs=None):
        if latest(events).get("status") != "open" or moves_left(case, events) <= 2:
            return list(original), {"changed": False, "reason": "terminal_or_closing_turn"}
        variants = menu_variants(packages, original)
        if not variants:
            return list(original), {"changed": False, "reason": "no_valid_menu"}
        estimates = self.estimates(case, sid, events, packages, variants, body, refs)
        base = estimates[0]
        # Fixed exploratory bounds, not calibrated confidence guarantees. The exit
        # head has only two events and is intentionally not used to authorize risk.
        eligible = [r for r in estimates if r["direct_cost_improved"] >= base["direct_cost_improved"] - 0.05]
        best = max(eligible, key=lambda r: r["quoted_cost_gain"])
        changed = best["quoted_cost_gain"] >= base["quoted_cost_gain"] + 0.005
        chosen = best["ids"] if changed else list(original)
        return chosen, {
            "changed": chosen != list(original),
            "original_ids": list(original),
            "selected_ids": chosen,
            "menus": estimates,
            "target": "next_primary_quoted_cost_gain",
            "minimum_gain_difference": 0.005,
            "maximum_progress_probability_drop": 0.05,
            "experimental": True,
            "predictive_gates_passed": False,
            "limitation": "Menu predictions are observational; no causal or record guarantee.",
        }


class ValuePortfolioNegotiator(LearnedPortfolioNegotiator):
    def __init__(self, *args, learned_artifact, experimental=False, **kwargs):
        super().__init__(*args, learned_artifact=learned_artifact, **kwargs)
        self.selector = ValueSelector(learned_artifact, experimental=experimental)
