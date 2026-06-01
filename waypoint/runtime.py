"""Phase 2 runtime liveness store (the ephemeral half of the channel).

Lives at ``<task_dir>/runtime/`` (already gitignored under
``.claude/waypoint/``). Holds short-lived signals the worker emits and the
guard reads: a ``heartbeat`` file (mtime = last tool activity) and an
``events.jsonl`` append log. Safe to delete between runs; always rebuildable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from . import model, store

RUNTIME_DIRNAME = "runtime"
HEARTBEAT_FILE = "heartbeat"
EVENTS_FILE = "events.jsonl"


def runtime_dir(root: str, task_id: str) -> str:
    """Return ``<task_dir>/runtime`` for the task."""
    return os.path.join(store.task_dir(root, task_id), RUNTIME_DIRNAME)


def _ensure(root: str, task_id: str) -> str:
    d = runtime_dir(root, task_id)
    os.makedirs(d, exist_ok=True)
    return d


def touch_heartbeat(root: str, task_id: str) -> None:
    """Update the heartbeat mtime to 'now' (creating it if needed)."""
    d = _ensure(root, task_id)
    path = os.path.join(d, HEARTBEAT_FILE)
    with open(path, "a", encoding="utf-8"):
        pass
    os.utime(path, None)


def heartbeat_age(root: str, task_id: str) -> Optional[float]:
    """Seconds since the last heartbeat, or None if there is none."""
    path = os.path.join(runtime_dir(root, task_id), HEARTBEAT_FILE)
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def append_event(root: str, task_id: str, kind: str, **fields) -> None:
    """Append a ``{ts, kind, **fields}`` JSON line to events.jsonl."""
    d = _ensure(root, task_id)
    rec = {"ts": model.now_iso(), "kind": kind}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with open(os.path.join(d, EVENTS_FILE), "a", encoding="utf-8") as fh:
        fh.write(line)


def read_events(root: str, task_id: str, limit: int = 20) -> list:
    """Return up to the last ``limit`` events (malformed lines skipped)."""
    path = os.path.join(runtime_dir(root, task_id), EVENTS_FILE)
    out: list = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]


def snapshot(root: str, task_id: str, *, events_limit: int = 5) -> dict:
    """A point-in-time liveness snapshot for rendering."""
    return {
        "heartbeat_age": heartbeat_age(root, task_id),
        "events": read_events(root, task_id, limit=events_limit),
    }
