"""Tests for roadmap math and progress rendering."""

from waypoint import model, progress


def _task_with_plan():
    t = model.new_task("t1", "g")
    t["plan"] = [{"id": i, "purpose": p} for i, p in
                 (("a", "first"), ("b", "second"), ("c", "third"))]
    return t


def test_no_plan_summary_counts_committed_steps():
    t = model.new_task("t1", "g")
    t["steps"] = [{"id": "a", "purpose": "x", "status": "succeeded"},
                  {"id": "b", "purpose": "y", "status": "succeeded"}]
    assert progress.has_plan(t) is False
    line = progress.summary(t)
    assert "2 steps committed" in line and "no plan" in line


def test_plan_in_progress_summary():
    t = _task_with_plan()
    t["steps"] = [{"id": "a", "purpose": "first", "status": "succeeded"}]
    t["current_step"] = {"id": "b", "purpose": "second", "status": "in_progress"}
    line = progress.summary(t)
    assert "1 of 3 done" in line
    assert "curr: step 2" in line
    assert "second" in line


def test_plan_between_steps_points_at_next():
    t = _task_with_plan()
    t["steps"] = [{"id": "a", "purpose": "first", "status": "succeeded"},
                  {"id": "b", "purpose": "second", "status": "succeeded"}]
    line = progress.summary(t)
    assert "2 of 3 done" in line
    assert "curr: step 3" in line and "third" in line


def test_plan_done_summary_has_checkmark():
    t = _task_with_plan()
    t["steps"] = [{"id": i, "purpose": p, "status": "succeeded"} for i, p in
                  (("a", "first"), ("b", "second"), ("c", "third"))]
    line = progress.summary(t)
    assert "3 of 3 done" in line and "✓" in line


def test_remaining_excludes_done_and_current():
    t = _task_with_plan()
    t["steps"] = [{"id": "a", "purpose": "first", "status": "succeeded"}]
    t["current_step"] = {"id": "b", "purpose": "second", "status": "in_progress"}
    assert [p["id"] for p in progress.remaining(t)] == ["c"]


def test_ad_hoc_step_not_in_plan_grows_total():
    t = _task_with_plan()
    t["steps"] = [{"id": "z", "purpose": "extra", "status": "succeeded"}]
    # plan has 3, plus one ad-hoc committed id -> total 4
    assert progress.total_count(t) == 4
