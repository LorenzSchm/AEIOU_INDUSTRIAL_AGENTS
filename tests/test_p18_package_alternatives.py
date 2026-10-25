import copy
import json

import pytest
from arena_kit.cost import total_cost
from assessment_helpers import assessment_fields
from test_negotiation_data_pipeline import NeverCall, scenario

from aeiou.config import ROOT
from aeiou.execution_policy import controlled_action, validate_policy
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package

POLICY = json.loads((ROOT / "config/p18-package-alternatives.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v16.txt").read_text()


def agent(backend=None, policy=None):
    return PromptLabAgent(
        package(),
        backend or NeverCall(),
        PromptSpec(
            "p18-package-alternatives",
            STRATEGY,
            assessment_enabled=True,
            execution_policy=copy.deepcopy(POLICY if policy is None else policy),
        ),
    )


def second_reply(duration=36, status="open"):
    case, sid, threads = scenario(5.40, "open")
    for event in threads[sid][1:]:
        event["term_sheets"][0]["terms"]["contract_months"] = duration
    threads[sid][1]["term_sheets"][0]["terms"]["unit_price"] = 4.60
    buyer = copy.deepcopy(threads[sid][1])
    buyer["term_sheets"][0]["terms"]["unit_price"] = 4.72
    seller = copy.deepcopy(threads[sid][-1])
    seller.update(status=status, body="Thank you for reviewing our calculation. Our revised offer is EUR 5.20/kg.")
    seller["term_sheets"][0]["term_sheet_id"] = "offer:second"
    seller["term_sheets"][0]["terms"]["unit_price"] = 5.20
    threads[sid].extend([buyer, seller])
    return case, sid, threads


def action_at(a, case, threads):
    return controlled_action(
        case, threads, a.decision_protocol_state(case, threads), a.acceptance_floor, a.execution_policy
    )


@pytest.mark.parametrize("duration", [24, 36])
def test_two_qualifying_alternatives_replace_third_price_counter_before_final(duration):
    case, sid, threads = second_reply(duration)
    a = agent()
    move = a.decide(case, threads)
    proposals = [s["terms"] for s in move["message"]["term_sheets"]]
    assert proposals == [{**p, "contract_months": duration} for p in POLICY["early_package_alternatives"]["packages"]]
    assert [total_cost(case, p) for p in proposals] == [243971.22, 243915.66]
    for p in proposals:
        assert (295173.23 - total_cost(case, p)) / (295173.23 - 222651.27) * 100 > 70.01
        assert set(p) == set(case["negotiable"])
    assert move["supplier_id"] == sid
    assert "new proposals for your approval" in move["message"]["body"]
    assert "5.05/kg" in move["message"]["body"] and "5.10/kg" in move["message"]["body"]
    assert a.decisions[-1]["source"] == "policy:package-alternatives"
    assert a.decisions[-1]["assessment"]["next_move"] == "Send the package menu once and request a binding offer."
    assert not a.audit["contract_rejections"]
    assert not a.decisions[-1]["numeric_checks"]
    assert not any(word in move["message"]["body"].lower() for word in ("score", "ceiling", "244400", "70.01"))


def test_menu_waits_for_two_priced_proposals_not_empty_questions():
    case, sid, threads = second_reply()
    a = agent()
    for length in (1, 3):
        assert action_at(a, case, {sid: threads[sid][:length]}) is None
    threads[sid][3]["term_sheets"] = []
    assert action_at(a, case, threads) is None


@pytest.mark.parametrize("status", ["final_offer", "walked_away", "no_bid"])
def test_automatic_menu_is_only_sent_while_open(status):
    case, sid, threads = second_reply(status=status)
    assert action_at(agent(), case, threads) is None


def test_no_menu_when_rounds_exhausted_or_duration_too_short():
    case, sid, threads = second_reply(12)
    assert action_at(agent(), case, threads) is None
    case, sid, threads = second_reply()
    case["round_limit"] = 2
    assert action_at(agent(), case, threads) is None


@pytest.mark.parametrize("which", [0, 1])
def test_binding_reply_accepts_exact_package_and_acceptance_precedes_menu(which):
    case, sid, threads = second_reply()
    offered = {**POLICY["early_package_alternatives"]["packages"][which], "contract_months": 36}
    # Even at the menu trigger, an eligible qualifying offer must be accepted first.
    threads[sid][-1]["term_sheets"][0]["terms"] = offered
    a = agent()
    assert a.decide(case, threads)["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:second"}
    assert a.decisions[-1]["source"] == "policy:accept"


@pytest.mark.parametrize("model_wording", [False, True])
def test_menu_survives_reconstruction_and_never_triggers_old_final_counter(model_wording):
    case, sid, threads = second_reply()
    move = agent().decide(case, threads)
    message = copy.deepcopy(move["message"])
    if model_wording:
        message["body"] = "Please approve one of the attached alternatives."
        message["term_sheets"].reverse()
    threads[sid].append({**message, "from": "buyer"})
    reply = copy.deepcopy(threads[sid][-2])
    reply["term_sheets"][0]["term_sheet_id"] = "offer:new-id"
    reply["term_sheets"][0]["terms"]["contract_months"] = 24
    threads[sid].append(reply)
    # The old counter stays suppressed even if a future config enables it again.
    policy = {**POLICY, "one_final_counter": True}
    for status in ("open", "final_offer"):
        threads[sid][-1]["status"] = status
        assert action_at(agent(policy=policy), case, threads) is None


def test_after_menu_model_can_address_objection_and_binding_reply_is_accepted():
    case, sid, threads = second_reply()
    menu = agent().decide(case, threads)
    threads[sid].extend([{**menu["message"], "from": "buyer"}, copy.deepcopy(threads[sid][-1])])
    threads[sid][-1]["body"] = "We need to review the 5% deadband with the owner."

    class Followup:
        def complete(self, system, user):
            payload = json.loads(user)
            return json.dumps(
                {
                    "supplier_id": sid,
                    "action": "send",
                    "term_sheets": [],
                    "body": "Thank you. Could your owner review Option A with 14-day payment?",
                    **assessment_fields(payload, sid),
                }
            )

    followup = agent(Followup()).decide(case, threads)
    assert followup["message"]["term_sheets"] == []
    threads[sid].append({**followup["message"], "from": "buyer"})
    approved = copy.deepcopy(threads[sid][-2])
    approved["term_sheets"] = [{"term_sheet_id": "offer:approved", "terms": menu["message"]["term_sheets"][0]["terms"]}]
    threads[sid].append(approved)
    assert agent().decide(case, threads)["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:approved"}


def test_advertised_nonstanding_alternative_is_never_accepted():
    case, sid, threads = second_reply()
    option = {**POLICY["early_package_alternatives"]["packages"][0], "contract_months": 36}
    threads[sid][-1]["standing_offer_id"] = "offer:second"
    threads[sid][-1]["term_sheets"].append({"term_sheet_id": "advertised", "terms": option})
    a = agent()
    with pytest.raises(ContractError):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "advertised"})
    assert a.decide(case, threads)["message"].get("action") is None


