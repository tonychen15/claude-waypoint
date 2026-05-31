"""On-disk state: project resolution, atomic read/write, archive, discovery.

State layout (§3)::

    <project>/.claude/waypoint/
    ├── <task_id>/{waypoint.json, STATUS.md}
    ├── archive/<task_id>/
    └── .locks/

Every write is tmp + ``os.replace`` so a crash mid-write never leaves a torn
file. ``waypoint.json`` is the source of truth; ``STATUS.md`` is regenerated
on every save.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

from . import model, statusmd

WAYPOINT_DIRNAME = "waypoint"
STATE_FILE = "waypoint.json"
STATUS_FILE = "STATUS.md"


def project_root(start: Optional[str] = None) -> str:
    """Resolve the project root for state.

    Prefers ``$CLAUDE_PROJECT_DIR``; falls back to the enclosing git
    work-tree; finally to ``start`` (or cwd).

    Args:
        start: Directory to resolve from. Defaults to cwd.

    Returns:
        Absolute path to the project root.
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return os.path.abspath(env)
    base = os.path.abspath(start or os.getcwd())
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base, capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return base


def _wp_dir(root: str) -> str:
    """Return ``<root>/.claude/waypoint`` for an absolute project root."""
    return os.path.join(root, ".claude", WAYPOINT_DIRNAME)


def task_dir(root: str, task_id: str) -> str:
    """Return the directory holding ``task_id``'s state."""
    return os.path.join(_wp_dir(root), task_id)


def state_path(root: str, task_id: str) -> str:
    """Return the path to ``task_id``'s ``waypoint.json``."""
    return os.path.join(task_dir(root, task_id), STATE_FILE)


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` atomically (tmp + os.replace)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save(root: str, task: dict, *, clock=None) -> None:
    """Persist a task and regenerate its STATUS.md.

    Refreshes ``updated_at`` and ``heartbeat``, validates the schema, and
    writes both files atomically.

    Args:
        root: Project root.
        task: The task dict.
        clock: Optional fixed datetime (for tests).

    Raises:
        ValueError: If the task fails schema validation.
    """
    errors = model.validate(task)
    if errors:
        raise ValueError("invalid task state: " + "; ".join(errors))
    ts = model.now_iso(clock)
    task["updated_at"] = ts
    task["heartbeat"] = ts
    tdir = task_dir(root, task["task_id"])
    os.makedirs(tdir, exist_ok=True)
    # Order matters: write the source of truth (waypoint.json) first, then the
    # derived STATUS.md. A crash between the two leaves STATUS.md momentarily
    # stale, which is harmless — it is regenerated on the next save (or can be
    # rebuilt from waypoint.json at any time). Never the reverse order.
    _atomic_write(os.path.join(tdir, STATE_FILE),
                  json.dumps(task, indent=2, ensure_ascii=False) + "\n")
    _atomic_write(os.path.join(tdir, STATUS_FILE), statusmd.render(task))


def load(root: str, task_id: str) -> dict:
    """Load a task dict by id.

    Args:
        root: Project root.
        task_id: The task id.

    Returns:
        The task dict.

    Raises:
        FileNotFoundError: If no such task exists.
    """
    with open(state_path(root, task_id), encoding="utf-8") as fh:
        return json.load(fh)


def list_tasks(root: str) -> list:
    """Return ``(task_id, task)`` pairs for all non-archived tasks.

    Args:
        root: Project root.

    Returns:
        A list of ``(task_id, task_dict)`` tuples (unreadable dirs skipped).
    """
    base = _wp_dir(root)
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if name in ("archive", ".locks") or name.startswith("."):
            continue
        sp = os.path.join(base, name, STATE_FILE)
        if os.path.isfile(sp):
            try:
                with open(sp, encoding="utf-8") as fh:
                    out.append((name, json.load(fh)))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def active_tasks(root: str) -> list:
    """Return ``(task_id, task)`` pairs whose status is ``in_progress``."""
    return [(tid, t) for tid, t in list_tasks(root)
            if t.get("status") == model.IN_PROGRESS]


def archive(root: str, task_id: str) -> str:
    """Move a task into ``archive/`` (never deletes — honors the red line).

    Args:
        root: Project root.
        task_id: The task to archive.

    Returns:
        The destination directory path.

    Raises:
        FileNotFoundError: If the task directory does not exist.
    """
    src = task_dir(root, task_id)
    if not os.path.isdir(src):
        raise FileNotFoundError(src)
    archive_root = os.path.join(_wp_dir(root), "archive")
    os.makedirs(archive_root, exist_ok=True)
    dst = os.path.join(archive_root, task_id)
    if os.path.exists(dst):
        dst = f"{dst}-{os.path.basename(src)}-{model.now_iso().replace(':', '')}"
    shutil.move(src, dst)
    return dst
