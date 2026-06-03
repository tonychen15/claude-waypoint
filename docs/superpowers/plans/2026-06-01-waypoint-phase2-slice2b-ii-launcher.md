# Waypoint Phase 2 — Slice 2b-ii: Spawn Launcher + `run` + Manual Resume

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make waypoint actually launch (and relaunch) a background headless worker — a `launcher.py` that spawns/stops the `claude` subprocess and records `worker.json`, the `waypoint run` command (auth gate via grants + spawn + optional follow), and `waypoint resume-worker` (manual kill + `--resume`). Tested entirely against fake-worker stub scripts; no real `claude`.

**Architecture:** Clean separation — `worker.py` is pure construction (Slice 2b-i), the new `waypoint/launcher.py` holds process side-effects (spawn/stop + `runtime/worker.json`), `runtime.py` is the liveness store. `run` and `resume-worker` and (later) the Slice-3 guard all share `launcher.spawn`/`launcher.stop`. A controlled `--session-id` (a generated uuid) is passed on a fresh launch so `--resume <uuid>` works on takeover. The `claude_bin` seam lets tests point at a stub.

**Tech Stack:** Python 3.12 stdlib (`subprocess`, `signal`, `os`, `uuid`, `json`), pytest. Reuses `worker.build_command`, `runtime`, `model`, `store`.

**Review protocol:** Per `CLAUDE.md`, every source commit passes the Gemini cross-LLM review (no CRITICAL/WARNING) before moving on. **Run gemini in the foreground and wait.** **Commit only the files named in each task** — never `.gitignore` or `.gstack/`. Branch `feat/phase2-reconciler`. Tests: `.venv/bin/python -m pytest`.

**Scope discipline:** No autonomous guard / no auto-takeover / no FSM (Slice 3). `run` spawns and (optionally) follows with the existing read-only `watch` display; takeover here is **manual only** (`resume-worker`). If a reviewer suggests auto-kill, the FSM, or tmux, DECLINE as out of scope. NEVER launch real `claude` in a test — use the fake stub.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/worker.py` | add `session_id` param to `build_command` (fresh-launch id control) | Modify |
| `waypoint/launcher.py` | process side-effects: `spawn`, `stop`, `worker_info`, `worker.json` | **Create** |
| `waypoint/cli.py` | `run` (grants + spawn + follow) and `resume-worker` commands | Modify |
| `tests/test_launcher.py` | spawn/stop/worker_info via fake stubs | **Create** |
| `tests/test_cli.py` | `run --no-follow` + `resume-worker` via fake stub | Modify |
| `README.md` / `cli.py` docstring | document `run` + `resume-worker` | Modify |

---

## Task 1: `build_command` — controlled `--session-id` on fresh launch

**Files:**
- Modify: `waypoint/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_worker.py`:

```python
def test_build_command_fresh_uses_session_id(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t,
                                session_id="sess-abc")
    assert argv[argv.index("--session-id") + 1] == "sess-abc"
    assert "--resume" not in argv


def test_build_command_resume_takes_precedence_over_session_id(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t,
                                resume_session="r1", session_id="s1")
    assert argv[argv.index("--resume") + 1] == "r1"
    assert "--session-id" not in argv
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k "session_id or resume_takes" -v`
Expected: FAIL (`build_command` has no `session_id` param).

- [ ] **Step 3: Modify `build_command` in `waypoint/worker.py`**

Change the signature and the resume/session block. Replace:
```python
def build_command(root: str, task_id: str, task: dict, *,
                  resume_session: str | None = None,
                  claude_bin: str = "claude") -> list:
```
with:
```python
def build_command(root: str, task_id: str, task: dict, *,
                  resume_session: str | None = None,
                  session_id: str | None = None,
                  claude_bin: str = "claude") -> list:
```
and replace:
```python
    argv = [claude_bin, "-p"]
    if resume_session:
        argv += ["--resume", resume_session]
