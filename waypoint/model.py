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

# Outbound-operation grants (Phase 2 permission policy). Default: nothing
# granted; the `run` authorization gate enables what a task may do. Remote
# deletes are intentionally not grantable — they stay unconditionally blocked
# by the worker's deny-by-default posture.
GRANT_PUSH = "push"
GRANT_REMOTE_WRITE = "remote_write"
GRANTS = {GRANT_PUSH, GRANT_REMOTE_WRITE}

# Orchestration policy (Phase 3, intra-Claude skill).
REVIEW_AUTO = "auto"
REVIEW_MANUAL = "manual"
REVIEW_MODES = {REVIEW_AUTO, REVIEW_MANUAL}

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
             review: str = "auto", reviewer: str = "", max_retries: int = 2,
             clock: Optional[datetime] = None) -> dict:
    """Build a fresh task dict with no steps and no current step.

    Args:
        task_id: Stable, unique task id (e.g. ``2026-05-30-slug``).
        goal: One-line overall objective.
        scope: Declared folders/files for overlap detection (§8).
        owner_session: Adopting session id.
        auto: Whether autonomous cron resume is enabled (§6A).
        review: Orchestration review mode (``"auto"`` or ``"manual"``).
        reviewer: Name of the reviewing entity when mode is ``"manual"``.
        max_retries: Maximum retry attempts for orchestration policy.
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
        "plan": [],
        "grants": {},
        "review": review,
        "reviewer": reviewer,
        "max_retries": int(max_retries),
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
    if not isinstance(task.get("plan", []), list):
        errors.append("plan must be a list")
    if not isinstance(task.get("grants", {}), dict):
        errors.append("grants must be a dict")
    if task.get("review", REVIEW_AUTO) not in REVIEW_MODES:
        errors.append(f"invalid review mode: {task.get('review')!r}")
    if not isinstance(task.get("reviewer", ""), str):
        errors.append("reviewer must be a str")
    if not isinstance(task.get("max_retries", 0), int):
        errors.append("max_retries must be an int")
    return errors


def migrate(task: dict) -> dict:
    """Upgrade a task dict in place to the current shape.

    Ensures a permanent ``plan`` roadmap exists. Legacy tasks stored a
    consumed-away ``pending`` queue and no ``plan``; when there is forward
    intent (non-empty ``pending``), reconstruct the full roadmap from
    committed steps + the current step + pending. Otherwise leave the plan
    empty (no plan was ever declared). The obsolete ``pending`` key is
    dropped. Idempotent and never raises on a well-formed task.

    Note: an empty ``pending`` yields ``plan == []`` *even if a
    ``current_step`` is active*. That is deliberate — empty pending means no
    roadmap was ever declared, so the task should read as "in step X, no
    plan", not "step N of M" (which would falsely imply a known finish line).
    The active ``current_step`` is NOT lost: it stays in
    ``task["current_step"]`` and remains fully resumable; it is simply not
    promoted into a declared plan. This preserves the no-plan vs plan-done
    distinction for legacy data.

    Args:
        task: The task dict (mutated in place).

    Returns:
        The same task dict, for chaining.
    """
    if "plan" not in task:
        pending = task.get("pending") or []
        if pending:
            plan = [{"id": s.get("id") or "", "purpose": s.get("purpose") or ""}
                    for s in (task.get("steps") or [])]
            cur = task.get("current_step")
            if cur:
                plan.append({"id": cur.get("id") or "",
                             "purpose": cur.get("purpose") or ""})
            plan.extend({"id": p.get("id") or "", "purpose": p.get("purpose") or ""}
                        for p in pending)
            task["plan"] = plan
        else:
            task["plan"] = []
    task.pop("pending", None)
    task.setdefault("grants", {})
    task.setdefault("review", REVIEW_AUTO)
    task.setdefault("reviewer", "")
    task.setdefault("max_retries", 2)
    return task


def last_succeeded(task: dict) -> Optional[dict]:
    """Return the most recently committed step, or None.

    Args:
        task: The task dict.

    Returns:
        The last element of ``steps`` (the last succeeded waypoint), or None.
    """
    steps = task.get("steps") or []
    return steps[-1] if steps else None


def set_grant(task: dict, name: str, value: bool = True) -> None:
    """Grant (or revoke) an outbound operation for a task."""
    if not isinstance(task.get("grants"), dict):
        task["grants"] = {}
    task["grants"][name] = bool(value)


def has_grant(task: dict, name: str) -> bool:
    """True if ``name`` is granted for this task."""
    grants = task.get("grants")
    if not isinstance(grants, dict):
        return False
    return bool(grants.get(name, False))
