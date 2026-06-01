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
