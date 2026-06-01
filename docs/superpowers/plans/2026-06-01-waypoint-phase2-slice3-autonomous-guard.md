# Waypoint Phase 2 — Slice 3: Autonomous Guard (capstone)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The autonomous watchdog. A pure decision function (`guard.decide`) implements the FSM (WATCHING → TAKEOVER → WATCHING, plus HALTED/DONE), the three triggers (death / waiting-timeout / heartbeat-timeout), and the progress-gated loop guard (K consecutive no-progress takeovers → HALT). Thin wrappers gather observations from disk, persist guard state, execute takeovers via `launcher`, and notify on halt/completion. The `waypoint guard` loop and `run --guard` wire it together.

**Architecture:** New `waypoint/guard.py`. `decide(obs, gstate, config) -> (action, new_gstate)` is pure (table-tested). `observe(root, task_id)` reads task + worker liveness; `load_state`/`save_state` persist `runtime/guard.json`; `notify` is best-effort desktop + stdout; `step` does one observe→decide→act cycle (testable with a fake worker); `cmd_guard` loops `step` with sleep. Reuses Slice 1–2 `launcher`, `runtime`, `store`, `monitor`.

**Tech Stack:** Python 3.12 stdlib (`os`, `json`, `subprocess`, `datetime`, `time`), pytest. No new deps.

**Review protocol:** Per `CLAUDE.md`, every source commit passes the Gemini cross-LLM review (no CRITICAL/WARNING) before moving on. **Run gemini foreground; wait.** **Commit only the files named per task** — never `.gitignore`/`.gstack/`. Branch `feat/phase2-reconciler`. Tests: `.venv/bin/python -m pytest`.

**Scope discipline:** The auto-kill is the highest-stakes code in the project — keep it conservative and exactly as specified. NEVER launch real `claude` in tests (fake stubs). No tmux, no two-pane (deferred). If a reviewer suggests extra triggers or aggressive timeouts, DECLINE.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/guard.py` | pure `decide` + `observe` + state persistence + `notify` + `step` | **Create** |
| `waypoint/cli.py` | `guard` loop command + `run --guard` follow option | Modify |
| `tests/test_guard.py` | table-tested `decide`; `observe`/`step` with fake workers | **Create** |
| `tests/test_cli.py` | `run --guard --no-follow` smoke | Modify |
| `README.md` / `cli.py` docstring | document `guard` / `run --guard` | Modify |

---

## Task 1: `guard.decide` — pure FSM + triggers + progress gate

**Files:**
- Create: `waypoint/guard.py`
- Test: `tests/test_guard.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_guard.py`:

```python
"""Tests for the autonomous guard decision logic (pure) + step (fake workers)."""

from waypoint import guard

CFG = {"idle_timeout": 600.0, "wait_timeout": 300.0, "max_no_progress": 2}


def _obs(**kw):
    base = {"task_status": "in_progress", "alive": True,
            "heartbeat_age": 1.0, "waiting_age": None, "committed": 0}
    base.update(kw)
    return base


def _g(**kw):
    base = {"fsm": guard.WATCHING, "no_progress": 0, "baseline_committed": 0}
    base.update(kw)
    return base


def test_healthy_worker_keeps_watching():
    action, _ = guard.decide(_obs(), _g(), CFG)
    assert action == guard.WATCH


def test_completed_task_is_done():
    action, new = guard.decide(_obs(task_status="completed"), _g(), CFG)
    assert action == guard.COMPLETE and new["fsm"] == guard.DONE


def test_dead_worker_triggers_takeover():
    action, new = guard.decide(_obs(alive=False), _g(), CFG)
    assert action == guard.TAKEOVER and new["fsm"] == guard.WATCHING


def test_heartbeat_timeout_triggers_takeover():
    action, _ = guard.decide(_obs(heartbeat_age=999.0), _g(), CFG)
    assert action == guard.TAKEOVER


def test_waiting_timeout_triggers_takeover():
    action, _ = guard.decide(_obs(waiting_age=999.0), _g(), CFG)
    assert action == guard.TAKEOVER


