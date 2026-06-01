"""Tests for the autonomous guard decision logic (pure) + step (fake workers)."""

from waypoint import guard

CFG = {"idle_timeout": 600.0, "wait_timeout": 300.0, "max_no_progress": 2}


def _obs(**kw):
    base = {"task_status": "in_progress", "alive": True,
            "heartbeat_age": 1.0, "waiting_age": None, "committed": 0}
    base.update(kw)
    return base


def _g(**kw):
    base = {"fsm": guard.WATCHING, "no_progress": 0, "baseline_committed": 0}
    base.update(kw)
    return base


def test_healthy_worker_keeps_watching():
    action, _ = guard.decide(_obs(), _g(), CFG)
    assert action == guard.WATCH


def test_completed_task_is_done():
    action, new = guard.decide(_obs(task_status="completed"), _g(), CFG)
    assert action == guard.COMPLETE and new["fsm"] == guard.DONE


def test_dead_worker_triggers_takeover():
    action, new = guard.decide(_obs(alive=False), _g(), CFG)
    assert action == guard.TAKEOVER and new["fsm"] == guard.WATCHING


def test_heartbeat_timeout_triggers_takeover():
    action, _ = guard.decide(_obs(heartbeat_age=999.0), _g(), CFG)
    assert action == guard.TAKEOVER


def test_waiting_timeout_triggers_takeover():
    action, _ = guard.decide(_obs(waiting_age=999.0), _g(), CFG)
    assert action == guard.TAKEOVER


def test_fresh_worker_no_heartbeat_is_not_a_stall():
    action, _ = guard.decide(_obs(heartbeat_age=None), _g(), CFG)
    assert action == guard.WATCH


def test_progress_resets_no_progress_counter():
    action, new = guard.decide(_obs(alive=False, committed=3),
                               _g(no_progress=1, baseline_committed=1), CFG)
    assert action == guard.TAKEOVER and new["no_progress"] == 0
    assert new["baseline_committed"] == 3


def test_no_progress_takeovers_eventually_halt():
    g = _g(no_progress=1, baseline_committed=0)
    action, new = guard.decide(_obs(alive=False, committed=0), g, CFG)
    assert action == guard.HALT and new["fsm"] == guard.HALTED


def test_terminal_states_are_sticky():
    for fsm, act in ((guard.HALTED, guard.HALT), (guard.DONE, guard.COMPLETE)):
        action, new = guard.decide(_obs(alive=False), _g(fsm=fsm), CFG)
        assert action == act and new["fsm"] == fsm


def test_state_roundtrip_defaults(tmp_path):
    root = str(tmp_path)
    from waypoint import model, store
    store.save(root, model.new_task("t1", "g"))
    g = guard.load_state(root, "t1")
    assert g["fsm"] == guard.WATCHING and g["no_progress"] == 0
    g["no_progress"] = 2
    guard.save_state(root, "t1", g)
    assert guard.load_state(root, "t1")["no_progress"] == 2


def test_observe_reports_dead_worker(tmp_path):
    root = str(tmp_path)
    from waypoint import model, store
    store.save(root, model.new_task("t1", "g"))
    from waypoint import launcher, runtime
    import json, os
    os.makedirs(runtime.runtime_dir(root, "t1"), exist_ok=True)
    with open(launcher.worker_json_path(root, "t1"), "w") as fh:
        json.dump({"pid": 2 ** 31 - 1, "session_id": "s"}, fh)
    obs = guard.observe(root, "t1")
    assert obs["alive"] is False
    assert obs["task_status"] == "in_progress"
    assert obs["committed"] == 0


def test_observe_waiting_age_from_latest_notification(tmp_path):
    root = str(tmp_path)
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    runtime.append_event(root, "t1", "notification", message="waiting for input")
    obs = guard.observe(root, "t1")
    assert obs["waiting_age"] is not None and obs["waiting_age"] >= 0


def _stub(tmp_path, body="import time\ntime.sleep(30)\n"):
    p = tmp_path / "fakeclaude"
    p.write_text("#!/usr/bin/env python3\n" + body)
    p.chmod(0o755)
    return str(p)


def test_step_takes_over_dead_worker(tmp_path):
    import time
    from waypoint import launcher, model, store
    root = str(tmp_path)
    t = model.new_task("t1", "g")
    t["plan"] = [{"id": "a", "purpose": "p"}]
    store.save(root, t)
    stub = _stub(tmp_path)
    first = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    launcher.stop(root, "t1")
    time.sleep(0.3)
    action = guard.step(root, "t1", config=CFG, claude_bin=stub)
    try:
        assert action == guard.TAKEOVER
        assert launcher.worker_info(root, "t1")["pid"] != first["pid"]
    finally:
        launcher.stop(root, "t1")


def test_step_halts_after_no_progress(tmp_path):
    from waypoint import model, store
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    import json, os
    from waypoint import launcher, runtime
    os.makedirs(runtime.runtime_dir(root, "t1"), exist_ok=True)
    with open(launcher.worker_json_path(root, "t1"), "w") as fh:
        json.dump({"pid": 2 ** 31 - 1, "session_id": "s"}, fh)
    guard.save_state(root, "t1",
                     {"fsm": guard.WATCHING, "no_progress": 1,
                      "baseline_committed": 0})
    action = guard.step(root, "t1", config=CFG, claude_bin="claude")
    assert action == guard.HALT
    assert guard.load_state(root, "t1")["fsm"] == guard.HALTED


def test_notify_never_raises():
    guard.notify("title", "body")
