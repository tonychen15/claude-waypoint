"""Roadmap math and progress rendering — one source of truth (§6).

Consumed by ``status``, ``steps``, ``list``, the commit/set-step beats, and
STATUS.md so the "step N of M" semantics are identical everywhere. A task
"has a plan" iff its permanent ``plan`` roadmap is non-empty; without one we
never speak of "step N" (meaningless), only a committed count.
"""

from __future__ import annotations


def has_plan(task: dict) -> bool:
    """True if a roadmap has been declared (``plan`` is non-empty)."""
    return bool(task.get("plan"))


def done_count(task: dict) -> int:
    """Number of committed (succeeded) steps."""
    return len(task.get("steps") or [])


def ordered_ids(task: dict) -> list:
    """Ordered union of step ids: plan ids, then committed/current ids not
    already in the plan (so ad-hoc steps still count toward the total)."""
    ids: list = []
    seen: set = set()

    def _add(sid):
        if sid is not None and sid not in seen:
            ids.append(sid)
            seen.add(sid)

    for p in task.get("plan") or []:
        _add(p.get("id"))
    for s in task.get("steps") or []:
        _add(s.get("id"))
    cur = task.get("current_step")
    if cur:
        _add(cur.get("id"))
    return ids


def total_count(task: dict) -> int:
    """Total distinct steps in the roadmap (incl. ad-hoc committed/current)."""
    return len(ordered_ids(task))


def position_of(task: dict, step_id: str) -> int:
    """1-based position of ``step_id`` in the ordered roadmap."""
    ids = ordered_ids(task)
    return ids.index(step_id) + 1 if step_id in ids else done_count(task) + 1


def remaining(task: dict) -> list:
    """Plan entries not yet committed and not the current step."""
    done_ids = {s.get("id") for s in task.get("steps") or []}
    cur = task.get("current_step")
    cur_id = cur.get("id") if cur else None
    return [p for p in (task.get("plan") or [])
            if p.get("id") not in done_ids and p.get("id") != cur_id]


def summary(task: dict) -> str:
    """One-line progress summary (the canonical wording used by ``status``)."""
    done = done_count(task)
    cur = task.get("current_step")
    if not has_plan(task):
        line = f"{done} step{'s' if done != 1 else ''} committed (no plan declared)"
        if cur:
            line += f"; in step '{cur.get('id')}' — {cur.get('purpose', '')}"
        return line
    total = total_count(task)
    rem = remaining(task)
    focus = cur or (rem[0] if rem else None)
    if focus is None:
        return f"{done} of {total} done ✓"
    pos = position_of(task, focus.get("id"))
    return (f"{done} of {total} done — curr: step {pos} "
            f"({focus.get('id')} — {focus.get('purpose', '')})")


def token(task: dict) -> str:
    """Compact status token for one-line listings (``list``)."""
    cur = task.get("current_step")
    if has_plan(task):
        total = total_count(task)
        if cur:
            return f"step {position_of(task, cur.get('id'))}/{total}"
        if remaining(task):
            return f"{done_count(task)}/{total} done"
        return f"{total}/{total} done ✓"
    if cur:
        return f"step {cur.get('id')}"
    return "between steps"