def test_fresh_worker_no_heartbeat_is_not_a_stall():
    action, _ = guard.decide(_obs(heartbeat_age=None), _g(), CFG)
    assert action == guard.WATCH


def test_progress_resets_no_progress_counter():
    # committed advanced past baseline -> takeover but counter resets to 0
    action, new = guard.decide(_obs(alive=False, committed=3),
                               _g(no_progress=1, baseline_committed=1), CFG)
    assert action == guard.TAKEOVER and new["no_progress"] == 0
    assert new["baseline_committed"] == 3


def test_no_progress_takeovers_eventually_halt():
    g = _g(no_progress=1, baseline_committed=0)   # already 1 fruitless takeover
    action, new = guard.decide(_obs(alive=False, committed=0), g, CFG)
    assert action == guard.HALT and new["fsm"] == guard.HALTED


def test_terminal_states_are_sticky():
    for fsm, act in ((guard.HALTED, guard.HALT), (guard.DONE, guard.COMPLETE)):
        action, new = guard.decide(_obs(alive=False), _g(fsm=fsm), CFG)
        assert action == act and new["fsm"] == fsm
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_guard.py -v`
Expected: FAIL (`No module named 'waypoint.guard'`).

- [ ] **Step 3: Create `waypoint/guard.py`** (this task: constants + `decide` + `_trigger`)

```python
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

    committed = obs.get("committed", 0)
    baseline = gstate.get("baseline_committed", committed)
    no_progress = 0 if committed > baseline else gstate.get("no_progress", 0) + 1
    if no_progress >= config["max_no_progress"]:
        return HALT, {**gstate, "fsm": HALTED, "no_progress": no_progress}
    return TAKEOVER, {"fsm": WATCHING, "no_progress": no_progress,
                      "baseline_committed": committed}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_guard.py -v`
Expected: PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/guard.py tests/test_guard.py
git commit -m "feat(guard): pure FSM/triggers/progress-gate decision logic"
```

---

## Task 2: `observe` + guard-state persistence

**Files:**
- Modify: `waypoint/guard.py`
- Test: `tests/test_guard.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_guard.py`:

```python
def test_state_roundtrip_defaults(tmp_path):
    root = str(tmp_path)
    from waypoint import model, store
    store.save(root, model.new_task("t1", "g"))
    g = guard.load_state(root, "t1")
    assert g["fsm"] == guard.WATCHING and g["no_progress"] == 0
    g["no_progress"] = 2
    guard.save_state(root, "t1", g)
    assert guard.load_state(root, "t1")["no_progress"] == 2


def test_observe_reports_dead_worker(tmp_path):
    root = str(tmp_path)
    from waypoint import model, store
    store.save(root, model.new_task("t1", "g"))
    # worker.json with a pid that cannot be alive
    from waypoint import launcher, runtime
    import json, os
    os.makedirs(runtime.runtime_dir(root, "t1"), exist_ok=True)
    with open(launcher.worker_json_path(root, "t1"), "w") as fh:
        json.dump({"pid": 2 ** 31 - 1, "session_id": "s"}, fh)
    obs = guard.observe(root, "t1")
    assert obs["alive"] is False
    assert obs["task_status"] == "in_progress"
    assert obs["committed"] == 0


def test_observe_waiting_age_from_latest_notification(tmp_path):
    root = str(tmp_path)
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    runtime.append_event(root, "t1", "notification", message="waiting for input")
    obs = guard.observe(root, "t1")
    assert obs["waiting_age"] is not None and obs["waiting_age"] >= 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_guard.py -k "state_roundtrip or observe" -v`
Expected: FAIL (`load_state`/`observe` undefined).

- [ ] **Step 3: Extend `waypoint/guard.py`**

