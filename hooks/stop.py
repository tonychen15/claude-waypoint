#!/usr/bin/env python3
"""Stop hook — record a 'turn_done' event (Phase 2).

Marks a worker turn boundary; the guard uses it for liveness/idle reasoning.
Never raises.
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
        for tid, _ in store.active_tasks(root):
            runtime.append_event(root, tid, "turn_done")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
