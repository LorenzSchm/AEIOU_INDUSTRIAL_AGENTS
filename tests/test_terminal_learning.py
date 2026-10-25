import copy

from test_custom_model import recorded

from aeiou.terminal_learning import terminal_label, terminal_rows


def test_final_quote_without_execution_is_censored_not_a_failed_deal():
    run, case, seller, buyer, reply = recorded()
    reply["status"] = "final_offer"
    label, reason = terminal_label(case, run["threads"]["sup:A"])
    assert label is None and reason == "censored"
    assert terminal_rows(run)[0] == []
    reply["status"] = "walked_away"
    label, reason = terminal_label(case, run["threads"]["sup:A"])
    assert label == {"terminal_deal": 0, "terminal_gain": 0}
    assert reason == "observed_exit"


def test_future_agreement_changes_label_without_changing_predecision_features():
    run, case, seller, buyer, reply = recorded()
    agreed = copy.deepcopy(reply)
    agreed["status"] = "agreed"
    run["threads"]["sup:A"] += [{"from": "buyer", "action": {"type": "accept"}}, agreed]
    first, _ = terminal_rows(run)
    assert first and first[0]["y"]["terminal_deal"] == 1
    altered = copy.deepcopy(run)
    altered["threads"]["sup:A"][-1]["term_sheets"][0]["terms"]["unit_price"] -= 5
    altered["result"] = {"score": 1, "hidden": "ignored"}
    second, _ = terminal_rows(altered)
    assert first[0]["v"] == second[0]["v"]
    assert first[0]["y"]["terminal_gain"] != second[0]["y"]["terminal_gain"]
    assert first[0]["past_targets"]["terminal_gain"] == {"sum": 0, "count": 0}
