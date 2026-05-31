"""Tests for on-disk state: save/load, discovery, archive, atomicity."""

import os

import pytest

from waypoint import model, store


def test_save_load_roundtrip(tmp_path):
    root = str(tmp_path)
    t = model.new_task("t1", "goal one")
    store.save(root, t)
    loaded = store.load(root, "t1")
    assert loaded["task_id"] == "t1"
    # STATUS.md is regenerated alongside.
    assert os.path.isfile(os.path.join(store.task_dir(root, "t1"), "STATUS.md"))


def test_save_rejects_invalid(tmp_path):
    root = str(tmp_path)
    t = model.new_task("t1", "g")
    t["status"] = "bogus"
    with pytest.raises(ValueError):
        store.save(root, t)


def test_active_tasks_filters_status(tmp_path):
    root = str(tmp_path)
    a = model.new_task("a", "g")
    b = model.new_task("b", "g")
    b["status"] = model.COMPLETED
    store.save(root, a)
    store.save(root, b)
    active = dict(store.active_tasks(root))
    assert "a" in active and "b" not in active


def test_archive_moves_not_deletes(tmp_path):
    root = str(tmp_path)
    t = model.new_task("t1", "g")
    store.save(root, t)
    dst = store.archive(root, "t1")
    assert os.path.isdir(dst)
    assert not os.path.isdir(store.task_dir(root, "t1"))
    assert "archive" in dst


def test_save_is_atomic_no_tmp_left(tmp_path):
    root = str(tmp_path)
    t = model.new_task("t1", "g")
    store.save(root, t)
    store.save(root, t)  # overwrite
    tdir = store.task_dir(root, "t1")
    leftovers = [f for f in os.listdir(tdir) if ".tmp." in f]
    assert leftovers == []


def test_load_migrates_legacy_pending_to_plan(tmp_path):
    import json, os
    root = str(tmp_path)
    tdir = store.task_dir(root, "t1")
    os.makedirs(tdir, exist_ok=True)
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00",
        "steps": [{"id": "a", "purpose": "first", "status": "succeeded"}],
        "current_step": None, "pending": [{"id": "b", "purpose": "second"}],
    }
    with open(store.state_path(root, "t1"), "w") as fh:
        json.dump(legacy, fh)
    t = store.load(root, "t1")
    assert [p["id"] for p in t["plan"]] == ["a", "b"]
    assert "pending" not in t


def test_waypoint_dir_points_under_dot_claude(tmp_path):
    import os
    root = str(tmp_path)
    assert store.waypoint_dir(root) == os.path.join(root, ".claude", "waypoint")
