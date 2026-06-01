#!/usr/bin/env python3
"""Notification hook — record a 'notification' event (Phase 2).

Claude Code fires this when it wants attention (e.g. waiting for input or
idle). The guard later uses it as an explicit stall signal. Never raises.
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
    msg = (data.get("message") or "")
    try:
        root = store.project_root(data.get("cwd"))
        for tid in runtime.scoped_task_ids(root):
            runtime.append_event(root, tid, "notification", message=msg)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
