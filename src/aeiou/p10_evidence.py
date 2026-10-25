"""Scoped P10 purchase history and argument cards from the frozen buyer dataset."""

from .forecast_evidence import commitment_comparison


def enrich_p10(case, pkg, intelligence):
    if (
        case.get("case_id") != "case:P10"
        or case.get("suppliers") != ["sup:70410"]
        or case.get("parts") != ["part:340010"]
    ):
        return
    facts, tables = intelligence["facts"], intelligence["tables"]
    orders = sorted(tables.get("po_lines", []), key=lambda row: (row["order_date"], row["id"]))
    for row in orders:
        if fact := pkg.lookup(row["id"]):
            facts[row["id"]] = fact
    recent = [r for r in orders if "2025-10-01" <= r["order_date"] < "2026-10-01"]
    calculations = intelligence.setdefault("calculations", [])

    def calculate(name, value, description, refs):
        calculations.append(
            {"id": f"p10:{name}", "value": round(value, 4), "description": description, "source_refs": refs}
        )

    refs = [r["id"] for r in recent]
    # Canonical facts must actually exist before an aggregate becomes citable.
    if refs and all(ref in facts for ref in refs):
        qty = sum(r["qty"] for r in recent)
        calculate(
            "ordered_last_12m_units",
            qty,
            "Units ordered for this supplier/part, 2025-10 through 2026-09; not receipts or consumption",
            refs,
        )
        if qty:
            calculate(
                "commitment_vs_orders_pct",
                (case["buyer"]["need_per_year"] / qty - 1) * 100,
                "Annual case need versus preceding 12 months of orders; not forecast growth",
                ["case", *refs],
            )
    latest = orders[-2:]
    groups = []
    if len(latest) == 2 and all(row["id"] in facts for row in latest):
        prices = [row["price"] / (row.get("price_unit") or 1) for row in latest]
        if prices[0] == prices[1]:
            refs = [r["id"] for r in latest]
            groups.append(
                {
                    "id": "recent_order_prices",
                    "source_refs": refs,
                    "sentence": f"Our orders for this seal on {latest[0]['order_date']} and "
                    f"{latest[1]['order_date']} were both at EUR {prices[0]:.2f}/unit "
                    f"({', '.join(refs)}).",
                    "numeric_claims": [],
                }
            )
    if "cost:340010" in facts:
        groups.append(
            {
                "id": "buyer_should_cost",
                "source_refs": ["cost:340010"],
                "interpretation": "Buyer estimate excluding margin, not a proven supplier floor; "
                "use the recorded value and uncertainty, not a profitability assertion.",
            }
        )
    if "sc:70410@2026Q3" in facts:
        groups.append(
            {
                "id": "delivery_performance",
                "source_refs": ["sc:70410@2026Q3"],
                "interpretation": "Supplier-wide rolling on-time delivery, not this part's delivery rate. "
                "A past service level is not proof of future capacity.",
            }
        )
    intelligence["p10_evidence"] = {
        "scope": "Supplier 70410 / part 340010; frozen practice data through September 2026",
        "order_refs": [r["id"] for r in orders if r["id"] in facts],
        "opening_arguments": groups,
        "interpretation": [
            "Use at most three evidence groups per email, with one argument per sentence.",
            "PO change_pct is not a unit-price change. Compare normalized prices explicitly.",
            "Annual LCI and PPI increased. They support rising inputs and do not establish a required price cut.",
            "Keep the forecast, authorized annual need and past orders distinct; compare their periods in "
            "demand_preparation before making a volume argument.",
            "The allocation news refers to past call-offs. Full future allocation remains to be negotiated.",
            "Do not ask for a best/final quote merely to obtain an ordinary counteroffer.",
        ],
    }
    preparation = intelligence["p10_evidence"]
    snapshots = intelligence.get("latest_forecasts", [])
    if snapshot := next((s for s in snapshots if s["part_id"] == case["parts"][0]), None):
        preparation["demand_preparation"] = {
            "forecast_ref": snapshot["forecast"]["id"],
            "source_refs": ["case", *snapshot["source_refs"]],
            "forecast_rows_complete": snapshot["coverage"]["complete"],
            **(commitment_comparison(case, snapshot) or {}),
        }
    else:
        preparation["demand_preparation"] = {
            "status": "unavailable",
            "source_refs": ["case"],
            "interpretation": "No scoped forecast is available; do not invent a plan.",
        }
    preparation["unresolved_from_buyer_records"] = [
        {
            "topic": "capacity_tradeoff",
            "source_refs": [case["trigger_doc"]] if case.get("trigger_doc") in facts else [],
            "gap": "The supplied records do not quantify available capacity or the allocation/price concession "
            "for a longer commitment. Reconcile with current supplier replies; "
            "priority is not guaranteed capacity.",
        },
        {
            "topic": "current_approval_authority",
            "source_refs": ["doc:20260310-70410-M1"] if "doc:20260310-70410-M1" in facts else [],
            "gap": "The March approval process is historical. Current authority is not confirmed by these buyer "
            "records; use current supplier replies and do not assume committee escalation is available.",
        },
    ]
