"""Buyer action boundaries shared by local and hosted transports."""


class NegotiationBlocked(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def validate_buyer_message(message):
    if (message.get("action") or {}).get("type") == "walk_away":
        raise NegotiationBlocked("BUYER_WALK_AWAY_DISABLED")
