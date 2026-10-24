"""Reconstruct tentative supplier preferences from public conversation evidence only."""

from collections import Counter, defaultdict

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left


def supplier_state(case, events):
    """A matched counteroffer is weak evidence, never a revealed utility or price floor.

    Only comparisons between alternatives offered in the same email count. A supplier
    repeating its standard terms without matching an offered package reveals nothing.
    Reconstruct from history so resumed sessions behave like uninterrupted sessions.
    """
    observations, votes = [], defaultdict(Counter)
    previous_buyer, supplier_prices = None, []
    fields = [f for f in case["negotiable"] if f != "unit_price"]
    for index, message in enumerate(events):
        if message.get("from") == "buyer":
            previous_buyer = (index, message)
            continue
        for sheet in message.get("term_sheets", []):
            if sheet.get("terms", {}).get("unit_price"):
                supplier_prices.append(sheet["terms"]["unit_price"])
        if previous_buyer is None:
            continue
        buyer_index, buyer = previous_buyer
        previous_buyer = None  # One response per probe, even if later status messages repeat it.
        offered = buyer.get("term_sheets", [])
        if len(offered) < 2:
            continue
        std = standard_terms(events[: index + 1])
        packages = [closing_terms(case, p.get("terms", {}), std) for p in offered]
        varied = [f for f in fields if len({repr(p.get(f)) for p in packages}) > 1]
        if not varied:
            continue
        # Multiple executable replies do not identify one selected package.
        replies = message.get("term_sheets", [])
        if len(replies) != 1 or message.get("status") not in {"open", "agreed", "final_offer"}:
            continue
        reply = replies[0]
        terms = closing_terms(case, reply.get("terms", {}), std)
        matches = [p for p in packages if all(p.get(f) == terms.get(f) for f in fields)]
        if len(matches) != 1:
            continue
        observation = {
            "buyer_message_index": buyer_index,
            "reply_message_index": index,
            "offer_id": reply.get("term_sheet_id"),
            "kind": "accepted_package" if message.get("status") == "agreed" else "counter_on_package",
            "selected_terms": {f: terms.get(f) for f in varied},
            "confounded": len(varied) > 1,
        }
        observations.append(observation)
        for field in varied:
            votes[field][repr(terms.get(field))] += 1
    preferences = []
    for field, counts in votes.items():
        ranked = counts.most_common()
        value, count = ranked[0]
        tied = len(ranked) > 1 and ranked[1][1] == count
        selected = next(
            o["selected_terms"][field]
            for o in observations
            if field in o["selected_terms"] and repr(o["selected_terms"][field]) == value
        )
        evidence = [
            {k: o[k] for k in ("reply_message_index", "offer_id", "kind", "confounded")}
            for o in observations
            if field in o["selected_terms"] and repr(o["selected_terms"][field]) == value
        ]
        preferences.append(
            {
                "field": field,
                "value": selected,
                "observations": count,
                "confidence": "uncertain" if tied else "tentative",
                "evidence": evidence[-12:],
                "limitation": "Package response can reflect price or other terms, not this field alone.",
            }
        )
    repeated = 0
    for price in reversed(supplier_prices):
        if price != supplier_prices[-1]:
            break
        repeated += 1
    attachments = [
        {"message_index": i, "name": a.get("name", "attachment")}
        for i, m in enumerate(events)
        if m.get("from") != "buyer"
        for a in m.get("attachments", [])
    ]
    return {
        "last_supplier_message_index": max(i for i, m in enumerate(events) if m.get("from") != "buyer"),
        "status": latest(events).get("status"),
        "moves_left": moves_left(case, events),
        "supplier_price_change": round(supplier_prices[-1] - supplier_prices[0], 2) if supplier_prices else None,
        "identical_price_replies": repeated,
        "preferences": preferences,
        "observations": observations[-12:],
        "received_attachments": attachments[-20:],
        "limitation": (
            "Observed package choices are tentative evidence, not acceptance probabilities or private floors."
        ),
    }
