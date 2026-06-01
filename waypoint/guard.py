"""Autonomous guard for the Phase 2 reconciler.

The decision logic (``decide``) is pure and table-tested: it implements the
watchdog FSM, the three takeover triggers (death / waiting-timeout /
heartbeat-timeout), and the progress-gated loop guard. The side-effecting
wrappers (``observe``/``step``/``cmd_guard``) gather signals from the waypoint
folder and execute takeovers via ``launcher``. Conservative by design — a
false takeover that kills a working worker is worse than a missed one.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

from . import launcher, model, runtime, store

# FSM states.
WATCHING = "watching"
HALTED = "halted"
DONE = "done"

# Actions returned by decide().
WATCH = "watch"
TAKEOVER = "takeover"
HALT = "halt"
COMPLETE = "done"

DEFAULTS = {"idle_timeout": 600.0, "wait_timeout": 300.0, "max_no_progress": 2}


def _trigger(obs: dict, config: dict):
    """Return the takeover trigger name, or None. Death > waiting > idle.

    A missing heartbeat (None) is treated as 'no signal yet', not a stall —
    only a stale heartbeat (alive but quiet past idle_timeout) counts.
    """
    if not obs.get("alive"):
        return "death"
    wa = obs.get("waiting_age")
    if wa is not None and wa > config["wait_timeout"]:
        return "waiting-timeout"
    ha = obs.get("heartbeat_age")
    if ha is not None and ha > config["idle_timeout"]:
        return "heartbeat-timeout"
    return None


def decide(obs: dict, gstate: dict, config: dict) -> tuple:
    """Pure decision: return ``(action, new_gstate)``.

    ``obs``: {task_status, alive, heartbeat_age, waiting_age, committed}.
    ``gstate``: {fsm, no_progress, baseline_committed}.
    ``config``: {idle_timeout, wait_timeout, max_no_progress}.
    """
    fsm = gstate.get("fsm", WATCHING)
    if fsm == HALTED:
        return HALT, gstate
    if fsm == DONE:
        return COMPLETE, gstate
    if obs.get("task_status") == "completed":
        return COMPLETE, {**gstate, "fsm": DONE}

    if not _trigger(obs, config):
        return WATCH, gstate

    committed = obs.get("committed") or 0
    baseline = gstate.get("baseline_committed", committed)
    no_progress = 0 if committed > baseline else gstate.get("no_progress", 0) + 1
    if no_progress >= config["max_no_progress"]:
        return HALT, {**gstate, "fsm": HALTED, "no_progress": no_progress}
    return TAKEOVER, {**gstate, "fsm": WATCHING, "no_progress": no_progress,
                      "baseline_committed": committed}


GUARD_FILE = "guard.json"


def _state_path(root: str, task_id: str) -> str:
    return os.path.join(runtime.runtime_dir(root, task_id), GUARD_FILE)


def load_state(root: str, task_id: str) -> dict:
    """Load persisted guard state, or fresh defaults."""
    try:
        with open(_state_path(root, task_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"fsm": WATCHING, "no_progress": 0, "baseline_committed": 0}


def save_state(root: str, task_id: str, gstate: dict) -> None:
    d = runtime.runtime_dir(root, task_id)
    os.makedirs(d, exist_ok=True)
    tmp = _state_path(root, task_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(gstate, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, _state_path(root, task_id))


def _pid_alive(pid: int | str | None) -> bool:
    try:
        p = int(pid)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return False
    if p <= 0:
        return False
    try:
        os.kill(p, 0)
        return True
    except PermissionError:
        # EPERM: process exists but we lack permission to signal it — still alive.
        return True
    except OSError:
        return False


def _event_age(ts: str) -> float | None:
    try:
        then = datetime.fromisoformat(ts)
        now = datetime.now(then.tzinfo) if then.tzinfo else datetime.now()
        return max(0.0, (now - then).total_seconds())
    except (ValueError, OverflowError, OSError):
        return None


def _waiting_age(events: list, heartbeat_age: float | None) -> float | None:
    """Age of the latest 'notification' event, if it is the most recent signal
    (no tool activity since). None otherwise."""
    notes = [e for e in events if e.get("kind") == "notification"]
    if not notes:
        return None
    age = _event_age(notes[-1].get("ts", ""))
    if age is None:
        return None
    if heartbeat_age is not None and heartbeat_age < age:
        return None
    return age


def observe(root: str, task_id: str) -> dict:
    """Gather the guard's observations from disk for one tick."""
    try:
        task = store.load(root, task_id)
    except (OSError, json.JSONDecodeError, KeyError):
        task = {}
    info = launcher.worker_info(root, task_id)
    alive = _pid_alive(info.get("pid")) if info else False
    snap = runtime.snapshot(root, task_id, events_limit=20)
    return {
        "task_status": task.get("status"),
        "alive": alive,
        "heartbeat_age": snap["heartbeat_age"],
        "waiting_age": _waiting_age(snap["events"], snap["heartbeat_age"]),
        "committed": len(task.get("steps")) if isinstance(task.get("steps"), list) else 0,
    }


def notify(title: str, message: str) -> None:
    """Best-effort desktop notification + stdout. Never raises."""
    print(f"[waypoint guard] {title}: {message}")
    try:
        subprocess.run(["notify-send", "--", title, message],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def step(root: str, task_id: str, *, config: dict,
         claude_bin: str = "claude") -> str:
    """One observe→decide→act cycle. Returns the action taken."""
    gstate = load_state(root, task_id)
    obs = observe(root, task_id)
    action, new_gstate = decide(obs, gstate, config)
    save_state(root, task_id, new_gstate)

    if action == TAKEOVER:
        info = launcher.worker_info(root, task_id)
        session = info.get("session_id") if info else None
        runtime.append_event(root, task_id, "takeover",
                             reason=_trigger(obs, config),
                             committed=obs.get("committed"))
        launcher.stop(root, task_id)
        launcher.spawn(root, task_id, store.load(root, task_id),
                       claude_bin=claude_bin, resume_session=session)
    elif action == HALT:
        if gstate.get("fsm") != HALTED:
            notify("task halted",
                   f"{task_id}: no forward progress after repeated takeovers — "
                   f"needs a human. Last worker left in place.")
    elif action == COMPLETE:
        if gstate.get("fsm") != DONE:
            notify("task complete", f"{task_id} finished.")
    return action
