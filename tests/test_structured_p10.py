import copy

import pytest

from aeiou.structured_p10 import StructuredP10Client, public_claims

FACTS = {
    "cost:340010": {"part": "part:340010", "price_unit": 1, "unit_cost": 24.31, "confidence_pct": 5},
    "sc:70410@2026Q3": {"supplier": "sup:70410", "otd_pct": 79.5},
}
COST = "Our buyer estimate is EUR 24.31/unit excluding margin, with 5% uncertainty (cost:340010)."
SERVICE = "Supplier-wide on-time delivery was 79.5% (sc:70410@2026Q3)."
TERMS = {"unit_price": 29.89, "volume_per_year": 23400, "contract_months": 24, "lead_time_days": 56}


def test_typed_claims_match_canonical_source_values():
    assert public_claims(COST + "\n" + SERVICE, FACTS) == [
        {"type": "should_cost", "refs": ["cost:340010"], "asserted": {"unit_cost": 24.31}},
        {"type": "delivery_record", "refs": ["sc:70410@2026Q3"], "asserted": {"otd_pct": 79.5}},
    ]
    assert public_claims("We propose EUR 29.89; no evidence assertion.", FACTS) == []


@pytest.mark.parametrize(
    "body",
    [
        COST.replace("24.31", "23.00"),
        COST.replace("5%", "1%"),
        SERVICE.replace("79.5%", "90%"),
        "Consider quote:unknown.",
    ],
)
def test_wrong_numbers_and_unsupported_sources_are_not_rewritten(body):
    with pytest.raises(ValueError):
        public_claims(body, FACTS)


def test_wrong_supplier_source_is_rejected():
    facts = copy.deepcopy(FACTS)
    facts["sc:70410@2026Q3"]["supplier"] = "sup:other"
    with pytest.raises(ValueError, match="wrong supplier"):
        public_claims(SERVICE, facts)


class Package:
    def for_case(self, case):
        assert case["case_id"] == "case:P10"
        return self

    def lookup(self, ref):
        return FACTS[ref]


class Client:
    def __init__(self):
        self.moves, self.closings = [], []

    def open_session(self, case_id):
        return {
            "session_id": "session",
            "case": {
                "case_id": case_id,
                "suppliers": ["sup:70410"],
                "parts": ["part:340010"],
                "negotiable": list(TERMS),
            },
            "opening": {"sup:70410": {"offer_id": "offer:live-opening"}},
        }

    def sessions(self, set_="practice"):
        return [{"session_id": "other-session", "status": "ended", "set": set_}]

    def move(self, session, supplier, move):
        self.moves.append(copy.deepcopy(move))
        return {
            "offer_id": "offer:live-reply",
            "status": "open",
            "terms": TERMS,
            "text": "Authoritative structured supplier text.",
            "claim_results": [{"result": "verified"}],
        }

    def thread(self, session, supplier):
        return {
            "offer_id": "offer:live-reply",
            "status": "open",
            "messages": [
                {
                    "from": "sales@70410",
                    "status": "open",
                    "body": "Stale opening email.",
                    "term_sheets": [{"term_sheet_id": "offer:live-opening", "terms": TERMS}],
                }
            ],
        }

    def send_message(self, session, supplier, message):
        self.closings.append(message)
        return {"result": {"deal": True}}


def test_live_id_terms_and_exact_body_survive_translation_and_acceptance():
    client = Client()
    adapter = StructuredP10Client(client, Package())
    adapter.open_session("case:P10")
    original = {"body": COST, "term_sheets": [{"terms": copy.deepcopy(TERMS)}]}
    saved = copy.deepcopy(original)
    reply = adapter.send_message("session", "sup:70410", original)
    assert original == saved
    assert client.moves[0]["offer_id"] == "offer:live-opening"
    assert client.moves[0]["terms"] == TERMS and client.moves[0]["text"] == COST
    assert reply["body"] == "Authoritative structured supplier text."
    assert reply["term_sheets"][0]["term_sheet_id"] == "offer:live-reply"
    assert reply["claim_results"] == [{"result": "verified"}]
    assert reply["source_transport"] == "documented-structured-move"
    adapter.send_message("session", "sup:70410", original)
    assert client.moves[1]["offer_id"] == "offer:live-reply"
    accept = {"action": {"type": "accept", "term_sheet_id": "offer:live-reply"}}
    assert adapter.send_message("session", "sup:70410", accept)["result"]["deal"]
    assert client.closings == [accept]


@pytest.mark.parametrize(
    "patch",
    [{"body": "x" * 801}, {"term_sheets": []}, {"term_sheets": [{"terms": TERMS}] * 2}, {"refs": ["cost:340010"]}],
)
def test_invalid_transport_input_is_rejected_before_network_mutation(patch):
    client = Client()
    adapter = StructuredP10Client(client, Package())
    adapter.open_session("case:P10")
    with pytest.raises(ValueError):
        adapter.send_message("session", "sup:70410", {"body": COST, "term_sheets": [{"terms": TERMS}], **patch})
    assert client.moves == []


def test_other_cases_are_rejected_before_opening():
    adapter = StructuredP10Client(Client(), Package())
    with pytest.raises(ValueError, match="another case"):
        adapter.open_session("case:P18")


def test_session_inventory_remains_callable_for_admission_control():
    from aeiou.arena_sessions import open_sessions

    adapter = StructuredP10Client(Client(), Package())
    assert adapter.sessions("practice")[0]["set"] == "practice"
    assert open_sessions(adapter) == []


def test_inconsistent_standing_id_is_not_made_acceptable():
    class StaleClient(Client):
        def thread(self, session, supplier):
            return {**super().thread(session, supplier), "offer_id": "offer:stale"}

    adapter = StructuredP10Client(StaleClient(), Package())
    adapter.open_session("case:P10")
    with pytest.raises(ValueError, match="standing offer are inconsistent"):
        adapter.send_message("session", "sup:70410", {"body": COST, "term_sheets": [{"terms": TERMS}]})
