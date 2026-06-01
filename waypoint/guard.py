"""Autonomous guard for the Phase 2 reconciler.

The decision logic (``decide``) is pure and table-tested: it implements the
watchdog FSM, the three takeover triggers (death / waiting-timeout /
heartbeat-timeout), and the progress-gated loop guard. The side-effecting
wrappers (``observe``/``step``/``cmd_guard``) gather signals from the waypoint
folder and execute takeovers via ``launcher``. Conservative by design — a
false takeover that kills a working worker is worse than a missed one.
"""

from __future__ import annotations

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