```
with:
```python
    argv = [claude_bin, "-p"]
    if resume_session:
        argv += ["--resume", resume_session]
    elif session_id:
        argv += ["--session-id", session_id]
```
(Update the docstring's resume sentence to: "With ``resume_session`` it resumes
that session id; otherwise a fresh run with ``session_id`` if given.")

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/worker.py tests/test_worker.py
git commit -m "feat(worker): build_command accepts a controlled --session-id"
```

---

## Task 2: `launcher.py` — spawn / stop / worker_info

**Files:**
- Create: `waypoint/launcher.py`
- Test: `tests/test_launcher.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_launcher.py`:

```python
"""Tests for the worker process launcher (fake-worker stubs; no real claude)."""

import os
import signal
import time

from waypoint import launcher, model, store


def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env python3\n" + body)
    p.chmod(0o755)
    return str(p)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_spawn_writes_worker_json_and_runs(tmp_path):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    stub = _stub(tmp_path, "fakeclaude", "import time\ntime.sleep(30)\n")
    info = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    try:
        assert info["pid"] and info["session_id"]
        assert launcher.worker_info(root, "t1")["pid"] == info["pid"]
        time.sleep(0.3)
        assert _alive(info["pid"])
    finally:
        launcher.stop(root, "t1")


def test_stop_kills_the_worker(tmp_path):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    stub = _stub(tmp_path, "fakeclaude", "import time\ntime.sleep(30)\n")
    info = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    assert launcher.stop(root, "t1") is True
    time.sleep(0.3)
    assert not _alive(info["pid"])


def test_spawn_fresh_passes_session_id_to_argv(tmp_path, monkeypatch):
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    out = tmp_path / "argv.txt"
    monkeypatch.setenv("ARGV_OUT", str(out))
    stub = _stub(tmp_path, "fakeclaude",
                 "import sys, os\n"
                 "open(os.environ['ARGV_OUT'], 'w').write('\\x00'.join(sys.argv))\n")
    info = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    time.sleep(0.3)
    argv = out.read_text().split("\x00")
    assert "--session-id" in argv
    assert argv[argv.index("--session-id") + 1] == info["session_id"]


def test_worker_info_none_when_absent(tmp_path):
    assert launcher.worker_info(str(tmp_path), "t1") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -v`
Expected: FAIL (`No module named 'waypoint.launcher'`).

- [ ] **Step 3: Create `waypoint/launcher.py`**

```python
"""Worker process side-effects (Phase 2): spawn, stop, and the worker.json
record. Pure command construction lives in ``worker.py``; the liveness store
in ``runtime.py``. ``run``, ``resume-worker``, and (later) the guard share
this one spawn/stop implementation so there is a single way to start/kill a
worker.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import uuid

from . import model, runtime, worker

WORKER_FILE = "worker.json"


def worker_json_path(root: str, task_id: str) -> str:
    return os.path.join(runtime.runtime_dir(root, task_id), WORKER_FILE)


def worker_info(root: str, task_id: str) -> dict | None:
    """Read the current worker record, or None if there is none."""
    try:
        with open(worker_json_path(root, task_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_worker(root: str, task_id: str, info: dict) -> None:
    d = runtime.runtime_dir(root, task_id)
    os.makedirs(d, exist_ok=True)
    tmp = worker_json_path(root, task_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    os.replace(tmp, worker_json_path(root, task_id))


def spawn(root: str, task_id: str, task: dict, *, claude_bin: str = "claude",
          resume_session: str | None = None) -> dict:
    """Launch the worker as a detached background process; record worker.json.

    A fresh launch generates and pins a ``--session-id`` so a later takeover
    can ``--resume`` it. Output (stdout+stderr) is appended to runtime/worker.log.
    """
    session_id = resume_session or str(uuid.uuid4())
    argv = worker.build_command(
        root, task_id, task, claude_bin=claude_bin,
        resume_session=resume_session,
        session_id=(None if resume_session else session_id))
    rdir = runtime.runtime_dir(root, task_id)
    os.makedirs(rdir, exist_ok=True)
    log_path = os.path.join(rdir, "worker.log")
    logf = open(log_path, "ab")
    proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT,
                            cwd=root, start_new_session=True)
    info = {"pid": proc.pid, "session_id": session_id,
            "started_at": model.now_iso(), "log": log_path,
            "resumed": bool(resume_session)}
    _write_worker(root, task_id, info)
    return info


def stop(root: str, task_id: str, *, sig: int = signal.SIGTERM) -> bool:
    """Signal the recorded worker process. True if a signal was delivered."""
    info = worker_info(root, task_id)
    if not info or not info.get("pid"):
        return False
    try:
        os.kill(int(info["pid"]), sig)
        return True
    except OSError:
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): spawn/stop a detached worker + worker.json"
```

