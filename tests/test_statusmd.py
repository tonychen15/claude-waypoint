"""Tests for STATUS.md rendering."""

from waypoint import model, statusmd


def test_render_shows_roadmap():
    t = model.new_task("t1", "build the thing")
    t["steps"].append({"id": "a", "purpose": "first", "status": "succeeded"})
    t["current_step"] = {"id": "b", "purpose": "second",
                         "status": "in_progress"}
    t["pending"] = [{"id": "c", "purpose": "third"}]
    out = statusmd.render(t)
    assert "build the thing" in out
    assert "✓ a  first" in out
    assert "▶ b  second" in out
    assert "☐ c  third" in out
    assert "Next on resume" in out


def test_render_between_steps():
    t = model.new_task("t1", "g")
    t["steps"].append({"id": "a", "purpose": "first", "status": "succeeded"})
    out = statusmd.render(t)
    assert "no current step" in out.lower() or "declare the next" in out.lower()
