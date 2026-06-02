"""HEADLESS / EDGE-CASE path — not the primary way to run a project.

The primary path is the ``/waypoint`` skill: the in-session Claude agent
orchestrates subagents per step. This module powers the headless fallback,
used only when there is **no live session** to host that agent (cron, CI,
rate-limit auto-resume).

Worker process side-effects: spawn, stop, and the worker.json record. Pure
command construction lives in ``worker.py``; the liveness store in
``runtime.py``. ``run``, ``resume-worker``, and the guard share this one
spawn/stop implementation so there is a single way to start/kill a worker.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import uuid

from . import model, runtime, worker

WORKER_FILE = "worker.json"


def worker_json_path(root: str, task_id: str) -> str:
    return os.path.join(runtime.runtime_dir(root, task_id), WORKER_FILE)


def worker_info(root: str, task_id: str) -> dict | None:
    """Read the current worker record, or None if there is none."""
    try:
        with open(worker_json_path(root, task_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_worker(root: str, task_id: str, info: dict) -> None:
    d = runtime.runtime_dir(root, task_id)
    os.makedirs(d, exist_ok=True)
    tmp = worker_json_path(root, task_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    os.replace(tmp, worker_json_path(root, task_id))


def spawn(root: str, task_id: str, task: dict, *, claude_bin: str = "claude",
          resume_session: str | None = None) -> dict:
    """Launch the worker as a detached background process; record worker.json.

    A fresh launch generates and pins a ``--session-id`` so a later takeover
    can ``--resume`` it. Output (stdout+stderr) is appended to runtime/worker.log.
    """
    session_id = resume_session or str(uuid.uuid4())
    argv = worker.build_command(
        root, task_id, task, claude_bin=claude_bin,
        resume_session=resume_session,
        session_id=(None if resume_session else session_id))
    rdir = runtime.runtime_dir(root, task_id)
    os.makedirs(rdir, exist_ok=True)
    log_path = os.path.join(rdir, "worker.log")
    env = {**os.environ, "WAYPOINT_TASK_ID": task_id}
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT,
                                cwd=root, start_new_session=True, env=env)
    info = {"pid": proc.pid, "session_id": session_id,
            "started_at": model.now_iso(), "log": log_path,
            "resumed": bool(resume_session)}
    _write_worker(root, task_id, info)
    return info


def stop(root: str, task_id: str, *, sig: int = signal.SIGTERM) -> bool:
    """Signal the recorded worker process. True if a signal was delivered.

    After delivering the signal, a best-effort reap loop waits up to ~0.2 s so
    that—when the caller is the spawning process—the child does not linger as a
    zombie and ``os.kill(pid, 0)`` correctly reports the process as gone.  The
    reap is silently skipped when this process is not the parent (e.g. a
    different CLI invocation).
    """
    import time as _time

    info = worker_info(root, task_id)
    if not info or not info.get("pid"):
        return False
    pid = int(info["pid"])
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    # Best-effort reap: poll for up to 0.2 s so the process is fully gone.
    deadline = _time.monotonic() + 0.2
    while _time.monotonic() < deadline:
        try:
            done_pid, _ = os.waitpid(pid, os.WNOHANG)
            if done_pid == pid:
                break
        except ChildProcessError:
            break
        _time.sleep(0.01)
    return True
