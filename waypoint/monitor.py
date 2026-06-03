"""Read-only render of a task + runtime snapshot for ``waypoint watch``.

Pure: ``render(task, snapshot) -> str``. No I/O, so it is fully unit-tested;
the ``watch`` command supplies the snapshot and handles the refresh loop.
"""

from __future__ import annotations

from . import progress


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s ago"
    h, m = divmod(m, 60)
    return f"{h}h {m}m ago"


def _liveness_line(heartbeat_age: float | None) -> str:
    if heartbeat_age is None:
        return "worker: no worker activity yet"
    label = "active" if heartbeat_age < 60 else "idle"
    return f"worker: {label} (last tool {_fmt_age(heartbeat_age)})"


def render(task: dict, snapshot: dict) -> str:
    """Return the live-monitor text for a task and its runtime snapshot."""
    tid = task.get("task_id", "?")
    status = task.get("status", "?")
    lines = [
        f"# {tid}   ({status})",
        f"progress: {progress.summary(task)}",
        _liveness_line(snapshot.get("heartbeat_age")),
    ]
    events = snapshot.get("events") or []
    if events:
        lines.append("recent:")
        for e in events:
            extra = e.get("message", "")
            lines.append(f"  {e.get('ts', '?')}  {e.get('kind', '?')}"
                         + (f"  {extra}" if extra else ""))
    return "\n".join(lines)
