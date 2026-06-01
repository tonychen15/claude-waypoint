"""Worker bootstrap construction (pure) for the Phase 2 reconciler.

Builds the inputs a background ``claude`` worker is launched with. This slice
provides the seed prompt; the permission posture and the subprocess launcher
are later slices. Pure functions of task state — they launch nothing and are
unit-tested without invoking ``claude``.
"""

from __future__ import annotations


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