Add imports at the top (below the module docstring's `from __future__`):
```python
import json
import os
from datetime import datetime

from . import launcher, model, runtime, store
```

Add the functions:
```python
GUARD_FILE = "guard.json"


def _state_path(root: str, task_id: str) -> str:
    return os.path.join(runtime.runtime_dir(root, task_id), GUARD_FILE)


def load_state(root: str, task_id: str) -> dict:
    """Load persisted guard state, or fresh defaults."""
    try:
        with open(_state_path(root, task_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"fsm": WATCHING, "no_progress": 0, "baseline_committed": 0}


def save_state(root: str, task_id: str, gstate: dict) -> None:
    d = runtime.runtime_dir(root, task_id)
    os.makedirs(d, exist_ok=True)
    tmp = _state_path(root, task_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(gstate, fh)
    os.replace(tmp, _state_path(root, task_id))


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _event_age(ts: str):
    try:
        then = datetime.fromisoformat(ts)
        now = datetime.now(then.tzinfo) if then.tzinfo else datetime.now()
        return max(0.0, (now - then).total_seconds())
    except Exception:
        return None


def _waiting_age(events: list, heartbeat_age):
    """Age of the latest 'notification' event, if it is the most recent signal
    (no tool activity since). None otherwise."""
    notes = [e for e in events if e.get("kind") == "notification"]
    if not notes:
        return None
    age = _event_age(notes[-1].get("ts", ""))
    if age is None:
        return None
    # Only a waiting signal if no tool activity happened more recently.
    if heartbeat_age is not None and heartbeat_age < age:
        return None
    return age


def observe(root: str, task_id: str) -> dict:
    """Gather the guard's observations from disk for one tick."""
    task = store.load(root, task_id)
    info = launcher.worker_info(root, task_id)
    alive = _pid_alive(info.get("pid")) if info else False
    snap = runtime.snapshot(root, task_id, events_limit=20)
    return {
        "task_status": task.get("status"),
        "alive": alive,
        "heartbeat_age": snap["heartbeat_age"],
        "waiting_age": _waiting_age(snap["events"], snap["heartbeat_age"]),
        "committed": len(task.get("steps") or []),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_guard.py -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/guard.py tests/test_guard.py
git commit -m "feat(guard): observe() + guard.json state persistence"
```

---

## Task 3: `notify` + `step` (observe→decide→act)

**Files:**
- Modify: `waypoint/guard.py`
- Test: `tests/test_guard.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_guard.py`:

```python
def _stub(tmp_path, body="import time\ntime.sleep(30)\n"):
    p = tmp_path / "fakeclaude"
    p.write_text("#!/usr/bin/env python3\n" + body)
    p.chmod(0o755)
    return str(p)


def test_step_takes_over_dead_worker(tmp_path):
    import time
    from waypoint import launcher, model, store
    root = str(tmp_path)
    t = model.new_task("t1", "g")
    t["plan"] = [{"id": "a", "purpose": "p"}]
    store.save(root, t)
    stub = _stub(tmp_path)
    first = launcher.spawn(root, "t1", store.load(root, "t1"), claude_bin=stub)
    launcher.stop(root, "t1")                       # worker now dead
    time.sleep(0.3)
    action = guard.step(root, "t1", config=CFG, claude_bin=stub)
    try:
        assert action == guard.TAKEOVER
        assert launcher.worker_info(root, "t1")["pid"] != first["pid"]
    finally:
        launcher.stop(root, "t1")


def test_step_halts_after_no_progress(tmp_path):
    from waypoint import model, store
    root = str(tmp_path)
    store.save(root, model.new_task("t1", "g"))
    # worker dead, and guard already had a fruitless takeover
    import json, os
    from waypoint import launcher, runtime
    os.makedirs(runtime.runtime_dir(root, "t1"), exist_ok=True)
    with open(launcher.worker_json_path(root, "t1"), "w") as fh:
        json.dump({"pid": 2 ** 31 - 1, "session_id": "s"}, fh)
    guard.save_state(root, "t1",
                     {"fsm": guard.WATCHING, "no_progress": 1,
                      "baseline_committed": 0})
    action = guard.step(root, "t1", config=CFG, claude_bin="claude")
    assert action == guard.HALT
    assert guard.load_state(root, "t1")["fsm"] == guard.HALTED


def test_notify_never_raises():
    guard.notify("title", "body")     # best-effort; must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_guard.py -k "step or notify" -v`
Expected: FAIL (`step`/`notify` undefined).

- [ ] **Step 3: Extend `waypoint/guard.py`**

Add `import subprocess` to the top imports, then:

```python
def notify(title: str, message: str) -> None:
    """Best-effort desktop notification + stdout. Never raises."""
    print(f"[waypoint guard] {title}: {message}")
    try:
        subprocess.run(["notify-send", title, message],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def step(root: str, task_id: str, *, config: dict,
         claude_bin: str = "claude") -> str:
    """One observe→decide→act cycle. Returns the action taken."""
    gstate = load_state(root, task_id)
    obs = observe(root, task_id)
    action, new_gstate = decide(obs, gstate, config)
    save_state(root, task_id, new_gstate)

    if action == TAKEOVER:
        info = launcher.worker_info(root, task_id)
        session = info.get("session_id") if info else None
        runtime.append_event(root, task_id, "takeover",
                             reason=_trigger(obs, config),
                             committed=obs.get("committed"))
        launcher.stop(root, task_id)
        launcher.spawn(root, task_id, store.load(root, task_id),
                       claude_bin=claude_bin, resume_session=session)
    elif action == HALT:
        notify("task halted",
               f"{task_id}: no forward progress after repeated takeovers — "
               f"needs a human. Last worker left in place.")
    elif action == COMPLETE:
        notify("task complete", f"{task_id} finished.")
    return action
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_guard.py -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/guard.py tests/test_guard.py
git commit -m "feat(guard): notify + step (observe/decide/act takeover loop)"
```

---

## Task 4: `waypoint guard` loop + `run --guard`

**Files:**
- Modify: `waypoint/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_run_with_guard_spawns_and_returns(root, tmp_path):
    from waypoint import launcher, guard
    stub = tmp_path / "fakeclaude"
    stub.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    stub.chmod(0o755)
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "p", "--id", "t1", "--root", root])
    rc = cli.main(["run", "--id", "t1", "--guard", "--no-follow",
                   "--claude-bin", str(stub), "--root", root])
    try:
        assert rc == 0
        assert launcher.worker_info(root, "t1")["pid"]
        # guard state initialized
        assert guard.load_state(root, "t1")["fsm"] == guard.WATCHING
    finally:
        launcher.stop(root, "t1")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k run_with_guard -v`
Expected: FAIL (`--guard` unrecognized).

- [ ] **Step 3: Implement in `waypoint/cli.py`**

(a) Add `guard` to the top `from . import ...` line.

(b) Add the guard loop command:
```python
def cmd_guard(root: str, args) -> int:
    import time
    task_id, _ = _resolve(root, args.id)
    config = {"idle_timeout": args.idle_timeout, "wait_timeout": args.wait_timeout,
              "max_no_progress": args.max_no_progress}
    # Persist any fresh defaults so a restarted guard is consistent.
    guard.save_state(root, task_id, guard.load_state(root, task_id))
    while True:
        action = guard.step(root, task_id, config=config,
                            claude_bin=args.claude_bin)
        if action in (guard.HALT, guard.COMPLETE):
            return 0
        _, task = _resolve(root, task_id)
        print(monitor.render(task, runtime.snapshot(root, task_id)))
        if args.once:
            return 0
        print("-" * 40)
        time.sleep(args.interval)
```

(c) In `cmd_run`, change the follow tail to honor `--guard`:
```python
    if args.no_follow:
        return 0
    if getattr(args, "guard", False):
        return cmd_guard(root, args)
    return cmd_watch(root, args)
```
And in `cmd_run`, after spawning, initialize guard state when `--guard` is set
(so the test sees WATCHING even with `--no-follow`):
```python
    info = launcher.spawn(root, task_id, task, claude_bin=args.claude_bin)
    if getattr(args, "guard", False):
        guard.save_state(root, task_id, guard.load_state(root, task_id))
    print(f"worker started — pid {info['pid']}, session {info['session_id']}")
```

(d) Add `--guard` and the timeout flags to the `run` subparser, and register the
standalone `guard` subparser. In the `run` block add:
```python
    s.add_argument("--guard", action="store_true",
                   help="follow with the autonomous guard (auto-takeover) instead of read-only watch")
    s.add_argument("--idle-timeout", type=float, default=guard.DEFAULTS["idle_timeout"])
    s.add_argument("--wait-timeout", type=float, default=guard.DEFAULTS["wait_timeout"])
    s.add_argument("--max-no-progress", type=int, default=guard.DEFAULTS["max_no_progress"])
```
And add a `guard` subparser:
```python
    s = sub.add_parser("guard", parents=[common]); s.set_defaults(fn=cmd_guard)
    s.add_argument("--id")
    s.add_argument("--claude-bin", default="claude")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=float, default=3.0)
    s.add_argument("--idle-timeout", type=float, default=guard.DEFAULTS["idle_timeout"])
    s.add_argument("--wait-timeout", type=float, default=guard.DEFAULTS["wait_timeout"])
    s.add_argument("--max-no-progress", type=int, default=guard.DEFAULTS["max_no_progress"])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k run_with_guard -v` → PASS. Then full suite `.venv/bin/python -m pytest -q` → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): autonomous 'guard' loop + 'run --guard'"
