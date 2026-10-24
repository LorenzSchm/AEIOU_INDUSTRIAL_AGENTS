from arena_kit.client import ArenaError
from arena_kit.play import play_waves
from arena_kit.sim import Fail, Sim, load_cases

from aeiou.agent import Negotiator
from aeiou.research import package
from aeiou.runner import RecordingTable
from aeiou.store import list_runs, new_run


def test_recording_final_waits_resumes_and_plays_two_waves(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))

    class Clock:
        now = 1_000_000.0

        def __call__(self):
            return self.now

    clock = Clock()
    sim = Sim(load_cases(), clock=clock, no_clock=True)
    sim.rehearse(wait=0, cap=480, brk=0)
    existing = sim.open("test", "case:R1-S01")["session_id"]

    class Client:
        listings = 0
        paths = []

        def cases(self, set_):
            assert set_ == "final"
            self.paths.append("/cases")
            self.listings += 1
            assert self.listings < 10000, "Rehearsal did not progress through both waves"
            if self.listings == 1:
                raise ArenaError(403, "the final has not started")
            mine = list(sim.sessions.values())
            for wave in (1, 2):
                cases = [s for s in mine if s["wave"] == wave]
                if len(cases) == 3 and all(s["status"] == "ended" for s in cases):
                    clock.now = max(clock.now, 1_000_000 + 480 * wave)
            return sim.final_cases("test")

        def open_session(self, case_id):
            self.paths.append("/sessions")
            try:
                return sim.open("test", case_id)
            except Fail as e:
                raise ArenaError(e.status, e.detail) from e

        def send_message(self, session_id, supplier_id, message):
            self.paths.append("/threads/messages")
            return sim.message("test", session_id, supplier_id, message)

        def session(self, session_id):
            self.paths.append("/sessions/state")
            return sim.state("test", session_id)

        def thread(self, session_id, supplier_id):
            self.paths.append("/threads")
            return {"messages": sim.sessions[session_id]["threads"][supplier_id]["messages"]}

        def attachment(self, session_id, attachment_id):
            self.paths.append("/attachments")
            return sim.attachment("test", session_id, attachment_id)[0]

    class Table(RecordingTable):
        def __init__(self, client, case):
            super().__init__(client, new_run(case["case_id"], "final", "adaptive"), case)

    client, pkg = Client(), package()
    result = play_waves(client, lambda: Negotiator(pkg), waves=2, parallel=3, poll=0.001, table_factory=Table)
    assert len(result) == 6
    assert all(isinstance(r, dict) and r.get("must_haves_ok") for r in result.values())
    assert len(sim.sessions) == 6  # the pre-existing session was resumed, not duplicated
    assert sim.sessions[existing]["status"] == "ended"
    assert len(list_runs()) == 6
    assert all(r["status"] == "completed" for r in list_runs())
    assert set(client.paths) <= {
        "/cases",
        "/sessions",
        "/sessions/state",
        "/threads",
        "/threads/messages",
        "/attachments",
    }