@pytest.mark.parametrize(
    "key,value",
    [("after_buyer_proposals", True), ("after_buyer_proposals", 0), ("packages", []), ("packages", [None, None])],
)
def test_invalid_menu_config_is_rejected(key, value):
    policy = copy.deepcopy(POLICY)
    policy["early_package_alternatives"][key] = value
    with pytest.raises(ValueError):
        validate_policy(policy, agent().acceptance_floor)


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit_price", float("nan")),
        ("unit_price", 5.11),
        ("payment_days", True),
        ("payment_days", 20),
        ("index_clause", None),
        ("index_clause", {}),
        ("unit_price", 5.06),
    ],
)
def test_invalid_or_overbudget_packages_fail_before_run(field, value):
    policy = copy.deepcopy(POLICY)
    policy["early_package_alternatives"]["packages"][0][field] = value
    case, _, _ = second_reply()
    with pytest.raises(ValueError):
        agent(policy=policy).prepare(case)


def test_runtime_cost_check_never_sends_one_valid_and_one_overbudget_option():
    case, _, threads = second_reply()
    a = agent()
    a.prepare(case)
    # A newly discovered cost input must be rechecked at decision time.
    case["buyer"]["index_paths"]["AS-14404"] = 0.20
    assert action_at(a, case, threads) is None


