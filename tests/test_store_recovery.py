from aeiou import store


def test_locked_target_preserves_latest_snapshot_without_aborting_negotiation(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    run = store.new_run("case:D02", "hosted", "strategic")
    store.save_run(run)
    original = store.os.replace

    def locked(*args):
        raise PermissionError("simulated Windows lock")

    monkeypatch.setattr(store.os, "replace", locked)
    monkeypatch.setattr(store.time, "sleep", lambda seconds: None)
    run["status"] = "completed"
    store.save_run(run)
    assert store.get_run(run["id"])["status"] == "completed"
    assert len(store.list_runs()) == 1
    assert store.list_runs()[0]["status"] == "completed"
    monkeypatch.setattr(store.os, "replace", original)
    run["result"] = {"score": 0.5}
    store.save_run(run)
    assert store.get_run(run["id"])["result"]["score"] == 0.5


def test_transient_lock_is_retried_before_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    original = store.os.replace
    calls = []

    def transient(*args):
        calls.append(args)
        if len(calls) == 1:
            raise PermissionError("reader holds file briefly")
        return original(*args)

    monkeypatch.setattr(store.os, "replace", transient)
    monkeypatch.setattr(store.time, "sleep", lambda seconds: None)
    run = store.new_run("case:D01", "hosted", "strategic")
    store.save_run(run)
    assert len(calls) == 2
    assert store.get_run(run["id"]) == run
    assert not (tmp_path / "runs" / "recovery").exists()
