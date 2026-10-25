import json
import re
from datetime import date

import pytest
from assessment_helpers import assessment_fields

from aeiou.acceptance_floor import AcceptanceFloor
from aeiou.config import ROOT
from aeiou.decision_memory import build_context
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package, research


@pytest.fixture(scope="module")
def prepared():
    case = json.loads((ROOT / "data/package/cases/practice/P18.json").read_text())
    pkg = package()
    intelligence = research(case, pkg)
    return case, pkg, intelligence, build_context(case, {}, intelligence, {})


def test_retrieves_complete_scoped_order_and_forecast_history(prepared):
    _, pkg, info, _ = prepared
    refs = info["p18_evidence"]["order_refs"]
    assert len(refs) == 72
    assert set(refs) == {
        r["id"]
        for r in pkg.query("SELECT id FROM po_lines WHERE supplier_id=? AND part_id=?", ["sup:70740", "part:370010"])
    }
    assert all(info["facts"][r]["supplier"] == "sup:70740" and info["facts"][r]["part"] == "part:370010" for r in refs)
    assert len(info["tables"]["forecasts"]) == 12
    assert all(row["part_id"] == "part:370010" for row in info["tables"]["forecasts"])
    assert len(info["tables"]["price_conditions"]) == 6
    assert len(info["tables"]["forecast_lines"]) == 12
    assert any(d["id"] == "ctr:4600070740" and "PPI-C2431" in d["text"] for d in info["documents"])
    assert "ctr:4600070740#3.2" in info["facts"]
    assert not info["estimates"]  # Do not invent a should-cost model.
    assert [r["id"] for r in info["tables"]["quotes"]] == ["quote:7200018"]
    assert "quote:7300008" not in info["facts"]


def test_calculations_keep_actual_orders_separate_from_company_forecast(prepared):
    case, _, info, context = prepared
    values = {ref: item["value"] for ref, item in context["calculations"].items()}
    assert values["p18:orders_last_12m_kg"] == 22440
    assert values["p18:orders_last_12m_lines"] == 23
    assert [values[f"p18:orders_{q}_kg"] for q in ("2025Q4", "2026Q1", "2026Q2", "2026Q3")] == [4830, 7210, 5340, 5060]
    assert values["p18:commitment_vs_orders_pct"] == pytest.approx((23700 / 22440 - 1) * 100, abs=0.0001)
    assert info["facts"]["fc:370010@2026-09"]["volume_per_year"] == 118500
    assert info["facts"]["fc:370010@2026-09"]["change_pct"] == 1.9
    assert case["fixed_terms"]["volume_per_year"] == 23700
    assert values["p18:order_price_change_pct"] == pytest.approx((5.84 / 5.26 - 1) * 100, abs=0.0001)
    for calc in info["calculations"]:
        assert all(ref in context["sources"] for ref in calc["source_refs"])


def test_opening_arguments_use_unique_eligible_source_groups(prepared):
    _, _, info, context = prepared
    groups = info["p18_evidence"]["opening_arguments"]
    assert len(groups) == 3
    assert 2 <= len(groups[0]["source_refs"]) <= 8
    assert len({ref for g in groups for ref in g["source_refs"]}) == sum(len(g["source_refs"]) for g in groups)
    for group in groups[1:]:
        a, b = [info["facts"][r] for r in group["source_refs"]]
        assert a["series"] == b["series"]
        start, end = [date.fromisoformat(f["month"] + "-01") for f in (a, b)]
        assert 0 < (end.year - start.year) * 12 + end.month - start.month <= 12
        assert date(2025, 10, 1) <= end <= date(2026, 9, 30)
        claim = group["numeric_claims"][0]
        assert claim["value"] == pytest.approx((b["value"] / a["value"] - 1) * 100, abs=0.01)
        assert claim["value"] == pytest.approx(context["calculations"][claim["calculation_id"]]["value"], abs=0.01)


@pytest.mark.parametrize("version", ["v6", "v7", "v8", "v9"])
def test_evidence_reaches_actual_send_and_keeps_score_gate(prepared, version):
    case, pkg, info, _ = prepared
    max_cost, minimum_score, opening_price = {
        "v6": (237155.66, 80, 4.45),
        "v7": (237155.66, 80, 4.45),
        "v8": (241950.40, 73.38857, 4.80),
        "v9": (246888.17, 66.57991, 4.90),
    }[version]
    strategy = (ROOT / f"prompts/p18-diagnostic-{version}.txt").read_text()
    body = (
        "Dear Mr Seidl,"
        + strategy.split("Dear Mr Seidl,", 1)[1].split("Purchasing, Hollerau Pumpenwerke", 1)[0]
        + "Purchasing, Hollerau Pumpenwerke"
    )
    sid = "sup:70740"
    threads = {
        sid: [
            {
                "from": "sales@70740",
                "status": "open",
                "body": "Our quotation.",
                "term_sheets": [
                    {
                        "term_sheet_id": "offer:opening",
                        "terms": {"unit_price": 6.10, "payment_days": 30, "contract_months": 12, "index_clause": None},
                    }
                ],
            }
        ]
    }
    calls = []

    class FixtureBackend:
        def complete(self, system, user):
            payload = json.loads(user)
            calls.append(payload)
            assert payload["acceptance_policy"]["maximum_total_cost"] == max_cost
            assert len(payload["research"]["p18_evidence"]["order_refs"]) == 72
            assert len(payload["research"]["tables"]["forecasts"]) == 12
            for ref in re.findall(r"p18:[a-zA-Z0-9_]+", strategy):
                if ref == "p18:orders_":  # The prompt also describes the <quarter> ID template.
                    continue
                assert ref in payload["decision_context"]["calculations"]
            fields = assessment_fields(payload, sid)
            fields["numeric_claims"] = json.loads(
                next(line for line in strategy.splitlines() if line.startswith('[{"calculation_id":'))
            )
            return json.dumps(
                {
                    "supplier_id": sid,
                    "action": "send",
                    "body": body,
                    **fields,
                    "term_sheets": [
                        {
                            "unit_price": opening_price,
                            "payment_days": 14,
                            "contract_months": 36,
                            "index_clause": {
                                "series": "AS-14404",
                                "material_share": 0.45,
                                "deadband_pct": 0,
                                "base_month": "2026-08",
                            },
                        }
                    ],
                }
            )

    spec = PromptSpec(f"{version}-offline-test", strategy, assessment_enabled=True)
    agent = PromptLabAgent(pkg, FixtureBackend(), spec)
    move = agent.decide(case, threads)
    assert len(calls) == 1
    assert move["message"]["body"] == body
    assert all(g["sentence"] in body for g in info["p18_evidence"]["opening_arguments"])
    assert set(move["message"]) == {"subject", "body", "term_sheets"}
    assert "237155" not in body and "acceptance_floor" not in body
    assert "buyer-record:" not in body and "p18:" not in body
    assert AcceptanceFloor.from_prompt(strategy).metadata()["minimum_score"] == minimum_score
    floor = AcceptanceFloor.from_prompt(strategy)
    assert not floor.allows(float(floor.max_cost) + 0.01, {"unit_price": 4.80})


