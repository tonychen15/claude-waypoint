#!/usr/bin/env python3
"""SessionStart hook — surface unfinished tasks on a new session (§6).

Emits a compact ``additionalContext`` block (kept well under the ~10K cap)
listing each active task with an age *label* — never mutating state by age
(§7). Claude is expected to offer resume and wait for the user's go-ahead.

Output uses the documented SessionStart JSON shape. The hook never raises.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from waypoint import model, store  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)

_MAX = 9000  # stay under the ~10K additionalContext cap


def _age_label(updated_at: str) -> str:
    try:
        then = datetime.fromisoformat(updated_at)
        now = datetime.now(then.tzinfo) if then.tzinfo else datetime.now()
        hours = (now - then).total_seconds() / 3600.0
    except Exception:
        return "unknown age"
    if hours < 48:
        return "active"
    return f"inactive ({int(hours // 24)} days)"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    try:
        root = store.project_root(data.get("cwd"))
        active = store.active_tasks(root)
    except Exception:
        return 0
    if not active:
        return 0

    lines = ["[waypoint] Unfinished task(s) found in this project. "
             "Offer to resume; wait for the user before acting.\n"]
    for tid, task in active:
        cur = task.get("current_step")
        step = model.last_succeeded(task)
        where = (f"in step '{cur.get('id')}' ({cur.get('purpose')})"
                 if cur else "between steps")
        lines.append(
            f"- {tid} [{_age_label(task.get('updated_at', ''))}]: "
            f"{task.get('goal')}\n"
            f"    state: {where}; "
            f"last committed: {step.get('id') if step else 'none'}\n"
            f"    to resume: `waypoint resume --id {tid}` "
            f"(then re-read STATUS.md and the step artifacts)."
        )
    context = "\n".join(lines)[:_MAX]

    out = {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
