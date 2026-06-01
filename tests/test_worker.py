"""Tests for pure worker-bootstrap construction."""

import json
import os

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


def test_permission_args_dont_ask_with_allow_and_deny():
    args = worker.permission_args(_task())
    assert "--permission-mode" in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    allow = args[args.index("--allowedTools") + 1]
    deny = args[args.index("--disallowedTools") + 1]
    assert "Edit" in allow and "Bash(waypoint*)" in allow
    assert "Bash(rm*)" in deny and "Bash(git push*)" in deny


def test_permission_args_push_grant_moves_push_to_allow():
    t = _task()
    model.set_grant(t, model.GRANT_PUSH)
    args = worker.permission_args(t)
    allow = args[args.index("--allowedTools") + 1]
    deny = args[args.index("--disallowedTools") + 1]
    assert "Bash(git push*)" in allow
    assert "Bash(git push*)" not in deny


def test_permission_args_remote_write_grant_allows_transfer_tools():
    t = _task()
    model.set_grant(t, model.GRANT_REMOTE_WRITE)
    args = worker.permission_args(t)
    allow = args[args.index("--allowedTools") + 1]
    assert "Bash(scp*)" in allow and "Bash(rsync*)" in allow


def _first_command(entry):
    return entry[0]["hooks"][0]["command"].split()[-1]


def test_worker_settings_wires_all_four_phase2_hooks(tmp_path):
    s = worker.worker_settings(str(tmp_path), "t1")
    hooks = s["hooks"]
    assert set(hooks) == {"PostToolUse", "Notification", "Stop", "PreToolUse"}
    flat = json.dumps(s)
    for script in ("post_tool_use.py", "notification.py", "stop.py",
                   "pre_tool_use_guard.py"):
        assert script in flat
    assert os.path.isabs(_first_command(hooks["Stop"]).strip('"'))


def test_build_command_assembles_headless_worker(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t)
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--permission-mode" in argv and "dontAsk" in argv
    assert "--settings" in argv
    assert "waypoint set-step" in argv[-1]   # seed prompt is the last positional
    assert "--resume" not in argv


def test_build_command_with_resume_and_custom_bin(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t,
                                resume_session="sess-123", claude_bin="/x/fake")
    assert argv[0] == "/x/fake"
    assert argv[argv.index("--resume") + 1] == "sess-123"


def test_build_command_settings_is_valid_json(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t)
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert "hooks" in settings
