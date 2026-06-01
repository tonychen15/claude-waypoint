"""Tests for pure worker-bootstrap construction."""

from waypoint import model, worker


def _task():
    t = model.new_task("2026-06-01-demo", "Add a /health endpoint")
    t["plan"] = [{"id": "api", "purpose": "add the route"},
                 {"id": "test", "purpose": "test it"}]
    return t


def test_seed_prompt_includes_goal_and_steps():
    s = worker.seed_prompt(_task())
    assert "Add a /health endpoint" in s
    assert "api" in s and "add the route" in s
    assert "test" in s and "test it" in s


def test_seed_prompt_states_the_policy_and_checkpoints():
    s = worker.seed_prompt(_task())
    assert "waypoint set-step" in s and "waypoint commit" in s
    assert "waypoint check" in s
    assert "to-be-deleted/" in s          # no-delete rule
    assert "remote" in s.lower()          # no-ungranted-remote rule
    assert "waypoint done" in s


def test_seed_prompt_handles_empty_plan():
    t = model.new_task("t1", "g")
    s = worker.seed_prompt(t)
    assert "no steps declared" in s
