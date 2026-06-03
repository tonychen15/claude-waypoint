"""Tests for the Phase 2 runtime liveness store."""

import os
import time

from waypoint import runtime, store


def test_runtime_dir_under_task(tmp_path):
    root = str(tmp_path)
    assert runtime.runtime_dir(root, "t1") == os.path.join(
        store.task_dir(root, "t1"), "runtime")


def test_touch_heartbeat_creates_file_and_age_is_small(tmp_path):
    root = str(tmp_path)
    runtime.touch_heartbeat(root, "t1")
    age = runtime.heartbeat_age(root, "t1")
    assert age is not None and age < 2.0


def test_heartbeat_age_none_when_absent(tmp_path):
    assert runtime.heartbeat_age(str(tmp_path), "t1") is None


def test_heartbeat_age_reflects_old_mtime(tmp_path):
    root = str(tmp_path)
    runtime.touch_heartbeat(root, "t1")
    hb = os.path.join(runtime.runtime_dir(root, "t1"), "heartbeat")
    old = time.time() - 600
    os.utime(hb, (old, old))
    assert runtime.heartbeat_age(root, "t1") >= 590


def test_append_and_read_events_roundtrip(tmp_path):
    root = str(tmp_path)
    runtime.append_event(root, "t1", "notification", message="waiting")
    runtime.append_event(root, "t1", "turn_done")
    evs = runtime.read_events(root, "t1")
    assert [e["kind"] for e in evs] == ["notification", "turn_done"]
    assert evs[0]["message"] == "waiting"
    assert all("ts" in e for e in evs)


def test_read_events_limit_returns_last_n(tmp_path):
    root = str(tmp_path)
    for i in range(5):
        runtime.append_event(root, "t1", "turn_done", n=i)
    evs = runtime.read_events(root, "t1", limit=2)
    assert [e["n"] for e in evs] == [3, 4]


def test_read_events_empty_when_absent(tmp_path):
    assert runtime.read_events(str(tmp_path), "t1") == []


def test_snapshot_shape(tmp_path):
    root = str(tmp_path)
    runtime.touch_heartbeat(root, "t1")
    runtime.append_event(root, "t1", "turn_done")
    snap = runtime.snapshot(root, "t1")
    assert snap["heartbeat_age"] is not None
    assert snap["events"] and snap["events"][-1]["kind"] == "turn_done"


def test_corrupt_events_line_is_skipped(tmp_path):
    root = str(tmp_path)
    runtime.append_event(root, "t1", "turn_done")
    path = os.path.join(runtime.runtime_dir(root, "t1"), "events.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    runtime.append_event(root, "t1", "notification")
    kinds = [e["kind"] for e in runtime.read_events(root, "t1")]
    assert kinds == ["turn_done", "notification"]   # bad line skipped


def test_scoped_task_ids_prefers_env(tmp_path, monkeypatch):
    root = str(tmp_path)
    from waypoint import model, store
    store.save(root, model.new_task("a", "g"))
    store.save(root, model.new_task("b", "g"))
    monkeypatch.delenv("WAYPOINT_TASK_ID", raising=False)
    assert set(runtime.scoped_task_ids(root)) == {"a", "b"}   # fallback: all active
    monkeypatch.setenv("WAYPOINT_TASK_ID", "a")
    assert runtime.scoped_task_ids(root) == ["a"]             # scoped to the worker's task
