"""Render the human-readable STATUS.md roadmap from a task dict (§3)."""

from __future__ import annotations

from . import model


def render(task: dict) -> str:
    """Return the STATUS.md contents for ``task``.

    Shows the goal, a checkbox roadmap (committed / current / pending), and a
    one-line "next on resume" hint.

    Args:
        task: The task dict.

    Returns:
        Markdown string.
    """
    tid = task.get("task_id", "?")
    status = task.get("status", "?")
    updated = task.get("updated_at", "?")
    lines = [
        f"# Task: {tid}   ({status}, last touched {updated})",
        "",
        f"**Goal:** {task.get('goal', '')}",
        "",
        "```",
    ]

    for step in task.get("steps", []):
        lines.append(f"✓ {step.get('id', '?')}  {step.get('purpose', '')}")

    cur = task.get("current_step")
    if cur:
        lines.append(
            f"▶ {cur.get('id', '?')}  {cur.get('purpose', '')}     ← current"
        )

    for step in task.get("pending", []):
        lines.append(f"☐ {step.get('id', '?')}  {step.get('purpose', '')}")

    lines.append("```")
    lines.append("")

    if cur:
        lines.append(
            f"**Next on resume:** continue current step "
            f"‘{cur.get('id', '?')}’ — {cur.get('purpose', '')}"
        )
    elif task.get("pending"):
        nxt = task["pending"][0]
        lines.append(
            f"**Next on resume:** declare and start step "
            f"‘{nxt.get('id', '?')}’ — {nxt.get('purpose', '')}"
        )
    else:
        lines.append("**Next on resume:** no current step — declare the next one.")

    lines.append("")
    return "\n".join(lines)
