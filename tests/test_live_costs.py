"""Keep browser cost estimates aligned with the pinned public Arena Kit formula."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from arena_kit.cost import closing_terms, must_haves_ok, total_cost

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/sandbox-cases.json").read_text())


def browser(payload, expression):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to check browser cost parity")
    script = f"""
import {{ readFileSync }} from 'node:fs';
import * as costs from './frontend/src/lib/offer-comparison.ts';
const input = JSON.parse(readFileSync(0, 'utf8'));
console.log(JSON.stringify({expression}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_browser_cost_matches_kit_for_complete_and_partial_packages():
    samples = []
    for original in CASES:
        case = copy.deepcopy(original)
        case["buyer"].update(index_paths={"METAL": -0.12}, index_risk_pct=2)
        for months in [0, 12, 24, 36]:
            for volume in [3000, 8000]:
                for incoterm in ["EXW", "FCA", "DAP"]:
                    terms = {
                        "unit_price": 16.7,
                        "payment_days": 90,
                        "contract_months": months,
                        "volume_per_year": volume,
                        "incoterm": incoterm,
                        "lead_time_days": 45,
                        "effective_date": "2027-04-01",
                        "tooling_eur": 1200,
                        "ppm_commitment": 1800,
                        "chargeback_eur_per_complaint": 12,
                        "index_clause": {"series": "METAL", "material_share": 0.6, "deadband_pct": 3},
                    }
                    samples.append({"case": case, "terms": terms, "standard": {}})
        samples.extend(
            [
                {
                    "case": case,
                    "terms": {"unit_price": 17.1, "payment_days": None},
                    "standard": {"payment_days": 30, "lead_time_days": 28, "ppm_commitment": 1100},
                },
                {
                    "case": case,
                    "terms": {"unit_price": 16.7, "payment_days": 60, "lead_time_days": 35, "incoterm": "DAP"},
                    "standard": {"ppm_commitment": 2600},
                },
                {"case": case, "terms": {}, "standard": {}},
            ]
        )
    observed = browser(
        samples,
        """input.map(s => {
      const t = costs.closingTerms(s.case, s.terms, s.standard);
      return { terms: t, cost: costs.publicCost(s.case, t), valid: !costs.violations(s.case, t).length };
    })""",
    )
    for sample, actual in zip(samples, observed, strict=True):
        case, terms, standard = sample["case"], sample["terms"], sample["standard"]
        expected = closing_terms(case, terms, standard)
        assert actual["terms"] == expected
        cost = total_cost(case, terms, standard)
        assert actual["cost"] == (None if cost == float("inf") else pytest.approx(cost, abs=0.01))
        assert actual["valid"] == must_haves_ok(case, terms, standard)


def message(price, standard, *, buyer=False, lead=28):
    return {
        "from": "buyer" if buyer else "sales@test",
        "term_sheets": [{"terms": {"unit_price": price, "payment_days": 30, "lead_time_days": lead}}],
        "attachments": []
        if buyer
        else [
            {
                "name": "quote-test.pdf",
                "text": f"""Offered terms
Unit price (EUR) {price}
Our standard terms (apply to everything not stated above)
Incoterm EXW
Quality commitment (ppm) {standard}
Price valid from none
Index clause none
Terms fixed by your enquiry
Contract term (months) 24""",
            }
        ],
    }


def test_live_quotes_keep_opening_standard_terms_and_supplier_boundaries():
    case = CASES[1]
    events = [
        message(17.1, 1100),
        message(14.5, 0, buyer=True),
        message(16, 2000),
        {"from": "buyer", "term_sheets": [], "action": {"type": "accept"}},
    ]
    other = [message(10, 3000, lead=45)]  # Cheaper, but violates the lead-time requirement.
    result = browser(
        {"case": case, "events": events, "other": other},
        """({
      quotes: costs.threadQuotes(input.case, input.events),
      opening: costs.openingCost(input.case, { J1: input.events, C1: input.other }),
      alternative: costs.alternativeCost(input.case),
      other: costs.threadQuotes(input.case, input.other)
    })""",
    )
    assert result["opening"] == 214296
    assert result["alternative"] == 238800
    quotes = result["quotes"]
    assert quotes["opening"][0]["terms"]["ppm_commitment"] == 1100
    assert quotes["current"][0]["terms"]["ppm_commitment"] == 2000
    assert quotes["proposal"][0]["terms"]["ppm_commitment"] == 1100
    assert quotes["proposal"][0]["terms"]["unit_price"] == 14.5
    assert result["other"]["opening"][0]["violations"] == ["lead time days must be <= 42"]


def test_missing_economics_stay_unavailable_and_all_packages_are_retained():
    event = message(17.1, 1100)
    event["term_sheets"].append({"terms": {"unit_price": 16.5, "payment_days": 14, "lead_time_days": 21}})
    case = copy.deepcopy(CASES[1])
    case["buyer"].pop("interest_rate")
    result = browser({"case": case, "events": [event]}, "costs.threadQuotes(input.case, input.events)")
    assert len(result["current"]) == 2
    assert all(quote["cost"] is None for quote in result["current"])
    assert not result["proposal"]
