"""Explicit known-case trial: avoid stalled bids after the successful P1 opening."""

import copy
import json

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left

from .economic_beliefs import total_cost
from .optimizer import alternative_cost, valid_terms
from .reciprocity import same_structure
from .renewal_focus import focused_renewal_case
from .strategic import StrategicNegotiator


def bounded_reciprocal_bid(case, events, cap, fraction=0.25):
    """Move a bounded share of a live comparable gap within an explicit cap."""
    if not 0 < fraction <= 0.5:
        raise ValueError("Concessions must be positive and at most half the gap")
    if latest(events).get("status") != "open" or moves_left(case, events) <= 2:
        return None
    sheets = latest(events).get("term_sheets", [])
    if len(sheets) != 1:
        return None
    quote = {f: sheets[0]["terms"].get(f) for f in case["negotiable"]}
    if not valid_terms(case, quote):
        return None
    previous = [
        s["terms"]
        for m in events
        if m.get("from") == "buyer"
        for s in m.get("term_sheets", [])
        if same_structure(s["terms"], quote)
    ]
    if not previous:
        return None
    last = max(p["unit_price"] for p in previous)
    price = min(cap, round(last + max(0.01, (quote["unit_price"] - last) * fraction), 2))
    if not last < price < quote["unit_price"]:
        return None
    terms = {**quote, "unit_price": price}
    standard = standard_terms(events)
    if not valid_terms(case, terms) or total_cost(case, terms, standard) >= min(
        total_cost(case, quote, standard), alternative_cost(case)
    ):
        return None
    return {"terms": terms, "previous_price": last, "quoted_price": quote["unit_price"]}


class RenewalReciprocityNegotiator(StrategicNegotiator):
    """Not registered in the default factory; reference is supplied by the trial."""

    def __init__(
        self,
        *args,
        reference_run,
        reserve_final_step=False,
        max_bids=5,
        early_fraction=0.25,
        closing_fraction=0.5,
        explain_renewal_basis=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if type(max_bids) is not int or not 5 <= max_bids <= 8:
            raise ValueError("Experimental bid limit must be five to eight")
        if not 0 < early_fraction <= 0.5 or not 0 < closing_fraction <= 0.5:
            raise ValueError("Concession fractions must be positive and at most half the gap")
        self.reference = copy.deepcopy(reference_run)
        self.reserve_final_step = reserve_final_step
        self.max_bids = max_bids
        self.early_fraction = early_fraction
        self.closing_fraction = closing_fraction
        self.explain_renewal_basis = explain_renewal_basis

    def decide(self, case, threads):
        if case["case_id"] != "case:P01":
            raise ValueError("This explicit experiment is restricted to P01")
        if self.case_id != case["case_id"]:
            self.prepare(case)
        old_case = self.reference["case"]
        fields = ("buyer", "negotiable", "fixed_terms", "term_options", "parts", "suppliers")
        if any(case[f] != old_case[f] for f in fields) or len(threads) != 1:
            return super().decide(case, threads)
        sid, events = next(iter(threads.items()))
        current = latest(events)
        if current.get("status") != "open" or moves_left(case, events) <= 2:
            return self.close(case, threads)
        prior = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
        reference_bids = [
            m for m in self.reference["threads"][sid] if m.get("from") == "buyer" and m.get("term_sheets")
        ]
        audit = {"phase": "reference opening", "source_run": self.reference.get("id")}
        if len(prior) < 2:
            # Preserve the successful opening and first payment exchange only.
            source = reference_bids[len(prior)]
            sheets = [{"terms": copy.deepcopy(s["terms"])} for s in source["term_sheets"]]
            if not all(valid_terms(case, s["terms"]) for s in sheets):
                return super().decide(case, threads)
            standard = standard_terms(events)
            live_cost = min(
                (
                    total_cost(case, closing_terms(case, s["terms"], standard), standard)
                    for s in current.get("term_sheets", [])
                ),
                default=float("inf"),
            )
            if any(total_cost(case, s["terms"], standard) >= live_cost for s in sheets):
                return self.close(case, threads)
            if prior and any(s["terms"].get("effective_date") != "2027-01-01" for s in current.get("term_sheets", [])):
                return super().decide(case, threads)
            message = {"subject": "Re: " + case["title"], "body": source["body"], "term_sheets": sheets}
            if self.explain_renewal_basis and not prior:
                _, basis = focused_renewal_case(case, self.pkg)
                if basis and basis["selected_effective_date"] == "2027-01-01":
                    message["body"] += (
                        "\n\nFor clarity on timing: the existing framework term runs until "
                        f"{basis['contract_valid_to']} ({basis['contract_ref']}). Our proposed "
                        "1 January start is the next contractual renewal date. Please distinguish "
                        "that renewal timing from the new concession we offer through accelerated "
                        "14-day payment, and reflect the cash-flow benefit in the renewal price. "
                        "We would like to continue the relationship on a complete, workable package."
                    )
                    message["refs"] = [basis["contract_ref"]]
                    audit["renewal_basis"] = basis
        else:
            if self.reserve_final_step and len(prior) >= self.max_bids:
                return self.close(case, threads)
            cap = max(
                s["terms"]["unit_price"]
                for m in reference_bids
                for s in m["term_sheets"]
                if s["terms"].get("payment_days") == 14 and s["terms"].get("effective_date") == "2027-01-01"
            )
            fraction = self.early_fraction
            if self.reserve_final_step and len(prior) >= 4:
                result = self.reference.get("result") or {}
                agreed = result.get("terms", {})
                if (
                    not result.get("deal")
                    or agreed.get("payment_days") != 14
                    or agreed.get("effective_date") != "2027-01-01"
                ):
                    return self.close(case, threads)
                # Bounded closing exchanges, still strictly cheaper than the
                # historical agreement and the current comparable quote.
                cap = round(agreed["unit_price"] - 0.01, 2)
                fraction = self.closing_fraction
            counter = bounded_reciprocal_bid(case, events, cap, fraction)
            if not counter:
                return self.close(case, threads)
            if counter["terms"].get("payment_days") != 14 or counter["terms"].get("effective_date") != "2027-01-01":
                return super().decide(case, threads)
            body = (
                "Dear Mr Brandlhuber,\n\nThank you for your movement and for working towards a "
                "renewal together. We respect that your detailed costing is confidential. "
                f"We can increase our price proposal from EUR {counter['previous_price']:.2f} "
                f"to EUR {counter['terms']['unit_price']:.2f}, keeping payment at 14 days and "
                "the start on 1 January 2027. This is a further step from us, conditional on "
                "the complete package. Could you reciprocate with your closest workable price "
                "on these same terms so we can conclude?\n\nExecutable package: " + json.dumps(counter["terms"])
            )
            message = {
                "subject": "Reciprocal step towards the renewal",
                "body": body,
                "term_sheets": [{"terms": counter["terms"]}],
            }
            audit = {
                "phase": "bounded reciprocal concession",
                "cap": cap,
                "gap_fraction": fraction,
                "reserve_final_step": self.reserve_final_step,
                **counter,
            }
        move = {"supplier_id": sid, "message": message}
        self.decisions.append(
            {
                "supplier_id": sid,
                "action": "send",
                "elapsed_s": 0,
                "source": "explicit renewal reciprocity trial",
                "reciprocity_trial": audit,
                "actual_sent_terms": copy.deepcopy(message["term_sheets"]),
            }
        )
        self.runtime_context = {}
        return move
