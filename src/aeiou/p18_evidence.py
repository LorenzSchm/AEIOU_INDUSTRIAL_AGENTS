"""Buyer-visible P18 records and reproducible arithmetic, never supplier private state."""

from collections import defaultdict
from datetime import date


def enrich_p18(case, pkg, intelligence):
    if (
        case.get("case_id") != "case:P18"
        or case.get("suppliers") != ["sup:70740"]
        or case.get("parts") != ["part:370010"]
    ):
        return
    facts, tables = intelligence["facts"], intelligence["tables"]
    calculations = intelligence.setdefault("calculations", [])

    def register(ref):
        if value := pkg.lookup(ref):
            facts[ref] = value
        return facts.get(ref)

    def calculate(name, value, description, refs):
        calculations.append(
            {"id": f"p18:{name}", "value": round(value, 4), "description": description, "source_refs": refs}
        )

    # Canonical PO facts already include date, quantity, part, supplier and normalized
    # price fields. Keep their IDs in the dossier instead of duplicating all raw rows.
    orders = sorted(tables["po_lines"], key=lambda row: (row["order_date"], row["id"]))
    for row in orders:
        register(row["id"])
    forecasts = sorted(tables["forecasts"], key=lambda row: row["snapshot_month"], reverse=True)
    tables["forecasts"] = forecasts
    for row in forecasts:
        register(row["id"])
    if forecasts:
        tables["forecast_lines"] = sorted(
            [r for r in tables["forecast_history_lines"] if r["fc_id"] == forecasts[0]["id"]],
            key=lambda row: row["target_month"],
        )
    for series in ("AS-14404", "PPI-C2431", "LCI-C"):
        for row in tables["index_values"]:
            if row["series_id"] != series or row["month"] < "2025-06":
                continue
            register(row["id"])
    contract = "ctr:4600070740"
    if contract not in {doc["id"] for doc in intelligence["documents"]}:
        intelligence["documents"].append({"id": contract, "text": pkg.document(contract)})
    for ref in pkg.facts:
        if ref.startswith(contract + "#"):
            register(ref)

    recent = [r for r in orders if "2025-10-01" <= r["order_date"] < "2026-10-01"]
    volume = sum(r["qty"] for r in recent)
    refs = [r["id"] for r in recent]
    calculate(
        "orders_last_12m_kg",
        volume,
        "Ordered kg, Werdenfels / part 370010, 2025-10-01 through 2026-09-30; not consumption",
        refs,
    )
    calculate("orders_last_12m_lines", len(recent), "Number of PO lines in that same period", refs)
    if volume:
        calculate(
            "commitment_vs_orders_pct",
            (case["fixed_terms"]["volume_per_year"] / volume - 1) * 100,
            "Fixed annual case commitment versus preceding 12 months of orders; not forecast growth",
            ["case", *refs],
        )
    quarters = defaultdict(list)
    for row in recent:
        year, month = row["order_date"][:4], int(row["order_date"][5:7])
        quarters[f"{year}Q{(month - 1) // 3 + 1}"].append(row)
    for quarter, rows in sorted(quarters.items()):
        calculate(
            f"orders_{quarter}_kg",
            sum(r["qty"] for r in rows),
            f"Ordered kg in {quarter}, same supplier and part",
            [r["id"] for r in rows],
        )

    price_refs = ["po:4500014022-10", "po:4500014078-10", "po:4500014355-10", "po:4500014427-10"]
    old, new = facts[price_refs[0]], facts[price_refs[-1]]
    calculate(
        "order_price_change_pct",
        ((new["unit_price"] / new["price_unit"]) / (old["unit_price"] / old["price_unit"]) - 1) * 100,
        "Change in normalized ordered price, June to September 2026; not PO change_pct",
        price_refs,
    )
    arguments = [
        {
            "id": "order_prices",
            "source_refs": price_refs,
            "sentence": "Our purchase orders with you for part:370010 on 2 and 17 June 2026 were at EUR 5.26/kg "
            "(po:4500014022-10, po:4500014078-10), compared with EUR 5.84/kg on 1 and 18 September "
            "2026 (po:4500014355-10, po:4500014427-10).",
            "numeric_claims": [],
        }
    ]
    for name, series, start, end in [
        ("as_recent_change_pct", "AS-14404", "2026-06", "2026-09"),
        ("ppi_recent_change_pct", "PPI-C2431", "2026-06", "2026-08"),
    ]:
        refs = [f"idx:{series}@{start}", f"idx:{series}@{end}"]
        change = (facts[refs[1]]["value"] / facts[refs[0]]["value"] - 1) * 100
        calculate(name, change, f"{series} percentage change {start} to {end}; not an annual change", refs)
        sentence = f"{series} changed by {change:+.2f}% from {start} to {end} ({', '.join(refs)})."
        arguments.append(
            {
                "id": name,
                "source_refs": refs,
                "sentence": sentence,
                "numeric_claims": [
                    {"calculation_id": f"p18:{name}", "value": round(change, 2), "quote": f"changed by {change:+.2f}%"}
                ],
            }
        )

    # Only case-authorized quotations can be used as competitor evidence. Same-part
    # quotations elsewhere in the package must not leak back into P18's price argument.
    eligible_quotes = {ref for ref in case.get("refs", []) if ref.startswith("quote:")}
    alternative = case.get("buyer", {}).get("alternative", {}).get("ref")
    if alternative and alternative.startswith("quote:"):
        eligible_quotes.add(alternative)
    excluded_quotes = [r["id"] for r in tables.get("quotes", []) if r["id"] not in eligible_quotes]
    tables["quotes"] = [r for r in tables.get("quotes", []) if r["id"] in eligible_quotes]
    for ref in excluded_quotes:
        facts.pop(ref, None)

    intelligence["p18_evidence"] = {
        "scope": "Public buyer records for sup:70740 / part:370010; company forecasts labelled separately",
        "order_refs": [r["id"] for r in orders],
        "opening_arguments": arguments,
        "volume_window": {"from": "2025-10-01", "through": "2026-09-30", "source_refs": [r["id"] for r in recent]},
        "eligible_quote_refs": sorted(eligible_quotes),
        "excluded_quote_refs": excluded_quotes,
        "interpretation": [
            "Use at most three checked argument groups per email; one group per sentence. Track used IDs.",
            "PO quantities are ordered kg, not proven consumption or payments; do not use each PO's change_pct "
            "as the change between its price and another order's price.",
            "The September forecast is company-wide: 118500 kg, recorded change +1.9%; only 23700 kg/year "
            "is authorized for this negotiation. The forecast is not a binding supplier call-off schedule.",
            "Short-window index declines coexist with annual increases. Category-team cost weights are "
            "estimates and differ from the supplier's stated weights.",
            "No should-cost estimate for this part/supplier is present. Historical prices and market data "
            "support repricing but do not prove the proposed price is supplier-acceptable.",
            "The July price condition is labelled index_adjustment. The historic contract uses PPI-C2431; "
            "the current case permits only the specified AS-14404 clause or none. Do not stack clauses "
            "or assert a proven double charge or notice breach.",
            "Supplier-wide scorecard receipt quantity is not this part's volume. Do not disclose unrelated "
            "part prices or use out-of-scope quotations as competitor evidence.",
        ],
    }
    _additional_evidence(pkg, intelligence, register, calculate)


