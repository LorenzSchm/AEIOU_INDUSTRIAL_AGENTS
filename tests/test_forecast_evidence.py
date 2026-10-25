import copy
import json

import pytest
from assessment_helpers import assessment_fields

from aeiou.config import ROOT
from aeiou.evidence_transport import prepare_evidence_payload, record_text, register_read, retrieve, source_records
from aeiou.forecast_evidence import commitment_comparison, latest_forecasts
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package, research


@pytest.fixture(scope="module")
def p10():
    case = json.loads((ROOT / "data/package/cases/practice/P10.json").read_text())
    pkg = package()
    return case, pkg, research(case, pkg)


def test_latest_forecast_is_scoped_and_preserves_monthly_rows_and_original_history(p10):
    case, _, info = p10
    tables = copy.deepcopy(info["tables"])
    before = copy.deepcopy(tables)
    snapshots = latest_forecasts(case, tables)
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["forecast"]["id"] == "fc:340010@2026-09"
    assert snapshot["monthly_rows"] == [r for r in tables["forecast_history_lines"]
                                        if r["fc_id"] == "fc:340010@2026-09"]
    assert len(snapshot["monthly_rows"]) == 12
    assert snapshot["coverage"]["complete"]
    assert snapshot["coverage"]["monthly_total"] == 21310
    assert (min(r["qty"] for r in snapshot["monthly_rows"]),
            max(r["qty"] for r in snapshot["monthly_rows"])) == (910, 2400)
    # A newer unrelated part must never replace the case part's snapshot.
    tables["forecasts"].append({**snapshot["forecast"], "id": "fc:other", "part_id": "part:other",
                                "snapshot_month": "2028-01"})
    assert latest_forecasts(case, tables) == snapshots
    snapshot["monthly_rows"][0]["qty"] = -1
    assert tables["forecast_history_lines"] == before["forecast_history_lines"]
    assert len(tables["forecast_history_lines"]) == 144


@pytest.mark.parametrize("problem", ["missing", "duplicate", "mismatch", "no_rows", "unexpected"])
def test_incomplete_latest_forecast_is_reported_without_filling_or_using_older_rows(p10, problem):
    case, _, info = p10
    tables = copy.deepcopy(info["tables"])
    rows = tables["forecast_history_lines"]
    row = next(r for r in rows if r["fc_id"] == "fc:340010@2026-09")
    if problem == "missing":
        rows.remove(row)
    elif problem == "duplicate":
        rows.append(copy.deepcopy(row))
    elif problem == "mismatch":
        row["qty"] += 1
    elif problem == "no_rows":
        tables["forecast_history_lines"] = [r for r in rows if r["fc_id"] != "fc:340010@2026-09"]
    else:
        row["target_month"] = "2028-01"
    snapshot = latest_forecasts(case, tables)[0]
    assert snapshot["forecast"]["id"] == "fc:340010@2026-09"
    assert not snapshot["coverage"]["complete"]
    assert len(snapshot["monthly_rows"]) == {"missing": 11, "duplicate": 13, "no_rows": 0}.get(problem, 12)
    if problem == "no_rows":
        assert snapshot["coverage"]["monthly_total"] is None
        assert len(snapshot["coverage"]["missing_months"]) == 12
    elif problem == "duplicate":
        assert snapshot["coverage"]["duplicate_months"] == [row["target_month"]]


def test_commitment_comparison_uses_calendar_months_and_keeps_authorized_need(p10):
    case, _, info = p10
    comparison = info["p10_evidence"]["demand_preparation"]
    assert comparison["forecast_start_month"] == "2026-10"
    assert comparison["forecast_end_month"] == "2027-09"
    assert comparison["contract_start_month"] == "2027-01"
    assert comparison["authorized_need_per_year"] == 23400
    assert comparison["need_minus_forecast_units"] == 2090
    assert not comparison["same_first_year_window"]
    assert comparison["forecast_months_before_contract"] == ["2026-10", "2026-11", "2026-12"]
    scenarios = comparison["full_need_contract_scenarios"]
    assert [r["total_committed_units"] for r in scenarios] == [23400, 46800, 70200]
    assert [r["months_with_forecast"] for r in scenarios] == [9, 9, 9]
    assert [len(r["months_without_forecast"]) for r in scenarios] == [3, 15, 27]
    assert [r["end_month"] for r in scenarios] == ["2027-12", "2028-12", "2029-12"]
    assert [r["months_beyond_scoring_horizon"] for r in scenarios] == [0, 0, 12]
    assert "not a measured demand shortfall" in " ".join(comparison["interpretation"])
    assert "Do not promise level monthly call-offs" in " ".join(comparison["interpretation"])
    # Moving the actual effective date changes the coverage; no P10 dates are hard-coded.
    aligned = copy.deepcopy(case)
    aligned["fixed_terms"]["effective_date"] = "2026-10-01"
    changed = commitment_comparison(aligned, info["latest_forecasts"][0])
    assert changed["same_first_year_window"]
    assert changed["full_need_contract_scenarios"][0]["months_without_forecast"] == []


