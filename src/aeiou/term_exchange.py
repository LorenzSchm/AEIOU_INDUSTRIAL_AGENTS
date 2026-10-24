"""Traceable single-term experiments from public offers, not hidden supplier data."""

from arena_kit.cost import complete_offer
from arena_kit.docs import standard_terms


def exchange_history(case, events):
    """Count only replies to a pair differing in one non-price term.

    Price movement is an association, not proof the term caused the concession.
    Ambiguous, bundled and unmatched replies are deliberately excluded.
    """
    fields = [f for f in case["negotiable"] if f != "unit_price"]
    observations = []
    pending, previous_price = None, None
    for index, message in enumerate(events):
        std = standard_terms(events[: index + 1])
        sheets = [complete_offer(case, {**std, **s.get("terms", {})}) for s in message.get("term_sheets", [])]
        if message.get("from") == "buyer":
            pending = sheets
            continue
        if len(sheets) == 1 and message.get("status") in {"open", "agreed", "final_offer"}:
            reply = sheets[0]
            if pending and previous_price is not None:
                matches = [p for p in pending if all(p.get(f) == reply.get(f) for f in fields)]
                if len(matches) == 1:
                    chosen = matches[0]
                    for other in pending:
                        changed = [f for f in fields if chosen.get(f) != other.get(f)]
                        if len(changed) == 1:
                            field = changed[0]
                            observation = {
                                "field": field,
                                "value": chosen[field],
                                "price_improvement": round(previous_price - reply["unit_price"], 4),
                                "message_index": index,
                                "limitation": "Observed association; price and negotiation timing also changed.",
                            }
                            if observation not in observations:
                                observations.append(observation)
            previous_price = reply["unit_price"]
        else:
            previous_price = None
        pending = None
    return observations


def ranked_exchanges(case, base, observations):
    """Prefer observed reciprocal moves; retire twice-unrewarded term values."""
    options = []
    for field in ("payment_days", "contract_months", "effective_date", "incoterm"):
        if field not in case["negotiable"]:
            continue
        for value in case.get("term_options", {}).get(field, []):
            if value == base.get(field):
                continue
            evidence = [o for o in observations if o["field"] == field and o["value"] == value]
            recent = evidence[-2:]
            if len(recent) == 2 and all(o["price_improvement"] <= 0 for o in recent):
                continue
            gain = sum(o["price_improvement"] for o in evidence) / len(evidence) if evidence else 0
            options.append({"field": field, "value": value, "evidence": evidence, "observed_gain": gain})
    return sorted(options, key=lambda o: (-o["observed_gain"], len(o["evidence"])))
