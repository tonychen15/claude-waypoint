"""Tests for the hook decision logic (PreToolUse tripwire, SessionStart)."""

import importlib.util
import io
import json
import os

import pytest

from waypoint import model, store

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "hooks")


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"hook_{name}", os.path.join(HOOKS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, payload, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return mod.main()


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return str(tmp_path)


def _edit_payload(root, path):
    return {"tool_name": "Edit", "cwd": root, "tool_input": {"file_path": path}}


def test_pretooluse_allows_when_no_task(root, monkeypatch):
    mod = _load("pre_tool_use")
    assert _run(mod, _edit_payload(root, "/x/file.py"), monkeypatch) == 0


def test_pretooluse_denies_between_steps(root, monkeypatch):
    store.save(root, model.new_task("t1", "g"))  # active, current_step None
    mod = _load("pre_tool_use")
    assert _run(mod, _edit_payload(root, os.path.join(root, "file.py")),
                monkeypatch) == 2


def test_pretooluse_allows_within_declared_step(root, monkeypatch):
    t = model.new_task("t1", "g")
    t["current_step"] = {"id": "a", "purpose": "p", "status": "in_progress"}
    store.save(root, t)
    mod = _load("pre_tool_use")
    assert _run(mod, _edit_payload(root, os.path.join(root, "file.py")),
                monkeypatch) == 0


def test_pretooluse_exempts_waypoint_state(root, monkeypatch):
    store.save(root, model.new_task("t1", "g"))  # between steps
    mod = _load("pre_tool_use")
    state = os.path.join(root, ".claude", "waypoint", "t1", "waypoint.json")
    assert _run(mod, _edit_payload(root, state), monkeypatch) == 0


def test_pretooluse_ignores_non_mutating_tools(root, monkeypatch):
    store.save(root, model.new_task("t1", "g"))
    mod = _load("pre_tool_use")
    payload = {"tool_name": "Read", "cwd": root,
               "tool_input": {"file_path": os.path.join(root, "f.py")}}
    assert _run(mod, payload, monkeypatch) == 0


def test_sessionstart_surfaces_active_task(root, monkeypatch, capsys):
    store.save(root, model.new_task("t1", "resume me"))
    mod = _load("session_start")
    assert _run(mod, {"cwd": root, "source": "startup"}, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "waypoint" in out and "resume me" in out
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_sessionstart_silent_when_no_task(root, monkeypatch, capsys):
    mod = _load("session_start")
    assert _run(mod, {"cwd": root, "source": "startup"}, monkeypatch) == 0
    assert capsys.readouterr().out.strip() == ""


def test_post_tool_use_touches_heartbeat(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("post_tool_use")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    assert _run(mod, {"tool_name": "Edit", "cwd": root}, monkeypatch) == 0
    assert runtime.heartbeat_age(root, "t1") is not None


def test_notification_records_event(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("notification")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    _run(mod, {"message": "waiting for your input", "cwd": root}, monkeypatch)
    evs = runtime.read_events(root, "t1")
    assert evs and evs[-1]["kind"] == "notification"
    assert "waiting" in evs[-1]["message"]


def test_stop_records_turn_done(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("stop")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    _run(mod, {"cwd": root}, monkeypatch)
    evs = runtime.read_events(root, "t1")
    assert evs and evs[-1]["kind"] == "turn_done"


def test_worker_hooks_never_raise_on_garbage(root, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    for name in ("post_tool_use", "notification", "stop"):
        mod = _load(name)
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
        assert mod.main() == 0
