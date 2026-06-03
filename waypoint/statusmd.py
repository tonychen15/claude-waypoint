"""Render the human-readable STATUS.md roadmap from a task dict (§3)."""

from __future__ import annotations

from . import model, progress


def render(task: dict) -> str:
    """Return the STATUS.md contents for ``task``.

    Shows the goal, a checkbox roadmap (committed / current / remaining), and a
    one-line "next on resume" hint.

    Args:
        task: The task dict.

    Returns:
        Markdown string.
    """
    tid = task.get("task_id", "?")
    status = task.get("status", "?")
    updated = task.get("updated_at", "?")
    rem = progress.remaining(task)
    lines = [
        f"# Task: {tid}   ({status}, last touched {updated})",
        "",
        f"**Goal:** {task.get('goal', '')}",
        "",
        f"**Progress:** {progress.summary(task)}",
        "",
        "```",
    ]

    def _gate(step: dict) -> str:
        # Flag human-gate steps so an auditor sees they hinge on a human answer.
        return " [HUMAN]" if step.get("awaits_human") else ""

    for step in task.get("steps", []):
        lines.append(
            f"✓ {step.get('id', '?')}  {step.get('purpose', '')}{_gate(step)}"
        )

    cur = task.get("current_step")
    if cur:
        lines.append(
            f"▶ {cur.get('id', '?')}  {cur.get('purpose', '')}{_gate(cur)}"
            f"     ← current"
        )

    for step in rem:
        lines.append(f"☐ {step.get('id', '?')}  {step.get('purpose', '')}")

    lines.append("```")
    lines.append("")

    if cur:
        lines.append(
            f"**Next on resume:** continue current step "
            f"'{cur.get('id', '?')}' — {cur.get('purpose', '')}"
        )
    elif rem:
        nxt = rem[0]
        lines.append(
            f"**Next on resume:** declare and start step "
            f"'{nxt.get('id', '?')}' — {nxt.get('purpose', '')}"
        )
    else:
        lines.append("**Next on resume:** no current step — declare the next one.")

    lines.append("")
    return "\n".join(lines)
