"""Dated buyer records as numeric context, never fabricated negotiation outcomes."""

import hashlib
import json
import math
from datetime import date, timedelta

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms

from .response_learning import _sheets
from .retrieval import evidence_cutoff

CONTEXT_FEATURES = (
    "erp_orders_available",
    "erp_quote_vs_order_price",
    "erp_proposal_vs_order_price",
    "erp_order_price_change",
    "erp_commitment_vs_order_volume",
    "erp_scorecard_available",
    "erp_otd_rate",
    "erp_quality_ppm_log",
    "erp_financials_available",
    "erp_equity_ratio",
    "erp_credit_change",
    "erp_indices_available",
    "erp_weighted_annual_index_change",
    "erp_weighted_quarter_index_change",
    "erp_contract_available",
    "erp_payment_vs_contract",
)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def snapshot_context(case, supplier_id, intelligence):
    """Use case-scoped frozen package records at/before its evidence cutoff.

    Undated rows are not observations. Supplier financials require a completed
    prior fiscal year. Quotes, should-cost estimates and post-run scores are not read.
    """
    cutoff = evidence_cutoff(case)[:10]
    start = (date.fromisoformat(cutoff) - timedelta(days=365)).isoformat()
    tables = intelligence.get("tables", {})
    parts = set(case.get("parts", []))
    sources = []

    def keep(table, rows):
        for r in rows:
            sources.append({"table": table, "ref": r.get("id") or supplier_id, "row": r})
        return rows

    orders = keep(
        "po_lines",
        sorted(
            [
                r
                for r in tables.get("po_lines", [])
                if r.get("supplier_id") == supplier_id
                and r.get("part_id") in parts
                and start < (r.get("order_date") or "") <= cutoff
                and r.get("currency") == "EUR"
                and finite(r.get("price"))
                and r["price"] > 0
                and finite(r.get("qty"))
                and r["qty"] > 0
                and finite(r.get("price_unit", 1))
                and r.get("price_unit", 1) > 0
            ],
            key=lambda r: (r["order_date"], r["id"]),
        ),
    )
    scorecards = sorted(
        [
            r
            for r in tables.get("scorecards", [])
            if r.get("supplier_id") == supplier_id
            and start < (r.get("period_end") or "") <= cutoff
            and finite(r.get("otd_pct"))
            and finite(r.get("ppm"))
            and r["ppm"] >= 0
        ],
        key=lambda r: (r["period_end"], r["id"]),
    )
    scorecard = keep("scorecards", scorecards[-1:])
    financials = sorted(
        [
            r
            for r in tables.get("supplier_intel", [])
            if r.get("supplier_id") == supplier_id
            and type(r.get("filed_fy")) is int
            and 0 < r["filed_fy"] < int(cutoff[:4])
            and finite(r.get("equity_ratio_pct"))
            and finite(r.get("credit_index"))
            and finite(r.get("credit_index_12m_ago"))
        ],
        key=lambda r: r["filed_fy"],
    )
    financial = keep("supplier_intel", financials[-1:])
    contracts = sorted(
        [
            r
            for r in tables.get("contracts", [])
            if r.get("supplier_id") == supplier_id
            and r.get("valid_from")
            and r.get("valid_to")
            and r["valid_from"] <= cutoff <= r["valid_to"]
            and finite(r.get("payment_days"))
        ],
        key=lambda r: (r["valid_from"], r["id"]),
    )
    contract = keep("contracts", contracts[-1:])
    # A driver weight must be explicitly dated at/before cutoff, or its provenance is insufficient.
    import re

    drivers = [
        r
        for r in tables.get("cost_drivers", [])
        if r.get("part_id") in parts
        and finite(r.get("weight"))
        and 0 < r["weight"] <= 1
        and (stamp := re.search(r"\d{4}-\d{2}", r.get("source", "")))
        and stamp.group() <= cutoff[:7]
    ]
    annual, quarter, weights = 0.0, 0.0, 0.0
    for driver in drivers:
        points = {
            r["month"]: r
            for r in tables.get("index_values", [])
            if r.get("series_id") == driver["series_id"]
            and r.get("month")
            and r["month"] <= cutoff[:7]
            and finite(r.get("value"))
            and r["value"] > 0
        }
        if not points:
            continue
        end = max(points)
        number = int(end[:4]) * 12 + int(end[5:]) - 1
        cutoff_number = int(cutoff[:4]) * 12 + int(cutoff[5:7]) - 1
        if cutoff_number - number > 2:
            continue
        months = [f"{n // 12:04d}-{n % 12 + 1:02d}" for n in (number - 12, number - 3)]
        if any(m not in points for m in months):
            continue
        annual += driver["weight"] * (points[end]["value"] / points[months[0]]["value"] - 1)
        quarter += driver["weight"] * (points[end]["value"] / points[months[1]]["value"] - 1)
        weights += driver["weight"]
        keep("cost_drivers", [driver])
        keep("index_values", [points[m] for m in [*months, end]])
    volume = sum(r["qty"] for r in orders)
    # Different part units must not be added together. Current cases use one part.
    if len(parts) != 1 or len({r.get("uom") for r in orders}) != 1:
        orders, volume = [], 0
    price = lambda r: r["price"] / r.get("price_unit", 1)  # noqa: E731
    data = {
        "order_price": sum(r["qty"] * price(r) for r in orders) / volume if volume else None,
        "order_volume": volume or None,
        "order_price_change": price(orders[-1]) / price(orders[0]) - 1 if orders else None,
        "otd_rate": scorecard[0]["otd_pct"] / 100 if scorecard else None,
        "quality_ppm_log": math.log1p(scorecard[0]["ppm"]) / 10 if scorecard else None,
        "equity_ratio": financial[0]["equity_ratio_pct"] / 100 if financial else None,
        "credit_change": (financial[0]["credit_index"] - financial[0]["credit_index_12m_ago"]) / 100
        if financial
        else None,
        "annual_index_change": annual if weights else None,
        "quarter_index_change": quarter if weights else None,
        "contract_payment_days": contract[0]["payment_days"] if contract else None,
    }
    encoded = json.dumps({"cutoff": cutoff, "sources": sources}, sort_keys=True).encode()
    return {
        "cutoff": cutoff,
        "values": data,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "source_refs": [{k: s[k] for k in ("table", "ref")} for s in sources],
        "source_count": len(sources),
        "meaning": "Frozen buyer records, not supplier private costs or guaranteed response predictors. "
        "Index weights are buyer estimates; supplier scorecards cover the supplier account.",
    }