def _additional_evidence(pkg, intelligence, register, calculate):
    """Separate fresh checked claims from ERP context and comparisons with no live offer."""
    facts, tables = intelligence["facts"], intelligence["tables"]
    dossier = intelligence["p18_evidence"]

    def ledger(name, sql, params):
        from .research_collection import read_all

        rows = read_all(pkg, sql, params)
        ref = f"buyer-record:p18:{name}"
        facts[ref] = {
            "kind": "internal_erp_extract",
            "arena_citable": False,
            "source": "world.sqlite",
            "query": sql,
            "params": params,
            "rows": rows,
        }
        return ref, rows

    account_ref, invoices = ledger(
        "fy2026-account-invoices",
        "SELECT i.id,i.invoice_date,i.amount_eur,i.po_line_id,p.part_id FROM invoices i "
        "JOIN po_lines p ON p.id=i.po_line_id WHERE i.supplier_id=? "
        "AND i.invoice_date>=? AND i.invoice_date<? ORDER BY i.invoice_date,i.id",
        ["sup:70740", "2025-10-01", "2026-10-01"],
    )
    amount = sum(row["amount_eur"] for row in invoices)
    calculate(
        "account_invoiced_fy2026_eur",
        amount,
        "FY2026 invoiced spend across the existing Werdenfels account, all parts; no new commitment",
        [account_ref],
    )
    calculate(
        "account_invoices_fy2026_count", len(invoices), "Invoices supporting that FY2026 account spend", [account_ref]
    )
    part_amount = sum(row["amount_eur"] for row in invoices if row["part_id"] == "part:370010")
    calculate(
        "part_invoiced_fy2026_eur",
        part_amount,
        "FY2026 invoiced spend for part 370010 only; distinct from order or payment totals",
        [account_ref],
    )

    receipt_ref, receipts = ledger(
        "fy2026-part-receipts",
        "SELECT g.id,g.po_line_id,g.posting_date,g.qty,p.requested_date,p.first_confirmed_date "
        "FROM goods_receipts g JOIN po_lines p ON p.id=g.po_line_id "
        "WHERE g.supplier_id=? AND g.part_id=? AND g.posting_date>=? AND g.posting_date<? "
        "ORDER BY g.posting_date,g.id",
        ["sup:70740", "part:370010", "2025-10-01", "2026-10-01"],
    )
    ontime = sum(
        -5 <= (date.fromisoformat(row["posting_date"]) - date.fromisoformat(row["requested_date"])).days <= 2
        for row in receipts
    )
    method_ref = "buyer-record:p18:scorecard-method"
    intelligence["documents"].append({"id": method_ref, "text": pkg.document("SCORECARD.md")})
    calculate(
        "part_received_fy2026_kg",
        sum(row["qty"] for row in receipts),
        "FY2026 posted receipts for part 370010, including orders placed before the period",
        [receipt_ref],
    )
    if receipts:
        calculate(
            "part_receipt_otd_fy2026_pct",
            ontime / len(receipts) * 100,
            "Part-specific receipt-line OTD, requested date -5/+2 days, partial receipts counted separately; "
            "not the supplier-wide scorecard",
            [receipt_ref, method_ref],
        )

    paid_refs = ["inv:5105005073", "inv:5105005125"]
    paid = [register(ref) for ref in paid_refs]
    if all(row and row.get("paid_amount_eur") == row["amount_eur"] and row.get("paid_date") for row in paid):
        calculate(
            "june_invoiced_and_paid_unit_price",
            sum(row["amount_eur"] for row in paid) / sum(row["qty"] for row in paid),
            "Two June-order invoices paid in full: EUR per kg, not evidence of a historical unindexed contract",
            paid_refs,
        )
    clauses = tables["contract_clauses"]
    for row in clauses:
        if register(row["id"]):
            facts[row["id"]] = {**facts[row["id"]], "text": row["text"]}

    # Other suppliers' POs are historical context only. Do not relabel them as eligible
    # rival quotations, or infer that the hosted checker accepts cross-supplier POs.
    comparison_refs = ["po:4500013392-10", "po:4500013482-10", "po:4500014391-10", "po:4500014455-10"]
    for ref in comparison_refs:
        register(ref)
    lci_refs = ["idx:LCI-C@2026-04", "idx:LCI-C@2026-06"]
    change = (facts[lci_refs[1]]["value"] / facts[lci_refs[0]]["value"] - 1) * 100
    calculate(
        "lci_latest_quarter_change_pct",
        change,
        "Latest available LCI-C April–June 2026 change; does not negate its annual rise",
        lci_refs,
    )
    dossier["followup_arguments"] = [
        {
            "id": "rolling_supplier_otd",
            "source_refs": ["sc:70740@2026Q3"],
            "kind": "checked_claim_candidate",
            "sentence": "Your supplier-wide on-time delivery rate for the twelve months ending September 2026 "
            "was 81.0% (sc:70740@2026Q3).",
            "numeric_claims": [],
            "use": "New evidence in the second email; relate to the contract's 95% target in a separate sentence. "
            "Do not claim a red scorecard, a part-specific rate, or a priced service remedy.",
        },
        {
            "id": "latest_wage_window",
            "source_refs": lci_refs,
            "kind": "checked_claim_candidate",
            "sentence": "LCI-C changed by +0.00% from 2026-04 to 2026-06 (idx:LCI-C@2026-04, idx:LCI-C@2026-06).",
            "numeric_claims": [
                {"calculation_id": "p18:lci_latest_quarter_change_pct", "value": change, "quote": "changed by +0.00%"}
            ],
            "use": "Secondary timing context only; latest data are flat, but annual wages rose. "
            "Never present this as falling wages or proof of a lower cost floor.",
        },
    ]
    dossier["new_context"] = {
        "account_spend": {
            "calculation_id": "p18:account_invoiced_fy2026_eur",
            "source_refs": [account_ref],
            "use": "Existing account value for the small-account/tier discussion; no additional award, "
            "no bundle, no changes to other parts. ERP context, not an arena-verified claim.",
        },
        "paid_price": {
            "calculation_id": "p18:june_invoiced_and_paid_unit_price",
            "source_refs": paid_refs,
            "use": "Only for a payment-proof question; invoices are not a documented checked claim type.",
        },
        "historical_other_supplier_orders": {
            "source_refs": comparison_refs,
            "use": "Internal context only: EUR 5.24/kg January, EUR 5.45/kg September, different volume allocation. "
            "Not a live quote; neither price undercuts the last observed EUR 5.20 offer. Hosted relevance untested.",
        },
        "receipt_scope": "23340 kg received differs from 22440 kg ordered because the date basis differs; "
        "neither is measured consumption. Part-specific OTD is distinct from supplier-wide 81%.",
        "authority": "The owner offered a calculation discussion (doc:20260904-70740-M1). "
        "This is an invitation to seek owner review, not proof of delegated approval.",
    }