def test_outgoing_model_package_still_checks_total_cost_when_final_counter_disabled():
    case, sid, threads = second_reply()
    a = agent(policy={**POLICY, "adapt_closing_price_to_budget": False})
    terms = threads[sid][-1]["term_sheets"][0]["terms"]
    with pytest.raises(ContractError, match="complete-cost"):
        a.contract(
            case,
            threads,
            {
                "supplier_id": sid,
                "action": "send",
                "body": "Please consider this.",
                "term_sheets": [{**terms, "unit_price": 5.10}],
            },
        )


def test_policy_is_case_scoped():
    case, _, threads = second_reply()
    case["case_id"] = "case:P19"
    a = agent()
    with pytest.raises(ValueError):
        a.prepare(case)
    assert controlled_action(case, threads, {}, a.acceptance_floor, a.execution_policy) is None


def standard_payment_agent():
    return agent_with_spec(
        "p18-early-payment-menu", "prompts/p18-diagnostic-v18.txt", "config/p18-early-payment-menu.json"
    )


def agent_with_spec(name, strategy_path, policy_path):
    return PromptLabAgent(
        package(),
        NeverCall(),
        PromptSpec(
            name,
            (ROOT / strategy_path).read_text(),
            assessment_enabled=True,
            execution_policy=json.loads((ROOT / policy_path).read_text())["case:P18"],
        ),
    )


@pytest.mark.parametrize("duration", [24, 36])
def test_standard_payment_menu_is_sent_after_first_reply_with_all_three_qualifying_packages(duration):
    case, sid, threads = second_reply(duration)
    threads[sid] = threads[sid][:3]
    for event in threads[sid]:
        event["term_sheets"][0]["terms"]["payment_days"] = 30
    a = standard_payment_agent()
    assert action_at(a, case, {sid: threads[sid][:1]}) is None
    move = a.decide(case, threads)
    terms = [s["terms"] for s in move["message"]["term_sheets"]]
    assert [(p["unit_price"], p["payment_days"]) for p in terms] == [(5.07, 30), (5.05, 14), (5.10, 60)]
    assert [total_cost(case, p) for p in terms] == [244082.98, 243971.22, 243915.66]
    assert all(p["contract_months"] == duration and p["index_clause"]["deadband_pct"] == 5 for p in terms)
    assert all(a.acceptance_floor.allows(total_cost(case, p), p) for p in terms)
    assert a.decisions[-1]["source"] == "policy:package-alternatives"
    assert a.decisions[-1]["message_index"] == 3
    assert "Option A: EUR 5.07/kg, payment within 30 days" in move["message"]["body"]
    assert "If none works" in move["message"]["body"]


@pytest.mark.parametrize("option", [0, 1, 2])
def test_each_new_menu_option_can_be_accepted_before_another_counter(option):
    case, sid, threads = second_reply()
    threads[sid] = threads[sid][:3]
    a = standard_payment_agent()
    terms = {**a.execution_policy["early_package_alternatives"]["packages"][option], "contract_months": 36}
    threads[sid][-1]["term_sheets"][0]["terms"] = terms
    move = a.decide(case, threads)
    assert move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:current"}
    assert a.decisions[-1]["source"] == "policy:accept"


def test_prior_standard_payment_proposal_prevents_repeated_menu():
    case, sid, threads = second_reply()
    a = standard_payment_agent()
    threads[sid][3]["term_sheets"][0]["terms"] = {
        **a.execution_policy["early_package_alternatives"]["packages"][0],
        "contract_months": 36,
    }
    for status in ("open", "final_offer"):
        threads[sid][-1]["status"] = status
        assert action_at(standard_payment_agent(), case, threads) is None
