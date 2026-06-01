#!/usr/bin/env python3
"""Worker PreToolUse deny-guard (Phase 2 permission policy).

Defense-in-depth atop the worker's allowlist posture. On a Bash tool call it
blocks:
  * local deletes (rm / git rm / rmdir / unlink / shred) -> the worker must
    move targets into ``to-be-deleted/`` instead.
  * ungranted remote ops (git push; scp/rsync; curl/wget upload) -> deny and
    record a ``needs-auth`` event for pane A to surface.
Everything else is allowed. Contract: exit 0 allows; exit 2 + stderr blocks.
Errs open (allow) on any internal error, consistent with the other hooks.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from waypoint import model, runtime, store  # noqa: E402
except Exception:  # pragma: no cover
    sys.exit(0)

_DELETE = re.compile(r"\b(rm|rmdir|unlink|shred)\b|\bgit\s+rm\b")
_REMOTE = [
    ("push", re.compile(r"\bgit\s+push\b"), model.GRANT_PUSH),
    ("remote-write", re.compile(r"\b(scp|rsync)\b"), model.GRANT_REMOTE_WRITE),
    ("remote-write",
     re.compile(r"\bcurl\b.*(--upload-file|\s-T\b|-X\s*(PUT|POST))",
                re.DOTALL),
     model.GRANT_REMOTE_WRITE),
    ("remote-write", re.compile(r"\bwget\b.*--post", re.DOTALL),
     model.GRANT_REMOTE_WRITE),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    try:
        root = store.project_root(data.get("cwd"))
        active = store.active_tasks(root)
    except Exception:
        return 0

    if _DELETE.search(cmd):
        sys.stderr.write(
            "waypoint: local delete is not allowed. Move the target into "
            "to-be-deleted/ instead of deleting it.\n")
        return 2

    for op, rx, grant in _REMOTE:
        if rx.search(cmd):
            if any(model.has_grant(t, grant) for _, t in active):
                return 0
            for tid, _ in active:
                try:
                    runtime.append_event(root, tid, "needs-auth",
                                         op=op, command=cmd)
                except Exception:
                    pass
            sys.stderr.write(
                f"waypoint: '{op}' is not authorized for this task. It has "
                f"been surfaced for approval; do not retry until granted.\n")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
