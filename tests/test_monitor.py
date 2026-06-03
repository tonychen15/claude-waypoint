"""Tests for the read-only monitor render (pure function)."""

from waypoint import model, monitor


def _task():
    t = model.new_task("t1", "build the thing")
    t["plan"] = [{"id": "a", "purpose": "first"}, {"id": "b", "purpose": "second"}]
    t["steps"].append({"id": "a", "purpose": "first", "status": "succeeded"})
    return t


def test_render_no_worker_activity():
    out = monitor.render(_task(), {"heartbeat_age": None, "events": []})
    assert "t1" in out
    assert "1 of 2 done" in out                 # progress line
    assert "no worker activity yet" in out


def test_render_active_worker_and_events():
    snap = {"heartbeat_age": 8.0,
            "events": [{"ts": "2026-06-01T00:00:00+00:00",
                        "kind": "notification", "message": "waiting"}]}
    out = monitor.render(_task(), snap)
    assert "active" in out and "8s ago" in out
    assert "notification" in out and "waiting" in out


def test_render_idle_worker_formats_minutes():
    out = monitor.render(_task(), {"heartbeat_age": 305.0, "events": []})
    assert "idle" in out and "5m" in out
