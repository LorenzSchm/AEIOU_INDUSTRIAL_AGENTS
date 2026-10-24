"""Summarise each negotiation transcript as a deal-pipeline card: stage, price path and rounds."""

import json

from .config import state_root

STAGES = ["briefing", "opening", "negotiating", "final_offer", "closed", "no_deal"]


def _price(message):
    # The first package is the sender's lead proposal; later packages are trade-offs around it.
    for sheet in message.get("term_sheets") or []:
        price = (sheet.get("terms") or {}).get("unit_price")
        if isinstance(price, (int, float)):
            return float(price)
    return None


def _lane(supplier_id, messages):
    supplier = [m for m in messages if m.get("from") != "buyer"]
    buyer = [m for m in messages if m.get("from") == "buyer"]
    asks = [p for p in map(_price, supplier) if p is not None]
    bids = [p for p in map(_price, buyer) if p is not None]
    return {
        "supplier_id": supplier_id,
        "asks": asks,
        "bids": bids,
        "rounds": len(buyer),
        "last_status": supplier[-1].get("status") if supplier else None,
    }


def stage_of(run, lanes):
    if run.get("status") == "failed":
        return "no_deal"
    if run.get("status") == "completed":
        return "closed" if (run.get("result") or {}).get("deal") else "no_deal"
    if not any(lane["asks"] for lane in lanes):
        return "briefing"
    if any(lane["last_status"] == "final_offer" for lane in lanes):
        return "final_offer"
    if any(lane["rounds"] for lane in lanes):
        return "negotiating"
    return "opening"


def summarize(run, cases):
    lanes = [_lane(s, m) for s, m in (run.get("threads") or {}).items()]
    case = run.get("case") or cases.get(run["case_id"]) or {}
    buyer = case.get("buyer") or (cases.get(run["case_id"]) or {}).get("buyer") or {}
    result = run.get("result") or {}
    agreed = (result.get("terms") or {}).get("unit_price") if result.get("deal") else None
    # Follow the supplier that agreed, otherwise the one currently asking the least.
    primary = next((lane for lane in lanes if lane["last_status"] == "agreed"), None)
    primary = primary or min((lane for lane in lanes if lane["asks"]), key=lambda lane: lane["asks"][-1], default=None)
    primary = primary or (lanes[0] if lanes else {"supplier_id": None, "asks": [], "bids": [], "rounds": 0})
    final = result.get("c_deal") if result.get("c_deal") is not None else result.get("c_alt")
    return {
        "id": run["id"],
        "case_id": run["case_id"],
        "title": case.get("title", run["case_id"]),
        "type": case.get("type"),
        "mode": run.get("mode"),
        "agent": run.get("agent"),
        "opponent": run.get("opponent"),
        "status": run.get("status"),
        "stage": stage_of(run, lanes),
        "created_at": run.get("created_at"),
        "supplier_id": primary["supplier_id"],
        "suppliers": len(lanes),
        "rounds": primary["rounds"],
        "round_limit": case.get("round_limit"),
        "asks": primary["asks"],
        "bids": primary["bids"],
        "start": primary["asks"][0] if primary["asks"] else None,
        "now": agreed if agreed is not None else (primary["asks"][-1] if primary["asks"] else None),
        "bid": primary["bids"][-1] if primary["bids"] else None,
        "target": buyer.get("current_unit_price"),
        "walk_away": buyer.get("fallback_unit_price"),
        "score": result.get("score"),
        "saved": result["c_open"] - final if result.get("c_open") is not None and final is not None else None,
    }


def pipeline(cases, limit=60):
    folder = state_root() / "runs"
    paths = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    deals = []
    for path in paths:
        try:
            deals.append(summarize(json.loads(path.read_text()), cases))
        except (OSError, ValueError, KeyError):
            continue  # A run can be mid-write; the next poll picks it up.
    return {"stages": STAGES, "deals": deals}
