import copy
import json
import math
import time

import pytest
from arena_kit.cost import complete_offer, total_cost
from arena_kit.play import play
from arena_kit.sim import Sim

from aeiou.agent import Negotiator
from aeiou.config import data_root
from aeiou.optimizer import candidate_packages, estimate, evaluate, safe_close, valid_terms
from aeiou.research import package, research
from aeiou.simulation import OPPONENTS, LocalTable, local_cases, local_play
from aeiou.store import new_run


@pytest.fixture(scope="module")
def pkg():
    return package()


@pytest.fixture
def opened(pkg):
    case = local_cases()["case:S01"]
    sim = Sim({case["case_id"]: case}, no_clock=True)
    table = LocalTable(sim, new_run(case["case_id"], "local", "adaptive"))
    c, threads = table.open(case["case_id"])
    return c, threads


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, True, "15.0", {}])
def test_nonfinite_and_malformed_price_rejected(value):
    c = local_cases()["case:S01"]
    assert not valid_terms(c, {"unit_price": value, "payment_days": 30})


def test_must_have_cannot_be_traded_for_discount():
    case = local_cases()["case:T02"]
    terms = complete_offer(case, {"unit_price": 1, "volume_per_year": 6000})
    assert not valid_terms(case, terms)


def test_short_horizon_discount_can_cost_more():
    case = local_cases()["case:S03"]
    full = complete_offer(case, {"unit_price": 200, "contract_months": 24})
    short = {**full, "unit_price": 180, "contract_months": 12}
    assert total_cost(case, short) > total_cost(case, full)


def test_model_cannot_accept_first_quote_or_invent_supplier(pkg, opened):
    case, threads = opened
    agent = Negotiator(pkg)
    agent.prepare(case)
    assert agent.guard(case, threads, {"supplier_id": "sup:70270", "action": "accept"}, {}) is None
    assert agent.guard(case, threads, {"supplier_id": "sup:fake", "action": "send"}, {}) is None


def test_fabricated_citation_drops_whole_claim(pkg, opened):
    case, threads = opened
    agent = Negotiator(pkg)
    agent.prepare(case)
    result = agent.guard(
        case,
        threads,
        {
            "supplier_id": "sup:70270",
            "action": "send",
            "body": "Your costs fell 90% (idx:FAKE@2026-08). Thank you for the quotation.",
            "term_sheets": [{"unit_price": 15, "payment_days": 60}],
        },
        {},
    )
    assert result is not None
    assert "90%" not in result["message"]["body"]
    assert "FAKE" not in result["message"]["body"]


def test_broken_provider_uses_valid_guarded_move(pkg, opened):
    class Broken:
        def complete(self, *_):
            raise TimeoutError("simulated timeout")

    case, threads = opened
    agent = Negotiator(pkg, Broken())
    move = agent.decide(case, threads)
    assert agent.fallbacks == 1
    assert move["message"]["term_sheets"]
    assert all(valid_terms(case, s["terms"]) for s in move["message"]["term_sheets"])


def test_walkaway_does_not_discard_safe_offer(pkg, opened):
    case, threads = opened
    agent = Negotiator(pkg)
    agent.prepare(case)
    assert agent.guard(case, threads, {"supplier_id": "sup:70270", "action": "walk_away"}, {}) is None
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 1}}
    assert safe_close(case, threads, agent.intelligence)["message"]["action"]["type"] == "walk_away"


def test_offered_payment_is_present_in_supplier_financing_reference():
    case = local_cases()["case:S01"]
    info = {
        "estimates": {"sup:70270": {"full_cost": 10}},
        "facts": {"sup:70270": {"payment_days": 30, "incoterm": "FCA"}},
    }
    value = estimate(case, "sup:70270", {"ppm_commitment": 500}, info)
    assert value["standard"]["payment_days"] == 30
    assert value["standard"]["incoterm"] == "FCA"


def test_deadline_closes_without_waiting_for_model(pkg, monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))

    class NeverCall:
        def complete(self, *_):
            raise AssertionError("the cap must bypass Claude")

    case = local_cases()["case:S01"]
    agent = Negotiator(pkg, NeverCall())
    table = LocalTable(Sim({case["case_id"]: case}, no_clock=True), new_run(case["case_id"], "local", "claude"))
    result = play(table, case["case_id"], agent, deadline=time.time() + 10)
    assert result["deal"]
    assert agent.fallbacks == 0


def test_research_finds_supplier_notes_beyond_case_refs(pkg):
    case = json.loads((data_root() / "cases/practice/P01.json").read_text())
    info = research(case, pkg)
    ids = {d["id"] for d in info["documents"]}
    assert "doc:20260902-70110-M2" in ids
    assert any(ref not in case["refs"] for ref in ids)
    for index in info["indices"]:
        a, b = [info["facts"][ref]["value"] for ref in index["refs"]]
        assert index["change_pct"] == pytest.approx((b / a - 1) * 100, abs=0.0001)


@pytest.mark.parametrize("case_id", ["P02", "P03"])
def test_strategic_cases_with_payment_already_in_offer_are_scorable(pkg, case_id):
    case = json.loads((data_root() / f"cases/practice/{case_id}.json").read_text())
    sid = case["suppliers"][0]
    threads = {
        sid: [
            {
                "from": "supplier",
                "status": "open",
                "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 440, "payment_days": 30}}],
            }
        ]
    }
    info = research(case, pkg)
    assert math.isfinite(evaluate(case, threads, info)[0]["cost"])
    candidates = candidate_packages(case, threads[sid], sid, info)
    assert candidates and all(math.isfinite(p["cost"]) for p in candidates)


@pytest.mark.parametrize("opponent", list(OPPONENTS))
def test_heldout_opponents_beat_baseline_without_constraint_violations(pkg, opponent, monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    base, challenger = [], []
    for case_id in local_cases():
        b = local_play(case_id, "baseline", opponent, 99, pkg=pkg)
        a = local_play(case_id, "adaptive", opponent, 99, pkg=pkg)
        assert a["status"] == "completed", a.get("error")
        assert a["result"]["must_haves_ok"] is not False
        base.append(b["result"]["score"])
        challenger.append(a["result"]["score"])
    assert sum(challenger) > sum(base)


def test_rfq_does_not_accept_before_contacting_every_supplier(pkg):
    case = copy.deepcopy(local_cases()["case:S02"])
    sim = Sim({case["case_id"]: case}, no_clock=True)
    data = sim.open("test", case["case_id"])
    threads = data["threads"]
    sid = case["suppliers"][0]
    threads[sid].append({"from": "buyer", "body": "counter"})
    threads[sid].append(
        {
            "from": "supplier",
            "status": "agreed",
            "term_sheets": [{"term_sheet_id": "offer:safe", "terms": {"unit_price": 10}}],
        }
    )
    agent = Negotiator(pkg)
    agent.prepare(case)
    assert agent.guard(case, threads, {"supplier_id": sid, "action": "accept"}, {}) is None