def context_features(case, prefix, proposal, context):
    v = context["values"]
    std = standard_terms(prefix)
    current = next(m for m in reversed(prefix) if m.get("from") != "buyer" and _sheets(m))
    quote = closing_terms(case, _sheets(current)[0], std)
    menu = [closing_terms(case, p, std) for p in _sheets(proposal)]
    order_price, volume = v["order_price"], v["order_volume"]
    values = [
        float(order_price is not None),
        quote["unit_price"] / order_price - 1 if order_price else 0,
        min(p["unit_price"] for p in menu) / order_price - 1 if order_price else 0,
        v["order_price_change"] or 0,
        min(p["volume_per_year"] for p in menu) / volume - 1 if volume else 0,
        float(v["otd_rate"] is not None),
        v["otd_rate"] or 0,
        v["quality_ppm_log"] or 0,
        float(v["equity_ratio"] is not None),
        v["equity_ratio"] or 0,
        v["credit_change"] or 0,
        float(v["annual_index_change"] is not None),
        v["annual_index_change"] or 0,
        v["quarter_index_change"] or 0,
        float(v["contract_payment_days"] is not None),
        (min(p["payment_days"] for p in menu) - v["contract_payment_days"]) / 90
        if v["contract_payment_days"] is not None
        else 0,
    ]
    if len(values) != len(CONTEXT_FEATURES) or not all(math.isfinite(x) for x in values):
        raise ValueError("Invalid business-context features")
    return [max(-3, min(3, float(x))) for x in values]
