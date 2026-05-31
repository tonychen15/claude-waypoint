#!/usr/bin/env python3
"""PreToolUse tripwire — enforce the step-boundary invariant (§10).

Matched on Write|Edit|MultiEdit. The rule that mechanically enforces
"at most one uncommitted step":

* No active task  -> allow (not armed; quick session).
* Active task with a ``current_step`` -> allow (working within the declared
  step).
* Active task **between steps** (``current_step`` is null) -> **deny**: the
  previous step was committed, so a new file mutation means new work that
  has not declared its step. The author must run ``waypoint set-step`` first.

Writes under ``.claude/waypoint/`` are always exempt (else the tool could
never update its own state). With several active tasks (concurrent mode) the
edit can be attributed to any task that has an *open* (declared) step, so the
rule is: allow if **any** active task has a ``current_step``; block only when
active tasks exist but **none** is open (all between steps) — that is the
case where new work genuinely has no declared step to belong to.

Contract: exit 0 allows; exit 2 + stderr blocks the tool call and shows the
reason to Claude. The hook never raises — on any internal error it allows,
so a bug here can't wedge the session.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from waypoint import store  # noqa: E402
except Exception:  # pragma: no cover - import guard
    sys.exit(0)

_MUTATORS = {"Write", "Edit", "MultiEdit"}


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") not in _MUTATORS:
        return 0

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    # Exempt waypoint's own state writes.
    if os.path.join(".claude", "waypoint") in os.path.abspath(file_path):
        return 0

    try:
        root = store.project_root(data.get("cwd"))
        active = store.active_tasks(root)
    except Exception:
        return 0

    if not active:
        return 0  # not armed (quick session)

    # The edit can be attributed to any task with an open (declared) step.
    if any(task.get("current_step") is not None for _, task in active):
        return 0

    # Active tasks exist but all are between steps: refuse new work until the
    # next step is declared on one of them.
    ids = ", ".join(tid for tid, _ in active)
    sys.stderr.write(
        f"waypoint: no step in progress (active: {ids}). A committed step "
        f"means new work needs a declared step before editing files:\n"
        f"  waypoint set-step --step <id> --purpose '<what>' "
        f"[--expected '<done looks like>']\n"
        f"(Or `waypoint done` if the task is finished.)\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
