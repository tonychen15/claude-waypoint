# Waypoint Phase 2 — Slice 1: Liveness + `watch` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the safe foundation of the Phase 2 orchestrator — a `runtime/` liveness store, worker-side hooks that emit a tool-activity heartbeat and lifecycle events, and a read-only `waypoint watch` live monitor. Zero auto-kill, nothing spawns Claude yet.

**Architecture:** A new `waypoint/runtime.py` owns the ephemeral `runtime/` subdir under each task (`heartbeat` file whose mtime = last tool activity; `events.jsonl` append log). Three new defensive hook scripts (`post_tool_use`, `notification`, `stop`) write into it from a worker session. A pure `waypoint/monitor.py` renders a task + a runtime snapshot to text; `waypoint watch` loops that render. All consistent with Phase 1 patterns (stdlib only, atomic-ish writes, hooks never raise).

**Tech Stack:** Python 3.12 stdlib (`os`, `json`, `time`, `argparse`), pytest. Reuses Phase 1 `store`/`model`/`progress`.

**Review protocol:** Per the repo/global `CLAUDE.md`, every commit that edits a source file MUST pass the Gemini cross-LLM review (`gemini -p "Review the following changes as a staff engineer: $(git diff --staged)"`) with no CRITICAL/WARNING before moving on. A `waypoint` step (`phase2-design`) is open, so file edits are permitted by the PreToolUse hook. Work on branch `feat/phase2-reconciler`. Run tests with `.venv/bin/python -m pytest`.

**Scope discipline:** Implement EXACTLY what each task specifies. This slice has NO process spawning, NO takeover, NO tmux. If a reviewer suggests adding those, DECLINE as out of scope (later slices).

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/runtime.py` | the `runtime/` store: heartbeat (touch/age), events (append/read), snapshot | **Create** |
| `waypoint/monitor.py` | pure render of (task, runtime snapshot) → text for `watch` | **Create** |
| `hooks/post_tool_use.py` | worker hook: touch heartbeat on every tool call | **Create** |
| `hooks/notification.py` | worker hook: record a `notification` event (waiting/idle) | **Create** |
| `hooks/stop.py` | worker hook: record a `turn_done` event | **Create** |
| `waypoint/cli.py` | add the read-only `watch` subcommand | Modify |
| `tests/test_runtime.py` | runtime store tests | **Create** |
| `tests/test_monitor.py` | render tests | **Create** |
| `tests/test_hooks.py` | extend with the three new worker hooks | Modify |
| `tests/test_cli.py` | `watch --once` test | Modify |
| `README.md` / `cli.py` docstring | document `watch` (read-only monitor) | Modify |

---

## Task 1: `runtime.py` — heartbeat (touch + age)

**Files:**
- Create: `waypoint/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runtime.py`:

```python
"""Tests for the Phase 2 runtime liveness store."""

import os
import time

from waypoint import runtime, store


def test_runtime_dir_under_task(tmp_path):
    root = str(tmp_path)
    assert runtime.runtime_dir(root, "t1") == os.path.join(
        store.task_dir(root, "t1"), "runtime")


def test_touch_heartbeat_creates_file_and_age_is_small(tmp_path):
    root = str(tmp_path)
    runtime.touch_heartbeat(root, "t1")
    age = runtime.heartbeat_age(root, "t1")
    assert age is not None and age < 2.0


def test_heartbeat_age_none_when_absent(tmp_path):
    assert runtime.heartbeat_age(str(tmp_path), "t1") is None


