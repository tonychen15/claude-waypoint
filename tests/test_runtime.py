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
