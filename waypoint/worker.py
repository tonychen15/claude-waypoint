"""Worker bootstrap construction (pure) for the Phase 2 reconciler.

Builds the inputs a background ``claude`` worker is launched with. This slice
provides the seed prompt; the permission posture and the subprocess launcher
are later slices. Pure functions of task state — they launch nothing and are
unit-tested without invoking ``claude``.
"""

from __future__ import annotations

import json
import os
import sys

from . import model

# Deny-by-default worker posture. The allowlist enumerates what an autonomous
# worker may do; everything else is denied (``dontAsk``). The deny-guard hook
# is defense-in-depth. These are best-effort defaults — validate against a
# real ``claude`` run before relying on autonomy.
_ALLOW_BASE = [
    "Read", "Edit", "Write",
    "Bash(waypoint*)",
    "Bash(git add*)", "Bash(git commit*)", "Bash(git status*)",
    "Bash(git diff*)", "Bash(git log*)", "Bash(git restore*)",
    "Bash(ls*)", "Bash(cat*)", "Bash(grep*)", "Bash(find*)",
    "Bash(mkdir*)", "Bash(mv*)", "Bash(cp*)", "Bash(touch*)",
    "Bash(python*)", "Bash(python3*)", "Bash(pytest*)",
    "Bash(npm*)", "Bash(node*)", "Bash(pip*)",
]
_DENY_BASE = ["Bash(rm*)", "Bash(git rm*)", "Bash(git push*)", "Bash(sudo*)"]
_REMOTE_WRITE_TOOLS = ["Bash(scp*)", "Bash(rsync*)", "Bash(curl*)", "Bash(wget*)"]


def permission_args(task: dict) -> list:
    """Return the ``--permission-mode``/``--allowedTools``/``--disallowedTools``
    argv for the worker, adjusted for the task's grants (deny-by-default)."""
    allow = list(_ALLOW_BASE)
    deny = list(_DENY_BASE)
    if model.has_grant(task, model.GRANT_PUSH):
        allow.append("Bash(git push*)")
        deny = [d for d in deny if d != "Bash(git push*)"]
    if model.has_grant(task, model.GRANT_REMOTE_WRITE):
        allow.extend(_REMOTE_WRITE_TOOLS)
    return [
        "--permission-mode", "dontAsk",
        "--allowedTools", " ".join(allow),
        "--disallowedTools", " ".join(deny),
    ]


def seed_prompt(task: dict) -> str:
    """Return the initial prompt for the worker (adopt-plan, then execute)."""
    tid = task.get("task_id", "?")
    goal = task.get("goal", "")
    steps = task.get("plan") or []
    lines = [
        f"You are the waypoint worker for task {tid!r}.",
        f"Goal: {goal}",
        "",
        "Adopt the DECLARED plan below — do not re-plan it:",
    ]
    if steps:
        for i, p in enumerate(steps, 1):
            lines.append(f"  {i}. {p.get('id')} — {p.get('purpose', '')}")
    else:
        lines.append("  (no steps declared yet)")
    lines += [
        "",
        "Before editing anything, reconcile reality: run `waypoint status` "
        "and `waypoint check`.",
        "Work the steps in order. For each: `waypoint set-step --step <id> "
        "--purpose <p>`, do the work, then `waypoint commit --summary <s> "
        "[--artifact <path> ...]`.",
        "Never delete files — move them into `to-be-deleted/` instead.",
        "Do not push or perform any remote operation unless explicitly "
        "granted; if you need one, stop and surface it rather than retrying.",
        "When every step is committed, run `waypoint done`.",
    ]
    return "\n".join(lines)


def _hooks_dir() -> str:
    """Absolute path to waypoint's own hook scripts (``<repo>/hooks``)."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")


def _hook_entry(script: str, matcher: str | None = None) -> dict:
    cmd = f'"{sys.executable}" "{os.path.join(_hooks_dir(), script)}"'
    entry: dict = {"hooks": [{"type": "command", "command": cmd}]}
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def worker_settings(root: str, task_id: str) -> dict:  # noqa: ARG001
    """Return an inline ``--settings`` dict wiring the four Phase-2 worker
    hooks (heartbeat, notification, stop, deny-guard) by absolute path.

    ``root`` and ``task_id`` are accepted for interface stability; the hook
    scripts resolve the active task from the filesystem at runtime.
    """
    return {
        "hooks": {
            "PostToolUse": [_hook_entry("post_tool_use.py")],
            "Notification": [_hook_entry("notification.py")],
            "Stop": [_hook_entry("stop.py")],
            "PreToolUse": [_hook_entry("pre_tool_use_guard.py", matcher="Bash")],
        }
    }


def build_command(root: str, task_id: str, task: dict, *,
                  resume_session: str | None = None,
                  claude_bin: str = "claude") -> list:
    """Assemble the full headless-worker ``claude`` argv (launches nothing).

    Headless (``-p``) autonomous run: permission posture + the session hooks +
    the project dir + the seed prompt. With ``resume_session`` it resumes that
    session id; otherwise a fresh run.
    """
    argv = [claude_bin, "-p"]
    if resume_session:
        argv += ["--resume", resume_session]
    argv += permission_args(task)
    argv += ["--settings", json.dumps(worker_settings(root, task_id))]
    argv += ["--add-dir", root]
    argv += ["--output-format", "stream-json", "--verbose"]
    argv += [seed_prompt(task)]
    return argv