def test_heartbeat_age_reflects_old_mtime(tmp_path):
    root = str(tmp_path)
    runtime.touch_heartbeat(root, "t1")
    hb = os.path.join(runtime.runtime_dir(root, "t1"), "heartbeat")
    old = time.time() - 600
    os.utime(hb, (old, old))
    assert runtime.heartbeat_age(root, "t1") >= 590
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_runtime.py -v`
Expected: FAIL (`No module named 'waypoint.runtime'`).

- [ ] **Step 3: Create `waypoint/runtime.py`**

```python
"""Phase 2 runtime liveness store (the ephemeral half of the channel).

Lives at ``<task_dir>/runtime/`` (already gitignored under
``.claude/waypoint/``). Holds short-lived signals the worker emits and the
guard reads: a ``heartbeat`` file (mtime = last tool activity) and an
``events.jsonl`` append log. Safe to delete between runs; always rebuildable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from . import model, store

RUNTIME_DIRNAME = "runtime"
HEARTBEAT_FILE = "heartbeat"
EVENTS_FILE = "events.jsonl"


def runtime_dir(root: str, task_id: str) -> str:
    """Return ``<task_dir>/runtime`` for the task."""
    return os.path.join(store.task_dir(root, task_id), RUNTIME_DIRNAME)


def _ensure(root: str, task_id: str) -> str:
    d = runtime_dir(root, task_id)
    os.makedirs(d, exist_ok=True)
    return d


def touch_heartbeat(root: str, task_id: str) -> None:
    """Update the heartbeat mtime to 'now' (creating it if needed)."""
    d = _ensure(root, task_id)
    path = os.path.join(d, HEARTBEAT_FILE)
    with open(path, "a", encoding="utf-8"):
        pass
    os.utime(path, None)


def heartbeat_age(root: str, task_id: str) -> Optional[float]:
    """Seconds since the last heartbeat, or None if there is none."""
    path = os.path.join(runtime_dir(root, task_id), HEARTBEAT_FILE)
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): heartbeat store for Phase 2 liveness"
```

---

## Task 2: `runtime.py` — events (append + read + snapshot)

**Files:**
- Modify: `waypoint/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime.py`:

```python
def test_append_and_read_events_roundtrip(tmp_path):
    root = str(tmp_path)
    runtime.append_event(root, "t1", "notification", message="waiting")
    runtime.append_event(root, "t1", "turn_done")
    evs = runtime.read_events(root, "t1")
    assert [e["kind"] for e in evs] == ["notification", "turn_done"]
    assert evs[0]["message"] == "waiting"
    assert all("ts" in e for e in evs)


def test_read_events_limit_returns_last_n(tmp_path):
    root = str(tmp_path)
    for i in range(5):
        runtime.append_event(root, "t1", "turn_done", n=i)
    evs = runtime.read_events(root, "t1", limit=2)
    assert [e["n"] for e in evs] == [3, 4]


def test_read_events_empty_when_absent(tmp_path):
    assert runtime.read_events(str(tmp_path), "t1") == []


def test_snapshot_shape(tmp_path):
    root = str(tmp_path)
    runtime.touch_heartbeat(root, "t1")
    runtime.append_event(root, "t1", "turn_done")
    snap = runtime.snapshot(root, "t1")
    assert snap["heartbeat_age"] is not None
    assert snap["events"] and snap["events"][-1]["kind"] == "turn_done"


def test_corrupt_events_line_is_skipped(tmp_path):
    root = str(tmp_path)
    runtime.append_event(root, "t1", "turn_done")
    path = os.path.join(runtime.runtime_dir(root, "t1"), "events.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    runtime.append_event(root, "t1", "notification")
    kinds = [e["kind"] for e in runtime.read_events(root, "t1")]
    assert kinds == ["turn_done", "notification"]   # bad line skipped
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_runtime.py -k "event or snapshot or corrupt" -v`
Expected: FAIL (`append_event`/`read_events`/`snapshot` undefined).

- [ ] **Step 3: Extend `waypoint/runtime.py`**

Add to the module:

```python
def append_event(root: str, task_id: str, kind: str, **fields) -> None:
    """Append a ``{ts, kind, **fields}`` JSON line to events.jsonl."""
    d = _ensure(root, task_id)
    rec = {"ts": model.now_iso(), "kind": kind}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with open(os.path.join(d, EVENTS_FILE), "a", encoding="utf-8") as fh:
        fh.write(line)


def read_events(root: str, task_id: str, limit: int = 20) -> list:
    """Return up to the last ``limit`` events (malformed lines skipped)."""
    path = os.path.join(runtime_dir(root, task_id), EVENTS_FILE)
    out: list = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]


def snapshot(root: str, task_id: str, *, events_limit: int = 5) -> dict:
    """A point-in-time liveness snapshot for rendering."""
    return {
        "heartbeat_age": heartbeat_age(root, task_id),
        "events": read_events(root, task_id, limit=events_limit),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_runtime.py -v`
Expected: PASS (all runtime tests).

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): events log + snapshot"
```

---

## Task 3: `monitor.py` — pure render

**Files:**
- Create: `waypoint/monitor.py`
- Test: `tests/test_monitor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_monitor.py`:

```python
"""Tests for the read-only monitor render (pure function)."""

from waypoint import model, monitor


def _task():
    t = model.new_task("t1", "build the thing")
    t["plan"] = [{"id": "a", "purpose": "first"}, {"id": "b", "purpose": "second"}]
    t["steps"].append({"id": "a", "purpose": "first", "status": "succeeded"})
    return t


def test_render_no_worker_activity():
    out = monitor.render(_task(), {"heartbeat_age": None, "events": []})
    assert "t1" in out
    assert "1 of 2 done" in out                 # progress line
    assert "no worker activity yet" in out


def test_render_active_worker_and_events():
    snap = {"heartbeat_age": 8.0,
            "events": [{"ts": "2026-06-01T00:00:00+00:00",
                        "kind": "notification", "message": "waiting"}]}
    out = monitor.render(_task(), snap)
    assert "active" in out and "8s ago" in out
    assert "notification" in out and "waiting" in out


def test_render_idle_worker_formats_minutes():
    out = monitor.render(_task(), {"heartbeat_age": 305.0, "events": []})
    assert "idle" in out and "5m" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitor.py -v`
Expected: FAIL (`No module named 'waypoint.monitor'`).

- [ ] **Step 3: Create `waypoint/monitor.py`**

```python
"""Read-only render of a task + runtime snapshot for ``waypoint watch``.

