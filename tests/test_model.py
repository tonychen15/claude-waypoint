"""Tests for the data model: construction, validation, helpers."""

from waypoint import model


def test_now_iso_has_offset():
    ts = model.now_iso()
    assert "T" in ts
    # tz-aware ISO ends with an offset (+/-HH:MM) or 'Z'.
    assert ts[-6] in "+-" or ts.endswith("Z")


def test_new_task_shape():
    t = model.new_task("2026-05-30-x", "do a thing", scope=["src/"], auto=True)
    assert t["task_id"] == "2026-05-30-x"
    assert t["status"] == model.IN_PROGRESS
    assert t["steps"] == []
    assert t["current_step"] is None
    assert t["auto"] is True
    assert model.validate(t) == []


def test_validate_catches_problems():
    assert any("missing" in e for e in model.validate({}))
    bad = model.new_task("i", "g")
    bad["status"] = "weird"
    assert any("invalid status" in e for e in model.validate(bad))


def test_validate_committed_step_must_be_succeeded():
    t = model.new_task("i", "g")
    t["steps"].append({"id": "a", "status": "in_progress"})
    assert any("not 'succeeded'" in e for e in model.validate(t))


def test_validate_current_step_must_be_in_progress():
    t = model.new_task("i", "g")
    t["current_step"] = {"id": "a", "status": "succeeded"}
    assert any("current_step.status" in e for e in model.validate(t))


def test_last_succeeded():
    t = model.new_task("i", "g")
    assert model.last_succeeded(t) is None
    t["steps"] = [{"id": "a", "status": "succeeded"},
                  {"id": "b", "status": "succeeded"}]
    assert model.last_succeeded(t)["id"] == "b"
