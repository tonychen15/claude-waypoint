#!/usr/bin/env python3
"""PreCompact hook — snapshot already-committed state before compaction (§6).

PreCompact runs shell-only and cannot make Claude reflect-and-write, so this
does NOT try to summarize anything. It simply copies each active task's
current ``waypoint.json`` (already maintained at step boundaries) to a
timestamped snapshot, preserving the last-good state across the window the
transcript would otherwise thin. The hook never raises.
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from waypoint import model, store  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)


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

    stamp = model.now_iso().replace(":", "").replace("+", "p")
    for tid, _ in active:
        src = store.state_path(root, tid)
        snap_dir = os.path.join(store.task_dir(root, tid), "snapshots")
        try:
            os.makedirs(snap_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(snap_dir, f"waypoint-{stamp}.json"))
        except OSError:
            continue
    return 0


if __name__ == "__main__":
    sys.exit(main())
