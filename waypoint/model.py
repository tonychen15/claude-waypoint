"""waypoint data model: timestamps, schema constants, and validation.

State is plain JSON (a dict) to stay small and legible (§3). These helpers
construct and validate that dict rather than wrapping it in classes — the
JSON file is the source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Task lifecycle states.
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
ABANDONED = "abandoned"
TASK_STATES = {IN_PROGRESS, COMPLETED, ABANDONED}

# Step states.
STEP_IN_PROGRESS = "in_progress"
STEP_SUCCEEDED = "succeeded"

# Effects-ledger states (§5).
EFFECT_PENDING = "pending"
EFFECT_COMPLETED = "completed"

_REQUIRED_TASK_KEYS = ("task_id", "goal", "status", "created_at", "steps")


def now_iso(clock: Optional[datetime] = None) -> str:
    """Return a timezone-aware ISO-8601 timestamp.

    Args:
        clock: Optional fixed datetime (for tests). Defaults to local now.

    Returns:
        ISO-8601 string including a UTC offset.
    """
    dt = clock or datetime.now(timezone.utc).astimezone()
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def new_task(task_id: str, goal: str, *, scope: Optional[list] = None,
             owner_session: str = "", auto: bool = False,
             clock: Optional[datetime] = None) -> dict:
    """Build a fresh task dict with no steps and no current step.

    Args:
        task_id: Stable, unique task id (e.g. ``2026-05-30-slug``).
        goal: One-line overall objective.
        scope: Declared folders/files for overlap detection (§8).
        owner_session: Adopting session id.
        auto: Whether autonomous cron resume is enabled (§6A).
        clock: Optional fixed datetime (for tests).

    Returns:
        A task dict conforming to the schema.
    """
    ts = now_iso(clock)
    return {
        "task_id": task_id,
        "goal": goal,
        "status": IN_PROGRESS,
        "auto": bool(auto),
        "created_at": ts,
        "updated_at": ts,
        "owner_session": owner_session,
        "heartbeat": ts,
        "session_history": [owner_session] if owner_session else [],
        "scope": scope or [],
        "steps": [],
        "current_step": None,
        "pending": [],
    }


def validate(task: dict) -> list:
    """Return a list of human-readable schema problems (empty if valid).

    Checks required keys, the task-state enum, the single-uncommitted-step
    invariant (at most one ``current_step``), and that committed steps are
    marked ``succeeded``.

    Args:
        task: The task dict to validate.

    Returns:
        A list of error strings; empty means valid.
    """
    errors: list = []
    for key in _REQUIRED_TASK_KEYS:
        if key not in task:
            errors.append(f"missing required key: {key}")
    status = task.get("status")
    if status is not None and status not in TASK_STATES:
        errors.append(f"invalid status: {status!r}")
    if not isinstance(task.get("steps", []), list):
        errors.append("steps must be a list")
    else:
        for i, step in enumerate(task.get("steps", [])):
            if step.get("status") != STEP_SUCCEEDED:
                errors.append(
                    f"committed step[{i}] {step.get('id')!r} is not 'succeeded'"
                )
    cur = task.get("current_step")
    if cur is not None:
        if not isinstance(cur, dict):
            errors.append("current_step must be an object or null")
        elif cur.get("status") != STEP_IN_PROGRESS:
            errors.append("current_step.status must be 'in_progress'")
    return errors


def last_succeeded(task: dict) -> Optional[dict]:
    """Return the most recently committed step, or None.

    Args:
        task: The task dict.

    Returns:
        The last element of ``steps`` (the last succeeded waypoint), or None.
    """
    steps = task.get("steps") or []
    return steps[-1] if steps else None
