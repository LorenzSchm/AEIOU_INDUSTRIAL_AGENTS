"""Complete, scoped reads from the frozen buyer dataset, with coverage accounting."""

VERSION = "research-collection-v1"
PAGE_ROWS = 200


def read_all(pkg, sql, params=()):
    """Page a trusted SELECT without inheriting DataPackage.query's 200-row cap."""
    rows = []
    while True:
        page = pkg.query(f"{sql} LIMIT ? OFFSET ?", [*params, PAGE_ROWS, len(rows)], limit=PAGE_ROWS)
        rows.extend(page)
        if len(page) < PAGE_ROWS:
            return rows


def collect_tables(case, pkg):
    """Only buyer tables and case-related rows; no private simulator state or arbitrary SQL."""
    schema, tables, coverage = pkg.tables(), {}, {}
    suppliers, parts = case.get("suppliers", []), case.get("parts", [])

    def load(name, filters, *, source=None, extra=None):
        table = source or name
        if table not in schema or any(not values for _, values in filters):
            coverage[name] = {"status": "not_available" if table not in schema else "out_of_scope"}
            return
        clauses = [f'"{column}" IN ({",".join("?" for _ in values)})' for column, values in filters]
        params = [value for _, values in filters for value in values]
        if extra:
            clause, values = extra
            clauses.append(clause)
            params.extend(values)
        where = " AND ".join(clauses) or "1=1"
        columns = [c.split()[0] for c in schema[table]["columns"]]
        order = ",".join(f'"{column}"' for column in columns)
        sql = f'SELECT * FROM "{table}" WHERE {where}'
        expected = pkg.query(f'SELECT count(*) AS n FROM "{table}" WHERE {where}', params)[0]["n"]
        rows = read_all(pkg, f"{sql} ORDER BY {order}", params)
        if len(rows) != expected:
            raise ValueError(f"Incomplete buyer data: {table}: expected {expected}, loaded {len(rows)}")
        tables[name] = rows
        coverage[name] = {"status": "complete", "source_table": table, "expected_rows": expected,
                          "loaded_rows": len(rows), "scope": dict(filters)}

    load("suppliers", [("id", suppliers)])
    load("parts", [("id", parts)])
    for table in ("supplier_intel", "negotiation_history", "news", "scorecards", "contracts", "spend_cube",
                  "documents"):
        load(table, [("supplier_id", suppliers)])
    for table in ("should_cost", "quotes", "forecasts", "sourcing", "cost_drivers", "rfqs"):
        load(table, [("part_id", parts)])
    for table in ("po_lines", "goods_receipts", "quality_notifications", "price_conditions"):
        load(table, [("supplier_id", suppliers), ("part_id", parts)])
    if parts and "po_lines" in schema:
        load("invoices", [("supplier_id", suppliers)], extra=(
            f'po_line_id IN (SELECT id FROM po_lines WHERE part_id IN ({",".join("?" for _ in parts)}))', parts))
    load("contract_clauses", [("contract_id", [r["id"] for r in tables.get("contracts", [])])])
    load("forecast_history_lines", [("fc_id", [r["id"] for r in tables.get("forecasts", [])])],
         source="forecast_lines")
    series = sorted({r["series_id"] for r in tables.get("cost_drivers", [])})
    load("index_series", [("id", series)])
    load("index_values", [("series_id", series)])
    return tables, {"version": VERSION, "tables": coverage,
                    "sunday_release_loaded": bool(getattr(pkg, "release", None)),
                    "scope_note": "Supplier account context and case-part records. Historical quotes are not "
                                  "automatically authorized live alternatives. Forecasts are not commitments."}


def reconcile_collection(intelligence):
    """Account for case-specific filtering after collection, rather than claiming excluded rows were delivered."""
    collection = intelligence["collection"]
    for name, entry in collection["tables"].items():
        if entry["status"] == "complete":
            count = len(intelligence["tables"].get(name, []))
            entry["retained_rows"] = count
            if count != entry["loaded_rows"]:
                if name != "quotes" or "p18_evidence" not in intelligence:
                    raise ValueError(f"Unexplained loss of collected buyer rows: {name}")
                entry["status"] = "filtered_by_case"
                entry["excluded_refs"] = intelligence["p18_evidence"]["excluded_quote_refs"]
                entry["reason"] = "P18 permits only case-authorized quotations as competitor evidence"
    collection["fact_count"] = len(intelligence["facts"])
    collection["document_count"] = len(intelligence["documents"])
    collection["issues"] = list(intelligence.get("retrieval_issues", []))
