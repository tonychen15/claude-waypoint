"""Worker bootstrap construction (pure) for the Phase 2 reconciler.

Builds the inputs a background ``claude`` worker is launched with. This slice
provides the seed prompt; the permission posture and the subprocess launcher
are later slices. Pure functions of task state — they launch nothing and are
unit-tested without invoking ``claude``.
"""

from __future__ import annotations

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
