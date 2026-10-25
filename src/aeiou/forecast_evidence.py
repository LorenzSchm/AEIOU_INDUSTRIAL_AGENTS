"""Latest scoped forecast rows, without turning forecasts into commitments."""

import copy
from collections import Counter


def month_offset(month, offset):
    year, number = map(int, month.split("-"))
    year, number = divmod(year * 12 + number - 1 + offset, 12)
    return f"{year:04}-{number + 1:02}"


def latest_forecasts(case, tables):
    """Keep complete latest snapshots separate from the full historical table."""
    latest = {}
    for row in tables.get("forecasts", []):
        part = row["part_id"]
        if part not in case.get("parts", []):
            continue
        if part not in latest or (row["snapshot_month"], row["id"]) > (
                latest[part]["snapshot_month"], latest[part]["id"]):
            latest[part] = row
    snapshots = []
    for part, forecast in sorted(latest.items()):
        rows = sorted((r for r in tables.get("forecast_history_lines", []) if r["fc_id"] == forecast["id"]),
                      key=lambda r: r["target_month"])
        expected = [month_offset(forecast["snapshot_month"], n + 1) for n in range(forecast["horizon_months"])]
        counts = Counter(r["target_month"] for r in rows)
        missing = sorted(set(expected) - counts.keys())
        unexpected = sorted(counts.keys() - set(expected))
        duplicates = sorted(month for month, count in counts.items() if count > 1)
        total = sum(r["qty"] for r in rows) if rows else None
        matches = total == forecast["qty_next_12m"] if total is not None else None
        snapshots.append({
            "part_id": part, "forecast": copy.deepcopy(forecast),
            "monthly_rows": copy.deepcopy(rows),
            "source_refs": ["table:forecasts", "table:forecast_history_lines"],
            "coverage": {
                "expected_months": expected, "missing_months": missing,
                "unexpected_months": unexpected, "duplicate_months": duplicates,
                "monthly_total": total, "matches_reported_total": matches,
                "complete": bool(expected) and not (missing or unexpected or duplicates) and matches is True,
            },
        })
    return snapshots


def commitment_comparison(case, snapshot):
    """Compare supplied months with full-need contract scenarios; never fill unknown months."""
    start = case.get("fixed_terms", {}).get("effective_date")
    if not start:
        return None
    start = start[:7]
    need = case["buyer"]["need_per_year"]
    forecast = snapshot["forecast"]
    coverage = snapshot["coverage"]
    expected = set(coverage["expected_months"])
    observed = {r["target_month"] for r in snapshot["monthly_rows"]}
    usable = (observed & expected) - set(coverage["duplicate_months"])
    scenarios = []
    for duration in sorted(set(case.get("term_options", {}).get("contract_months", []))):
        months = [month_offset(start, offset) for offset in range(duration)]
        scenarios.append({
            "contract_months": duration, "start_month": start, "end_month": months[-1],
            "annual_volume": need, "total_committed_units": need * duration / 12,
            "months_with_forecast": len(set(months) & usable),
            "months_without_forecast": sorted(set(months) - usable),
            "months_beyond_scoring_horizon": max(0, duration - case["buyer"]["horizon_years"] * 12),
        })
    return {
        "authorized_need_per_year": need, "forecast_total": forecast["qty_next_12m"],
        "forecast_horizon_months": forecast["horizon_months"],
        "forecast_start_month": coverage["expected_months"][0] if expected else None,
        "forecast_end_month": coverage["expected_months"][-1] if expected else None,
        "contract_start_month": start,
        "need_minus_forecast_units": need - forecast["qty_next_12m"] if forecast["horizon_months"] == 12 else None,
        "same_first_year_window": coverage["expected_months"] == [month_offset(start, n) for n in range(12)],
        "forecast_months_before_contract": sorted(month for month in usable if month < start),
        "full_need_contract_scenarios": scenarios,
        "interpretation": [
            "The annual case need is authorized; the forecast is planning evidence, not a binding call-off schedule.",
            "Different forecast and contract windows are not a measured demand shortfall or proof of growth.",
            "Missing months are unknown, not zero demand. Do not extrapolate or scale monthly rows to the annual need.",
            "Do not promise level monthly call-offs without a supported plan. Reconcile the periods before using "
            "a schedule as a concession; preserve the case's authorized volume and must-haves.",
            "Contract scenarios show full-need commitments, not accepted supplier offers. Months beyond the "
            "scoring horizon remain a real commitment.",
        ],
    }
