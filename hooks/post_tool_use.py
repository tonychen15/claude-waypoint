#!/usr/bin/env python3
"""PostToolUse hook — touch the heartbeat for each active task (Phase 2).

The tool-activity heartbeat is the guard's primary liveness signal. Fires
after every tool call in the worker session. Never raises.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from waypoint import runtime, store  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    try:
        root = store.project_root(data.get("cwd"))
        for tid in runtime.scoped_task_ids(root):
            runtime.touch_heartbeat(root, tid)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