---

## Task 3: `waypoint run` — grants + spawn + optional follow

**Files:**
- Modify: `waypoint/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_run_spawns_worker_with_grants(root, capsys, tmp_path):
    import time
    from waypoint import launcher
    stub = tmp_path / "fakeclaude"
    stub.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    stub.chmod(0o755)
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    rc = cli.main(["run", "--id", "t1", "--no-follow", "--allow", "push",
                   "--claude-bin", str(stub), "--root", root])
    try:
        assert rc == 0
        from waypoint import model, store
        assert model.has_grant(store.load(root, "t1"), "push") is True
        info = launcher.worker_info(root, "t1")
        assert info and info["pid"]
    finally:
        launcher.stop(root, "t1")


def test_run_requires_a_plan(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    assert cli.main(["run", "--id", "t1", "--no-follow", "--root", root]) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "run_spawns or run_requires" -v`
Expected: FAIL (`invalid choice: 'run'`).

- [ ] **Step 3: Implement in `waypoint/cli.py`**

Add `launcher` to the top import line (alongside `monitor, runtime`):
```python
from . import __version__, fingerprint, launcher, model, monitor, progress, runtime, statusmd, store, worker
```

Add the command:
```python
def cmd_run(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    if not task.get("plan"):
        print("waypoint: declare a plan first (`waypoint plan ...`) before "
              "run", file=sys.stderr)
        return 1
    for g in (args.allow or []):
        if g not in model.GRANTS:
            print(f"waypoint: unknown grant {g!r} (choices: "
                  f"{', '.join(sorted(model.GRANTS))})", file=sys.stderr)
            return 1
        model.set_grant(task, g)
    store.save(root, task)
    info = launcher.spawn(root, task_id, task, claude_bin=args.claude_bin)
    print(f"worker started — pid {info['pid']}, session {info['session_id']}")
    if args.no_follow:
        return 0
    return cmd_watch(root, args)
```

Register the subparser (its own block, extra flags). Note: `cmd_watch` reads
`args.once`/`args.interval`, so `run` must define them too (default to the
follow loop):
```python
    s = sub.add_parser("run", parents=[common]); s.set_defaults(fn=cmd_run)
    s.add_argument("--id")
    s.add_argument("--allow", action="append",
                   help="grant an outbound op (push|remote_write|remote_delete); repeatable")
    s.add_argument("--no-follow", action="store_true",
                   help="spawn the worker and return (do not follow with the monitor)")
    s.add_argument("--claude-bin", default="claude",
                   help="worker binary (default: claude; override for testing)")
    s.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--interval", type=float, default=3.0, help=argparse.SUPPRESS)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "run_spawns or run_requires" -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): 'run' — grants + spawn worker + optional follow"
```

---

## Task 4: `waypoint resume-worker` — manual kill + `--resume`

**Files:**
- Modify: `waypoint/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_resume_worker_stops_old_and_spawns_resume(root, tmp_path):
    import time
    from waypoint import launcher, store
    stub = tmp_path / "fakeclaude"
    stub.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    stub.chmod(0o755)
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "p", "--id", "t1", "--root", root])
    cli.main(["run", "--id", "t1", "--no-follow", "--claude-bin", str(stub),
              "--root", root])
    first = launcher.worker_info(root, "t1")
    rc = cli.main(["resume-worker", "--id", "t1", "--claude-bin", str(stub),
                   "--root", root])
    try:
        assert rc == 0
        second = launcher.worker_info(root, "t1")
        assert second["pid"] != first["pid"]
        assert second["resumed"] is True
        assert second["session_id"] == first["session_id"]  # same session resumed
    finally:
        launcher.stop(root, "t1")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k resume_worker -v`
