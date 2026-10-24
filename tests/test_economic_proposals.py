import json

from aeiou.agent import Negotiator
from aeiou.config import data_root
from aeiou.optimizer import safe_close
from aeiou.research import package
from aeiou.simulation import local_cases


def test_rejects_discounted_overbuy_before_it_reaches_supplier():
    case = json.loads((data_root() / "cases/practice/P03.json").read_text())
    sid = case["suppliers"][0]
    thread = [
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {
                    "term_sheet_id": "offer:live",
                    "terms": {"unit_price": 428.4, "payment_days": 14, "contract_months": 36, "volume_per_year": 4580},
                }
            ],
        }
    ]
    agent = Negotiator(package())
    agent.prepare(case)
    proposal = {
        "supplier_id": sid,
        "action": "send",
        "body": "We can commit to this package.",
        "term_sheets": [{"unit_price": 417, "payment_days": 14, "contract_months": 36, "volume_per_year": 5496}],
    }
    assert agent.guard(case, {sid: thread}, proposal, {}) is None


def test_same_price_at_full_need_is_an_executable_proposal():
    case = json.loads((data_root() / "cases/practice/P03.json").read_text())
    sid = case["suppliers"][0]
    thread = [
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {
                    "term_sheet_id": "offer:live",
                    "terms": {"unit_price": 428.4, "payment_days": 14, "contract_months": 36, "volume_per_year": 4580},
                }
            ],
        }
    ]
    agent = Negotiator(package())
    agent.prepare(case)
    proposal = {
        "supplier_id": sid,
        "action": "send",
        "body": "Thank you for offer:live. Please review our package.",
        "term_sheets": [{"unit_price": 426, "payment_days": 14, "contract_months": 36, "volume_per_year": 4580}],
    }
    result = agent.guard(case, {sid: thread}, proposal, {})
    assert result is not None
    assert "offer:live" not in result["message"]["body"]


def test_close_scores_every_supplier_alternative_not_just_first_sheet():
    case = local_cases()["case:S01"]
    sid = case["suppliers"][0]
    threads = {
        sid: [
            {
                "from": "supplier",
                "status": "final_offer",
                "term_sheets": [
                    {"term_sheet_id": "offer:main", "terms": {"unit_price": 17, "payment_days": 14}},
                    {"term_sheet_id": "offer:alt", "terms": {"unit_price": 17, "payment_days": 90}},
                ],
            }
        ]
    }
    assert safe_close(case, threads, {})["message"]["action"]["term_sheet_id"] == "offer:alt"
