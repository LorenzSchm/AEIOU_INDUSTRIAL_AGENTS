import copy

from test_custom_model import recorded

from aeiou.opening_finality import TARGET, opening_rows


def test_only_first_proposal_is_labeled_and_future_changes_cannot_change_inputs():
    run, _, _, buyer, reply = recorded()
    run["threads"]["sup:A"] += [copy.deepcopy(buyer), copy.deepcopy(reply)]
    before = opening_rows(run)
    assert len(before) == 1
    assert before[0]["y"][TARGET] == 0
    run["threads"]["sup:A"][2]["status"] = "final_offer"
    run["threads"]["sup:A"][2]["term_sheets"][0]["terms"]["unit_price"] += 100
    after = opening_rows(run)
    assert len(after) == 1
    assert after[0]["y"][TARGET] == 1
    assert before[0]["v"] == after[0]["v"]
    assert after[0]["past_targets"][TARGET] == {"sum": 0, "count": 0}


def test_missing_response_does_not_become_an_open_or_terminal_label():
    run, _, _, _, _ = recorded()
    run["threads"]["sup:A"] = run["threads"]["sup:A"][:2]
    assert opening_rows(run) == []
