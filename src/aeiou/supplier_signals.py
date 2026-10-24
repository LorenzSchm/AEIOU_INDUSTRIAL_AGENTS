"""Bounded interpretations of public messages; never binding facts or private floors."""

import re

PRICE_FREEZE = (
    r"(?:pric\w*|reductions?).{0,45}(?:frozen|freez\w*|on hold)|"
    r"(?:frozen|freez\w*).{0,45}(?:pric\w*|reductions?)"
)
KINDS = {
    "price_resistance",
    "price_freeze",
    "liquidity",
    "continuity",
    "delivery_cost",
    "approval_required",
    "closing_warning",
}
PATTERNS = {
    "price_freeze": PRICE_FREEZE,
    "price_resistance": (
        r"not feasible|unrealistic|serious proposal|far apart|no constructive movement|"
        r"movement is still too small|do not represent a step forward|"
        r"(?:only|barely) moved (?:a few cents|your price)|prices do not reflect"
    ),
    "liquidity": r"cash flow|liquidity|liquidit|cash moving",
    "continuity": r"extended commitment|longer commitment|stability.*contract|planning security",
    "delivery_cost": r"overtime|at a premium|expedit",
    "approval_required": r"approval|authorize|authorise|management sign",
    "closing_warning": r"final offer|last offer|letztes angebot|no constructive movement",
}


def observed_signals(events):
    signals = []
    for index, message in enumerate(events):
        if message.get("from") == "buyer":
            continue
        body = message.get("body", "")
        for kind, pattern in PATTERNS.items():
            if match := re.search(pattern, body, re.I):
                signals.append(
                    {"kind": kind, "quote": match.group(), "message_index": index, "source": "public_text_rule"}
                )
    return signals[-18:]


def validated_interpretation(data, body, index):
    if not isinstance(data, dict) or not isinstance(data.get("signals"), list):
        return []
    result = []
    for signal in data["signals"][:6]:
        if not isinstance(signal, dict):
            continue
        kind, quote = signal.get("kind"), signal.get("quote")
        if (
            isinstance(kind, str)
            and kind in KINDS
            and isinstance(quote, str)
            and 8 <= len(quote) <= 250
            and quote in body
        ):
            result.append({"kind": kind, "quote": quote, "message_index": index, "source": "llm_interpretation"})
    return result


def recent_kinds(state):
    signals = state.get("signals", [])
    last = state.get("last_supplier_message_index", max((s["message_index"] for s in signals), default=-1))
    return {s["kind"] for s in signals if s["message_index"] == last}