Expected: FAIL (`invalid choice: 'resume-worker'`).

- [ ] **Step 3: Implement in `waypoint/cli.py`**

```python
def cmd_resume_worker(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    info = launcher.worker_info(root, task_id)
    session = info.get("session_id") if info else None
    launcher.stop(root, task_id)
    new = launcher.spawn(root, task_id, task, claude_bin=args.claude_bin,
                         resume_session=session)
    print(f"resumed worker — pid {new['pid']}, session {new['session_id']}"
          + ("" if session else " (fresh; no prior session)"))
    return 0
```

Register the subparser:
```python
    s = sub.add_parser("resume-worker", parents=[common])
    s.set_defaults(fn=cmd_resume_worker)
    s.add_argument("--id")
    s.add_argument("--claude-bin", default="claude")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k resume_worker -v` → PASS. Then full suite `.venv/bin/python -m pytest -q` → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): 'resume-worker' — manual kill + --resume relaunch"
```

---

## Task 5: Docs — `run` + `resume-worker`

**Files:**
- Modify: `waypoint/cli.py` (module docstring), `README.md`

- [ ] **Step 1: Update the `cli.py` module docstring usage block** — add after `waypoint watch`:
```python
    waypoint run      --id TASK [--allow GRANT ...] [--no-follow]
    waypoint resume-worker --id TASK
```
And add a sentence to the prose: `` ``run`` spawns a headless worker for the
task and follows it with the monitor; ``resume-worker`` kills and ``--resume``-
relaunches it. Outbound grants are off by default (``--allow push`` etc.). ``

- [ ] **Step 2: Update `README.md`** — add rows after the `watch` row:
```markdown
| `waypoint run --id <t> [--allow push] [--no-follow]` | Spawn a headless worker for the task (Phase 2) and follow it with the monitor. Outbound ops off by default. |
| `waypoint resume-worker --id <t>` | Manually kill the worker and relaunch it resuming its session (Phase 2). |
```

- [ ] **Step 3: Verify**

Run `waypoint run --help` and `waypoint resume-worker --help`; run `.venv/bin/python -m pytest -q` (all pass).

- [ ] **Step 4: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/cli.py README.md
git commit -m "docs: document 'run' and 'resume-worker'"
```

---

## Self-review notes (author)

- **Spec coverage (Slice 2b-ii only):** `launcher.spawn/stop/worker_info` +
  `worker.json` (T2) · controlled `--session-id` for resumable launches (T1) ·
  `run` = auth gate (grants) + spawn + optional follow (T3) · **manual**
  resume (T4) · docs (T5). The autonomous guard FSM, the three triggers,
  auto-takeover, progress-gated loop guard, and completion notification are
  **Slice 3**.
- **No real `claude` in tests** — fake stub scripts via the `--claude-bin` /
  `claude_bin` seam; spawn detaches (`start_new_session=True`) and tests always
  `launcher.stop` in a `finally`.
- **Naming consistency:** `launcher.spawn/stop/worker_info/worker_json_path`,
  `worker.build_command(..., session_id=)`, `model.GRANTS/set_grant/has_grant`,
  and the `worker.json` shape `{pid, session_id, started_at, log, resumed}` are
  used identically across tasks; `run` reuses `cmd_watch` for follow.
- **Shared spawn/stop** — Slice 3's guard will call the same
  `launcher.spawn/stop`, so the takeover mechanic is written once here.
- **Known deferred:** `hooks/` packaging for non-editable installs (flagged in
  Slice 2b-i) and the real-`claude` posture validation remain the user's
  pre-autonomy checks.
