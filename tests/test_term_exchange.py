import copy
import json
from pathlib import Path

from aeiou.grounding import responsive_act
from aeiou.research import package
from aeiou.strategic import StrategicNegotiator
from aeiou.term_exchange import exchange_history, ranked_exchanges


def opening():
    data = json.loads((Path(__file__).parent / "fixtures/d01_public_opening.json").read_text())
    return data["case"], data["threads"]


def experiment(bundled=False):
    case, threads = opening()
    events = next(iter(threads.values()))
    first = events[0]["term_sheets"][0]["terms"]
    a = {**first, "payment_days": 60, "contract_months": 24, "unit_price": 49}
    b = {**a, "payment_days": 30, "unit_price": 48}
    if bundled:
        b["contract_months"] = 12
    events += [
        {"from": "buyer", "term_sheets": [{"terms": a}, {"terms": b}]},
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [{"terms": {**b, "unit_price": first["unit_price"] - 1}}],
        },
    ]
    return case, events, a


def test_learning_is_public_local_and_reconstructible():
    case, events, base = experiment()
    history = exchange_history(case, events)
    assert len(history) == 1
    assert history[0]["field"] == "payment_days"
    assert history[0]["value"] == 30
    assert history[0]["price_improvement"] == 1
    assert ranked_exchanges(case, base, history)[0]["value"] == 30
    assert exchange_history(case, copy.deepcopy(events)) == history
    assert exchange_history(case, events[:1]) == []


def test_bundled_and_ambiguous_replies_do_not_teach_single_term_preferences():
    case, events, _ = experiment(bundled=True)
    assert exchange_history(case, events) == []
    case, events, _ = experiment()
    events[-1]["term_sheets"] *= 2
    assert exchange_history(case, events) == []


def test_twice_unrewarded_value_is_retired_but_other_terms_remain():
    case, events, base = experiment()
    obs = exchange_history(case, events)[0]
    history = [{**obs, "price_improvement": 0}, {**obs, "price_improvement": -1}]
    options = ranked_exchanges(case, base, history)
    assert options
    assert not any(o["field"] == "payment_days" and o["value"] == 30 for o in options)


def test_d01_exchange_is_selected_cost_protected_and_conditional():
    case, threads = opening()
    agent = StrategicNegotiator(package())
    move = agent.decide(case, threads)
    plan = agent.support[move["supplier_id"]]["plan"]["packages"]
    base = next(p for p in plan if p["id"] == "A")
    swap = next(p for p in plan if p.get("exchange_probe"))
    assert swap["id"] in agent.controller["candidate_ids"]
    assert swap["cost"] <= base["cost"] + 0.001
    assert swap["terms"]["unit_price"] < base["terms"]["unit_price"]
    assert sum(swap["terms"][f] != base["terms"][f] for f in case["negotiable"] if f != "unit_price") == 1
    assert "conditional" in move["message"]["body"].lower()
    assert responsive_act({}, [], exchange_probe=True)["question"] == "exchange"
