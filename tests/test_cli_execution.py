import sys

import pytest

from aeiou import cli, runner


@pytest.mark.parametrize("limit,left,expected", [(None, 174, 2), (1, 174, 1), (4, 1, 1), (2, 0, 0)])
def test_hosted_practice_respects_attempt_cap_and_uses_original(monkeypatch, limit, left, expected):
    calls = []

    class Client:
        def cases(self, category):
            assert category == "practice"
            return [{"case_id": f"case:P{i:02}"} for i in range(10)]

        def me(self):
            return {"practice": {"left": left}}

    def play(case_id, agent, client):
        calls.append((case_id, agent))
        return {"case_id": case_id, "status": "completed"}

    argv = ["aeiou", "practice", "--agent", "original"] + (
        ["--max-sessions", str(limit)] if limit is not None else [])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "ArenaClient", Client)
    monkeypatch.setattr(cli, "hosted", lambda cases: cases)
    monkeypatch.setattr(runner, "hosted_play", play)
    cli.main()
    assert len(calls) == expected
    assert all(agent == "original" for _, agent in calls)


def test_final_launch_uses_original_prompt_without_opening_a_session(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(sys, "argv", ["aeiou", "final", "--waves", "1"])
    monkeypatch.setattr(cli, "ArenaClient", lambda: object())
    monkeypatch.setattr(cli.config, "state_root", lambda: tmp_path)
    monkeypatch.setattr(runner, "make_agent", lambda name: created.append(name) or object())

    def waves(client, factory, **kwargs):
        factory()
        return {}

    monkeypatch.setattr(cli, "play_waves", waves)
    cli.main()
    assert created == ["original"]