def test_extra_calculation_cannot_reference_unavailable_evidence(prepared):
    case, _, _, _ = prepared
    with pytest.raises(ValueError, match="unavailable source"):
        build_context(
            case,
            {},
            {
                "calculations": [
                    {"id": "p18:false", "value": 1, "description": "Invalid", "source_refs": ["po:unknown"]}
                ]
            },
            {},
        )


def test_enrichment_does_not_change_other_cases(prepared):
    _, pkg, _, _ = prepared
    case = json.loads((ROOT / "data/package/cases/practice/P01.json").read_text())
    info = research(case, pkg)
    assert "p18_evidence" not in info
    assert "calculations" not in info
    assert not any(r.startswith("po:") and f.get("supplier") == "sup:70740" for r, f in info["facts"].items())


def test_account_value_is_reconciled_to_invoices_and_not_extra_case_volume(prepared):
    case, _, info, context = prepared
    account = info["facts"]["buyer-record:p18:fy2026-account-invoices"]
    assert not account["arena_citable"]
    assert len(account["rows"]) == 79
    assert {r["part_id"] for r in account["rows"]} == {"part:370010", "part:370011", "part:370012"}
    assert sum(r["amount_eur"] for r in account["rows"]) == pytest.approx(428119.02)
    cube = next(r for r in info["tables"]["spend_cube"] if r["fiscal_year"] == "FY2026")
    assert context["calculations"]["p18:account_invoiced_fy2026_eur"]["value"] == cube["spend_eur"]
    assert context["calculations"]["p18:part_invoiced_fy2026_eur"]["value"] == 126823.3
    assert case["fixed_terms"]["volume_per_year"] == 23700


def test_receipts_orders_and_supplier_scorecard_keep_distinct_bases(prepared):
    _, _, info, context = prepared
    receipts = info["facts"]["buyer-record:p18:fy2026-part-receipts"]["rows"]
    assert len(receipts) == 26
    values = {key: item["value"] for key, item in context["calculations"].items()}
    assert values["p18:part_received_fy2026_kg"] == 23340
    assert values["p18:orders_last_12m_kg"] == 22440
    assert values["p18:part_receipt_otd_fy2026_pct"] == pytest.approx(22 / 26 * 100, abs=0.0001)
    assert info["facts"]["sc:70740@2026Q3"]["otd_pct"] == 81
    scorecard = next(r for r in info["tables"]["scorecards"] if r["id"] == "sc:70740@2026Q3")
    assert (scorecard["period_start"], scorecard["period_end"]) == ("2025-10-01", "2026-09-30")


def test_fresh_claims_and_paid_price_are_supported_without_inventing_live_competition(prepared):
    _, _, info, context = prepared
    opening_refs = {ref for arg in info["p18_evidence"]["opening_arguments"] for ref in arg["source_refs"]}
    followup = info["p18_evidence"]["followup_arguments"]
    assert len(followup) == 2
    for argument in followup:
        assert not opening_refs.intersection(argument["source_refs"])
        assert all(ref in info["facts"] for ref in argument["source_refs"])
    assert context["calculations"]["p18:lci_latest_quarter_change_pct"]["value"] == 0
    assert next(i for i in info["indices"] if i["series"] == "LCI-C")["change_pct"] > 0
    assert context["calculations"]["p18:june_invoiced_and_paid_unit_price"]["value"] == 5.26
    for ref in info["p18_evidence"]["new_context"]["paid_price"]["source_refs"]:
        assert info["facts"][ref]["paid_amount_eur"] == info["facts"][ref]["amount_eur"]
        assert info["facts"][ref]["paid_date"]
    comparisons = info["p18_evidence"]["new_context"]["historical_other_supplier_orders"]
    assert all(info["facts"][ref]["supplier"] == "sup:70710" for ref in comparisons["source_refs"])
    assert "Hosted relevance untested" in comparisons["use"]
    assert info["p18_evidence"]["eligible_quote_refs"] == ["quote:7200018"]