```

---

## Task 5: Docs — `guard` / `run --guard`

**Files:**
- Modify: `waypoint/cli.py` (module docstring), `README.md`

- [ ] **Step 1: Update the `cli.py` module docstring usage block** — add after the `run` line:
```python
    waypoint guard    --id TASK [--idle-timeout S] [--wait-timeout S] [--max-no-progress K]
```
And extend the prose: `` ``guard`` (or ``run --guard``) is the autonomous
watchdog: it auto-takes-over a dead/stalled worker (kill + ``--resume``),
bounded by a progress-gated loop guard, and notifies on completion or when it
gives up. ``

- [ ] **Step 2: Update `README.md`** — add a row after the `resume-worker` row:
```markdown
| `waypoint guard [--id <t>]` (or `run --guard`) | Autonomous watchdog (Phase 2): auto-takeover on death/stall, progress-gated loop guard, completion/halt notification. |
```

- [ ] **Step 3: Verify**

Run `waypoint guard --help`; run `.venv/bin/python -m pytest -q` (all pass).

- [ ] **Step 4: Commit** (after foreground Gemini review passes)

```bash
git add waypoint/cli.py README.md
git commit -m "docs: document the autonomous 'guard' / 'run --guard'"
```

---

## Self-review notes (author)

- **Spec coverage (Slice 3):** FSM + three triggers + progress-gated loop
  guard, pure & table-tested (T1) · `observe` from disk + `guard.json`
  persistence (T2) · `notify` + `step` takeover via `launcher` (T3) · the
  `guard` loop + `run --guard` (T4) · docs (T5). Completes Phase 2.
- **Conservative auto-kill:** missing heartbeat ≠ stall; only death,
  waiting-past-`wait_timeout`, or heartbeat-past-`idle_timeout` trigger; the
  progress-gated counter HALTs after `max_no_progress` fruitless takeovers and
  notifies a human, leaving the worker in place. No infinite loop.
- **Resume mechanic:** takeover spawns with `resume_session` (`--resume`); a
  fresh spawn (no session) uses the `seed_prompt` brief — the
  try-`--resume`-then-brief intent. (A dedicated `--resume`-failure→fresh
  retry is a possible future refinement; the loop guard bounds failures today.)
- **Naming consistency:** `guard.decide/observe/step/notify/load_state/
  save_state`, the action constants (`WATCH/TAKEOVER/HALT/COMPLETE`), the FSM
  constants (`WATCHING/HALTED/DONE`), and `DEFAULTS` are used identically
  across tasks; `step` reuses `launcher.spawn/stop`, `runtime`, `store`.
- **No real `claude` in tests** — fake stubs; tests always `launcher.stop` in
  `finally`.
