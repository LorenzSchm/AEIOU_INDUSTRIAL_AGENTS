import copy

import pytest

from aeiou.grounding import COMMERCIAL_CONTEXT, compose_email, responsive_act
from aeiou.simulation import local_cases


def situation():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["negotiable"] += ["contract_months", "effective_date"]
    case["fixed_terms"].pop("contract_months")
    terms = {"unit_price": 20, "payment_days": 30, "contract_months": 12, "effective_date": "2028-01-01"}
    events = [
        {"from": "supplier", "status": "open", "body": "Please review our offer.", "term_sheets": [{"terms": terms}]}
    ]
    return case, events, terms


@pytest.mark.parametrize("kind", ["date_transition", "payment_review", "continuity_review"])
def test_specific_explanation_comes_from_real_terms_without_mutating_them(kind):
    case, events, terms = situation()
    proposal = dict(terms)
    if kind == "date_transition":
        proposal["effective_date"] = "2028-04-01"
    elif kind == "payment_review":
        events[0]["body"] = "Our base prices are frozen. Please review payment terms."
        proposal["payment_days"] = 60
    else:
        proposal["contract_months"] = 24
    before = copy.deepcopy((case, events, proposal))
    act = responsive_act({"question": "exchange"}, events, case=case, terms=[proposal])
    assert act["commercial_context"] == kind
    text = compose_email(act, [], "supplier")
    assert all(part in text for part in COMMERCIAL_CONTEXT[kind])
    assert text.count("?") == 1
    assert "conditional on the executable terms" in text
    assert (case, events, proposal) == before


def test_model_cannot_claim_unsent_commitment_or_invent_context():
    case, events, terms = situation()
    act = responsive_act(
        {"commercial_context": "continuity_review", "repeat_context": True}, events, case=case, terms=[terms]
    )
    assert act["commercial_context"] is None
    assert "longer contractual commitment" not in compose_email(act, [], "supplier")
    with pytest.raises(ValueError, match="Unknown commercial context"):
        compose_email({"commercial_context": "We promise future orders"}, [], "supplier")


def test_does_not_describe_a_longer_contract_when_already_in_current_menu():
    case, events, terms = situation()
    longer = {**terms, "contract_months": 24}
    events[0]["term_sheets"].append({"terms": longer})
    assert responsive_act({}, events, case=case, terms=[longer])["commercial_context"] is None


def test_repeated_brief_is_omitted_but_specific_question_remains():
    case, events, terms = situation()
    proposal = {**terms, "contract_months": 24}
    first = responsive_act({}, events, case=case, terms=[proposal])
    text = compose_email(first, [], "supplier")
    repeated = [events[0], {"from": "buyer", "body": text}, copy.deepcopy(events[0])]
    second = compose_email(responsive_act({}, repeated, case=case, terms=[proposal]), [], "supplier")
    assert COMMERCIAL_CONTEXT["continuity_review"][0] not in second
    assert COMMERCIAL_CONTEXT["continuity_review"][1] in second


@pytest.mark.parametrize("status", ["final_offer", "agreed"])
def test_no_new_review_request_on_terminal_offer(status):
    case, events, terms = situation()
    events[0]["status"] = status
    assert responsive_act({}, events, case=case, terms=[{**terms, "contract_months": 24}])["commercial_context"] is None


def test_fixed_duration_and_dates_do_not_activate_new_context():
    case, events, terms = situation()
    case["negotiable"] = ["unit_price", "payment_days"]
    events[0]["body"] = "Our prices are frozen and require management approval."
    assert responsive_act({}, events, case=case, terms=[{**terms, "payment_days": 60}])["commercial_context"] is None


def test_complete_bundle_requests_approval_without_claiming_private_authority_rules():
    case, events, terms = situation()
    case.update(type="bundling", parts=["part:new-a", "part:new-b"])
    before = copy.deepcopy((case, events, terms))
    act = responsive_act({}, events, case=case, terms=[terms])
    assert act["commercial_context"] == "bundle_review"
    email = compose_email(act, [], "supplier")
    assert "complete kit" in email and "pricing review" in email
    assert "committee" not in email and "2%" not in email and "exclusive" not in email
    assert email.count("?") == 1
    assert (case, events, terms) == before


@pytest.mark.parametrize("change", ["single_part", "not_bundle", "extra_volume", "short_coverage"])
def test_bundle_claim_requires_actual_complete_scope(change):
    case, events, terms = situation()
    case.update(type="bundling", parts=["part:new-a", "part:new-b"])
    proposal = dict(terms)
    if change == "single_part":
        case["parts"] = ["part:new-a"]
    elif change == "not_bundle":
        case["type"] = "renewal"
    elif change == "extra_volume":
        proposal["volume_per_year"] = case["buyer"]["need_per_year"] + 1
    else:
        case["buyer"]["horizon_years"] = 2
    act = responsive_act({}, events, case=case, terms=[proposal])
    assert act["commercial_context"] not in {"bundle_review", "bundle_reciprocity"}


def test_bundle_concession_is_acknowledged_only_on_comparable_previous_terms():
    case, events, terms = situation()
    case.update(type="bundling", parts=["part:new-a", "part:new-b"])
    previous = {**terms, "unit_price": 18}
    events = [events[0], {"from": "buyer", "term_sheets": [{"terms": previous}]}, copy.deepcopy(events[0])]
    proposal = {**previous, "unit_price": 18.2}
    act = responsive_act({}, events, case=case, terms=[proposal])
    assert act["commercial_context"] == "bundle_reciprocity"
    proposal["payment_days"] = 60
    assert responsive_act({}, events, case=case, terms=[proposal])["commercial_context"] == "bundle_review"
