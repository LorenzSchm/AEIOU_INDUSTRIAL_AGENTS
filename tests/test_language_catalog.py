import json

import pytest

from aeiou.language_sim import LanguageSim, SupplierSimulationError, confirms_package
from aeiou.negotiation_state import package_repeats
from aeiou.prompt_protocol import PromptLabAgent, prompt_specs
from aeiou.research import package
from aeiou.simulation import local_cases


def supplier(backend=None):
    sim = LanguageSim(local_cases(), "cashflow", 72, {}, backend)
    opened = sim.open("test", "case:T02")
    session = sim.sessions[opened["session_id"]]
    sid, thread = next(iter(session["threads"].items()))
    return sim, opened, session, sid, thread


def test_all_catalog_packages_are_executable_above_private_floor():
    sim, _, session, _, thread = supplier()
    catalog = sim.package_catalog(session["case"], thread, [])
    assert len(catalog) > 20
    for terms in catalog.values():
        sim._check(session["case"], terms, thread)
        assert terms["unit_price"] >= sim.required_price(thread["opening"], terms)


def test_agreement_only_available_for_current_feasible_buyer_proposals():
    sim, _, session, _, thread = supplier()
    terms = {"unit_price": 250, "payment_days": 60, "volume_per_year": 8490, "contract_months": 24}
    empty = sim.private_input(session, thread, {}, [])
    assert "agreed" not in empty["schema_state"]["allowed_statuses"]
    payload = sim.private_input(session, thread, {}, [terms])
    assert "agreed" in payload["schema_state"]["allowed_statuses"]
    pid = payload["agreement_package_ids"][0]
    resolved = sim.resolve_reply(payload, {"status": "agreed", "body": "Agreed", "package_ids": [pid]})
    assert resolved["term_sheets"] == [terms]
    other = next(p["id"] for p in payload["package_catalog"] if p["id"] != pid)
    with pytest.raises(ValueError, match="current agreement"):
        sim.resolve_reply(payload, {"status": "agreed", "package_ids": [other]})


def test_confirmation_can_repeat_current_quote_after_buyer_message():
    sim, _, session, _, thread = supplier()
    terms = {"unit_price": 250.13, "payment_days": 60, "volume_per_year": 8490, "contract_months": 24}
    thread["messages"][-1]["term_sheets"] = [{"terms": terms}]
    thread["messages"].append({"from": "buyer", "body": "Please repeat the same terms.", "term_sheets": []})
    assert terms in sim.package_catalog(session["case"], thread, []).values()


def test_invalid_reply_repair_is_json_and_never_substitutes_a_price():
    class Repair:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 1:
                return json.dumps({"status": "agreed", "body": "Yes", "package_ids": ["invented"]})
            assert "reply_repair" in payload
            assert "agreed" not in payload["schema_state"]["allowed_statuses"]
            p = max(payload["package_catalog"], key=lambda p: p["terms"]["unit_price"])
            self.selected = p["terms"]
            return json.dumps({"status": "final_offer", "body": "Our binding quotation.", "package_ids": [p["id"]]})

    backend = Repair()
    sim, opened, _, sid, _ = supplier(backend)
    reply = sim.message("test", opened["session_id"], sid, {"body": "Please confirm."})
    assert reply["term_sheets"][0]["terms"] == backend.selected
    assert sim.repairs == 1


def test_persistent_seller_schema_failure_stays_visible():
    class Invalid:
        def complete(self, *args):
            return '{"status":"open","body":"quote","package_ids":["invented"]}'

    sim, opened, _, sid, _ = supplier(Invalid())
    with pytest.raises(SupplierSimulationError):
        sim.message("test", opened["session_id"], sid, {"body": "Please quote."})
    assert len(sim.rejections) == 2


def test_explicit_repeat_choice_copies_binding_terms_without_forcing_seller_to_repeat():
    class Repeat:
        def complete(self, system, user):
            payload = json.loads(user)
            assert payload["confirmation_request"]["package_ids"]
            return json.dumps({"status": "final_offer", "body": "Confirmed unchanged.", "repeat_unchanged": True})

    sim = LanguageSim(local_cases(), "relationship", 71, {}, Repeat())
    opened = sim.open("test", "case:S01")
    s = sim.sessions[opened["session_id"]]
    sid, th = next(iter(s["threads"].items()))
    terms = {"unit_price": th["opening"], "payment_days": 14}
    th["messages"][-1]["term_sheets"][0]["terms"] = terms
    agent = PromptLabAgent(package(), None, prompt_specs()["original"])
    move = agent.contract(
        s["case"], {sid: th["messages"]}, {"supplier_id": sid, "action": "confirm", "offer_position": 1}
    )
    for _ in range(4):
        reply = sim.message("test", opened["session_id"], sid, move["message"])
        assert reply["term_sheets"][0]["terms"] == terms
    assert package_repeats(s["case"], th["messages"], terms) == 5
    payload = sim.private_input(s, th, move["message"], [])
    other = next(p["id"] for p in payload["package_catalog"] if p["terms"] != terms)
    # Choosing a counter rather than repeating remains legal; its price is not substituted.
    counter = sim.resolve_reply(
        payload,
        {"status": "open", "body": "These are revised terms.", "repeat_unchanged": False, "package_ids": [other]},
    )
    assert counter["term_sheets"] != [terms]
    with pytest.raises(ValueError, match="conflicts"):
        sim.resolve_reply(payload, {"status": "open", "repeat_unchanged": True, "package_ids": [other]})


def test_confirmation_matches_values_not_dictionary_order():
    terms = {"unit_price": 43.24, "payment_days": 60, "ppm_commitment": 800, "index_clause": None}

    def email(fields):
        return (
            "Please confirm that your complete offer remains exactly as follows: "
            + "; ".join(f"{k}: {v}" for k, v in fields)
            + ". Please repeat the identical binding package. This requests confirmation."
        )

    reordered = list(reversed(terms.items()))
    assert confirms_package(email(reordered), terms)
    assert confirms_package(email({**terms, "payment_days": 60.0}.items()), terms)
    assert not confirms_package(email({**terms, "unit_price": 43.25}.items()), terms)
    assert not confirms_package(email(reordered[1:]), terms)
    assert not confirms_package(email([*reordered, ("unit_price", 43.24)]), terms)
    assert not confirms_package(email({**terms, "payment_days": "NaN"}.items()), terms)


def test_missing_confirmation_does_not_silently_resolve_to_empty_offer():
    sim, _, session, _, thread = supplier()
    payload = sim.private_input(session, thread, {}, [])
    with pytest.raises(ValueError, match="recognized complete confirmation"):
        sim.resolve_reply(payload, {"status": "open", "repeat_unchanged": True, "package_ids": []})