Pure: ``render(task, snapshot) -> str``. No I/O, so it is fully unit-tested;
the ``watch`` command supplies the snapshot and handles the refresh loop.
"""

from __future__ import annotations

from . import progress


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s ago"
    h, m = divmod(m, 60)
    return f"{h}h {m}m ago"


def _liveness_line(heartbeat_age) -> str:
    if heartbeat_age is None:
        return "worker: no worker activity yet"
    label = "active" if heartbeat_age < 60 else "idle"
    return f"worker: {label} (last tool { _fmt_age(heartbeat_age) })"


def render(task: dict, snapshot: dict) -> str:
    """Return the live-monitor text for a task and its runtime snapshot."""
    tid = task.get("task_id", "?")
    status = task.get("status", "?")
    lines = [
        f"# {tid}   ({status})",
        f"progress: {progress.summary(task)}",
        _liveness_line(snapshot.get("heartbeat_age")),
    ]
    events = snapshot.get("events") or []
    if events:
        lines.append("recent:")
        for e in events:
            extra = e.get("message", "")
            lines.append(f"  {e.get('ts', '?')}  {e.get('kind', '?')}"
                         + (f"  {extra}" if extra else ""))
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_monitor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): pure render for the watch display"
```

---

## Task 4: Worker hooks — heartbeat + events

**Files:**
- Create: `hooks/post_tool_use.py`, `hooks/notification.py`, `hooks/stop.py`
- Test: `tests/test_hooks.py`

Each hook follows the Phase 1 pattern (read JSON from stdin, resolve the
project root from `cwd`, never raise, exit 0). They write liveness for every
active task. They are NOT wired into `settings.json` here — Slice 2's worker
bootstrap installs them in the worker session.

- [ ] **Step 1: Write the failing tests**

`tests/test_hooks.py` already has a `_load(name)` helper and a `root` fixture.
Append:

```python
def test_post_tool_use_touches_heartbeat(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("post_tool_use")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    assert _run(mod, {"tool_name": "Edit", "cwd": root}, monkeypatch) == 0
    assert runtime.heartbeat_age(root, "t1") is not None


def test_notification_records_event(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("notification")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    _run(mod, {"message": "waiting for your input", "cwd": root}, monkeypatch)
    evs = runtime.read_events(root, "t1")
    assert evs and evs[-1]["kind"] == "notification"
    assert "waiting" in evs[-1]["message"]


def test_stop_records_turn_done(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("stop")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    _run(mod, {"cwd": root}, monkeypatch)
    evs = runtime.read_events(root, "t1")
    assert evs and evs[-1]["kind"] == "turn_done"


def test_worker_hooks_never_raise_on_garbage(root, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    for name in ("post_tool_use", "notification", "stop"):
        mod = _load(name)
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
        assert mod.main() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hooks.py -k "post_tool_use or notification or stop or garbage" -v`
Expected: FAIL (hook modules don't exist).

- [ ] **Step 3: Create the three hook scripts**

`hooks/post_tool_use.py`:

```python
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
        for tid, _ in store.active_tasks(root):
            runtime.touch_heartbeat(root, tid)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`hooks/notification.py`:

```python
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
        for tid, _ in store.active_tasks(root):
            runtime.append_event(root, tid, "notification", message=msg)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`hooks/stop.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_hooks.py -v`
Expected: PASS (existing + new hook tests).

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add hooks/post_tool_use.py hooks/notification.py hooks/stop.py tests/test_hooks.py
git commit -m "feat(hooks): worker heartbeat + notification/stop events"
```

---

## Task 5: CLI `watch` command (read-only monitor)

**Files:**
- Modify: `waypoint/cli.py` (add `cmd_watch`; register subparser)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_watch_once_renders_progress_and_liveness(root, capsys):
    from waypoint import runtime
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    runtime.touch_heartbeat(root, "t1")
    capsys.readouterr()
    assert cli.main(["watch", "--once", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "0 of 1 done" in out
    assert "worker:" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k watch_once -v`
Expected: FAIL (`invalid choice: 'watch'`).

- [ ] **Step 3: Implement `cmd_watch`**

Add `monitor` and `runtime` to the top imports of `waypoint/cli.py`:
```python
from . import __version__, fingerprint, model, monitor, progress, runtime, statusmd, store
```

Add the command (near the other `cmd_*` functions):
```python
def cmd_watch(root: str, args) -> int:
    import time
    task_id, _ = _resolve(root, args.id)
    while True:
        _, task = _resolve(root, task_id)   # reload each tick
        snap = runtime.snapshot(root, task_id)
        print(monitor.render(task, snap))
        if args.once or task.get("status") != model.IN_PROGRESS:
            return 0
        print("-" * 40)
        time.sleep(args.interval)
```

Register the subparser in `build_parser` (its own block, since it has extra
flags):
```python
    s = sub.add_parser("watch", parents=[common]); s.set_defaults(fn=cmd_watch)
    s.add_argument("--id")
    s.add_argument("--once", action="store_true",
                   help="render once and exit (no refresh loop)")
    s.add_argument("--interval", type=float, default=3.0,
                   help="refresh seconds when looping (default: 3)")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k watch_once -v`
Expected: PASS. Then full suite `.venv/bin/python -m pytest -q` → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): read-only 'watch' live monitor"
```

---

## Task 6: Docs — `watch` in README + docstring

**Files:**
- Modify: `waypoint/cli.py` (module docstring), `README.md`

- [ ] **Step 1: Update the `cli.py` module docstring**

Add a line to the usage block (after `waypoint where`):
```python
    waypoint watch    [--id TASK] [--once] [--interval S]
```
And in the prose below the block, add:
```python
``watch`` is a read-only live monitor of a task's progress and worker liveness
(Phase 2). It never mutates state.
```

- [ ] **Step 2: Update `README.md`**

In the command table (Usage section), add a row after the `where` row:
```markdown
| `waypoint watch [--id <t>] [--once]` | Read-only live monitor: progress + worker liveness (Phase 2 reconciler). |
```

- [ ] **Step 3: Verify and run the suite**

Run: `waypoint watch --help` (confirm flags) and `.venv/bin/python -m pytest -q` (all pass).

- [ ] **Step 4: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py README.md
git commit -m "docs: document the read-only 'watch' monitor"
```

---

## Self-review notes (author)

- **Spec coverage (Slice 1 only):** `runtime/` store with `heartbeat` +
  `events.jsonl` (T1,T2) · worker hooks emitting heartbeat/notification/stop
  (T4) · read-only `watch` display (T3 render + T5 loop) · docs (T6). The
  `runtime/worker.json`, `takeovers.jsonl`, `guard.json`, the auth gate, the
  watchdog FSM, spawn, and takeover are **Slice 2/3** — intentionally absent.
- **No spawn / no auto-kill** in this slice — the riskiest pieces are deferred,
  per the staged build.
- **Naming consistency:** `runtime.runtime_dir/touch_heartbeat/heartbeat_age/
  append_event/read_events/snapshot` and `monitor.render` are used identically
  across tasks; `cli.cmd_watch` consumes `runtime.snapshot` + `monitor.render`.
- **Hooks not wired into settings.json** here (defensive, unit-tested via
  simulated stdin) — wiring into the worker session is Slice 2.