def test_missing_latest_forecast_stays_unknown(p10):
    from aeiou.p10_evidence import enrich_p10

    case, pkg, _ = p10
    info = {"facts": {}, "tables": {}, "latest_forecasts": []}
    assert latest_forecasts(case, {}) == []
    enrich_p10(case, pkg, info)
    assert info["p10_evidence"]["demand_preparation"]["status"] == "unavailable"
    assert "full_need_contract_scenarios" not in info["p10_evidence"]["demand_preparation"]


def test_actual_backend_gets_latest_months_and_preparation_without_retrieval(p10):
    case, pkg, info = p10
    sid = case["suppliers"][0]
    threads = {sid: [{"from": "supplier", "body": "Please review our quotation.", "status": "open"}]}
    requests = []

    class Backend:
        def complete(self, system, user):
            payload = json.loads(user)
            requests.append(payload)
            snapshot = payload["research"]["latest_forecasts"][0]
            assert snapshot == info["latest_forecasts"][0]
            preparation = payload["research"]["p10_evidence"]["demand_preparation"]
            assert preparation["forecast_rows_complete"]
            assert preparation["full_need_contract_scenarios"][2]["months_with_forecast"] == 9
            assert "unresolved_from_buyer_records" in payload["research"]["p10_evidence"]
            assert payload["research"]["estimates"][sid]["full_cost"] == 24.31
            assert payload["evidence_transport"]["delivered_payload_chars"] == len(user)
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Thank you for your quotation.",
                               "term_sheets": [], **assessment_fields(payload, sid)})

    agent = PromptLabAgent(pkg, Backend(), PromptSpec("forecast-check", "Negotiate P10.", assessment_enabled=True))
    agent.decide(case, threads)
    assert len(requests) == 1
    payload = requests[0]
    history = payload["decision_context"]["sources"]["table:forecast_history_lines"]
    assert history["delivery"] == "partial"
    assert history["available"] and history["retrievable"] and not history["complete_record"]
    assert "forecast_history_lines" not in payload["research"]["tables"]
    assert payload["research"]["table_row_counts"]["forecast_history_lines"] == 144
    records = source_records(agent.intelligence, threads)
    assert records["table:forecast_history_lines"] == info["tables"]["forecast_history_lines"]
    # Only reading the complete history upgrades it from a latest-snapshot excerpt to full.
    pages, offset = [], 0
    while offset is not None:
        page = retrieve(records, {"source_ref": "table:forecast_history_lines", "offset": offset})
        pages.append(page)
        offset = page["next_offset"]
    payload["retrieved_evidence"] = pages
    register_read(payload, pages[-1])
    assert history["complete_record"]
    assert "".join(p["text"] for p in pages) == record_text(records["table:forecast_history_lines"])


def test_small_full_history_is_delivered_under_the_correct_table_name(p10):
    case, pkg, info = p10
    agent = PromptLabAgent(pkg, None, PromptSpec("forecast-check", "Negotiate P10.", assessment_enabled=True))
    agent.prepare(case)
    agent.intelligence["tables"]["forecast_history_lines"] = copy.deepcopy(info["latest_forecasts"][0]["monthly_rows"])
    payload = json.loads(agent.render_lab(case, {}))
    assert len(payload["research"]["tables"]["forecast_history_lines"]) == 12
    assert payload["decision_context"]["sources"]["table:forecast_history_lines"]["delivery"] == "full"
    # Re-preparing delivery does not downgrade a fully supplied table to a partial excerpt.
    prepare_evidence_payload(payload, agent.intelligence, {}, "")
    assert payload["decision_context"]["sources"]["table:forecast_history_lines"]["complete_record"]
