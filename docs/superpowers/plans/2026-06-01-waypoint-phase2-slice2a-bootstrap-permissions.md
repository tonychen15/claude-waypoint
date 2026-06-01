# Waypoint Phase 2 — Slice 2a: Bootstrap Primitives + Permission Policy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the launch-nothing foundation for the headless worker — the outbound `grants` model, the pure `seed_prompt` the worker is launched with, and the worker `PreToolUse` deny-guard that enforces the permission policy (no local delete → `to-be-deleted/`; ungranted remote → `needs-auth`).

**Architecture:** `model.py` gains a `grants` dict + helpers (set on a task; read by the guard). A new `waypoint/worker.py` holds pure bootstrap construction (this slice: `seed_prompt`). A new `hooks/pre_tool_use_guard.py` is a worker-session deny-guard (same exit-0-allow / exit-2-block contract as the existing `pre_tool_use.py`), defense-in-depth atop the worker's allowlist. Nothing launches `claude`; everything is unit-tested.

**Tech Stack:** Python 3.12 stdlib (`json`, `re`, `os`, `sys`), pytest. Reuses Phase 1 `model`/`store` and Phase 2 Slice 1 `runtime`.

**Review protocol:** Per the repo/global `CLAUDE.md`, every commit that edits a source file MUST pass the Gemini cross-LLM review with no CRITICAL/WARNING before moving on. A `waypoint` step (`phase2-design`) is open. Work on branch `feat/phase2-reconciler`. Run tests with `.venv/bin/python -m pytest`.

**Scope discipline:** This slice **launches nothing** and adds **no `run`/launcher/auth-gate-UI** (that is Slice 2b). The permission *posture* command construction (`--allowedTools` etc.) is **Slice 2b** (needs a tool-syntax spike). If a reviewer suggests adding launching/spawn/tmux/posture-args, DECLINE as out of scope.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/model.py` | add `grants` dict + `set_grant`/`has_grant` + grant constants; migrate/validate | Modify |
| `waypoint/worker.py` | pure worker bootstrap construction — `seed_prompt(task)` (this slice) | **Create** |
| `hooks/pre_tool_use_guard.py` | worker deny-guard: block local delete + ungranted remote | **Create** |
| `tests/test_model.py` | grant helpers + migration | Modify |
| `tests/test_worker.py` | seed_prompt content | **Create** |
| `tests/test_hooks.py` | deny-guard behavior | Modify |

---

## Task 1: `grants` in the model

**Files:**
- Modify: `waypoint/model.py` (`new_task`, `validate`, `migrate`; add constants + helpers)
- Test: `tests/test_model.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_model.py`:

```python
def test_new_task_has_empty_grants():
    t = model.new_task("t1", "g")
    assert t["grants"] == {}


def test_set_and_has_grant():
    t = model.new_task("t1", "g")
    assert model.has_grant(t, model.GRANT_PUSH) is False
    model.set_grant(t, model.GRANT_PUSH)
    assert model.has_grant(t, model.GRANT_PUSH) is True
    model.set_grant(t, model.GRANT_PUSH, False)
    assert model.has_grant(t, model.GRANT_PUSH) is False


def test_migrate_adds_grants_to_legacy():
    legacy = {"task_id": "t", "goal": "g", "status": "in_progress",
              "created_at": "2026-01-01T00:00:00+00:00", "steps": [],
              "current_step": None, "plan": []}
    model.migrate(legacy)
    assert legacy["grants"] == {}


def test_validate_rejects_non_dict_grants():
    t = model.new_task("t1", "g")
    t["grants"] = ["push"]
    assert any("grants" in e for e in model.validate(t))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model.py -k "grant" -v`
Expected: FAIL (`KeyError: 'grants'` / `GRANT_PUSH` undefined / `set_grant` undefined).

- [ ] **Step 3: Implement in `waypoint/model.py`**

(a) After the effects-ledger constants (near the top), add:
```python
# Outbound-operation grants (Phase 2 permission policy). Default: nothing
# granted; the `run` authorization gate enables what a task may do.
GRANT_PUSH = "push"
GRANT_REMOTE_WRITE = "remote_write"
GRANT_REMOTE_DELETE = "remote_delete"
GRANTS = {GRANT_PUSH, GRANT_REMOTE_WRITE, GRANT_REMOTE_DELETE}
```

(b) In `new_task`'s returned dict, add `"grants": {},` (next to `"plan": []`).

(c) In `validate`, after the `plan` check, add:
```python
    if not isinstance(task.get("grants", {}), dict):
        errors.append("grants must be an object")
```

(d) In `migrate`, just before `return task`, add:
```python
    task.setdefault("grants", {})
```

(e) Add these helpers at the end of the module:
```python
def set_grant(task: dict, name: str, value: bool = True) -> None:
    """Grant (or revoke) an outbound operation for a task."""
    task.setdefault("grants", {})[name] = bool(value)


def has_grant(task: dict, name: str) -> bool:
    """True if ``name`` is granted for this task."""
    return bool((task.get("grants") or {}).get(name, False))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_model.py -v`
Expected: PASS (all model tests). Then full suite `.venv/bin/python -m pytest -q` → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/model.py tests/test_model.py
git commit -m "feat(model): outbound-operation grants (Phase 2 permission policy)"
```

---

## Task 2: `worker.py` — pure `seed_prompt`

**Files:**
- Create: `waypoint/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_worker.py`:

```python
"""Tests for pure worker-bootstrap construction."""

from waypoint import model, worker


def _task():
    t = model.new_task("2026-06-01-demo", "Add a /health endpoint")
    t["plan"] = [{"id": "api", "purpose": "add the route"},
                 {"id": "test", "purpose": "test it"}]
    return t


def test_seed_prompt_includes_goal_and_steps():
    s = worker.seed_prompt(_task())
    assert "Add a /health endpoint" in s
    assert "api" in s and "add the route" in s
    assert "test" in s and "test it" in s


def test_seed_prompt_states_the_policy_and_checkpoints():
    s = worker.seed_prompt(_task())
    assert "waypoint set-step" in s and "waypoint commit" in s
    assert "waypoint check" in s
    assert "to-be-deleted/" in s          # no-delete rule
    assert "remote" in s.lower()          # no-ungranted-remote rule
    assert "waypoint done" in s


def test_seed_prompt_handles_empty_plan():
    t = model.new_task("t1", "g")
    s = worker.seed_prompt(t)
    assert "no steps declared" in s
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: FAIL (`No module named 'waypoint.worker'`).

- [ ] **Step 3: Create `waypoint/worker.py`**

```python
"""Worker bootstrap construction (pure) for the Phase 2 reconciler.

Builds the inputs a background ``claude`` worker is launched with. This slice
provides the seed prompt; the permission posture and the subprocess launcher
are later slices. Pure functions of task state — they launch nothing and are
unit-tested without invoking ``claude``.
"""

from __future__ import annotations


def seed_prompt(task: dict) -> str:
    """Return the initial prompt for the worker (adopt-plan, then execute)."""
    tid = task.get("task_id", "?")
    goal = task.get("goal", "")
    steps = task.get("plan") or []
    lines = [
        f"You are the waypoint worker for task {tid!r}.",
        f"Goal: {goal}",
        "",
        "Adopt the DECLARED plan below — do not re-plan it:",
    ]
    if steps:
        for i, p in enumerate(steps, 1):
            lines.append(f"  {i}. {p.get('id')} — {p.get('purpose', '')}")
    else:
        lines.append("  (no steps declared yet)")
    lines += [
        "",
        "Before editing anything, reconcile reality: run `waypoint status` "
        "and `waypoint check`.",
        "Work the steps in order. For each: `waypoint set-step --step <id> "
        "--purpose <p>`, do the work, then `waypoint commit --summary <s> "
        "[--artifact <path> ...]`.",
        "Never delete files — move them into `to-be-deleted/` instead.",
        "Do not push or perform any remote operation unless explicitly "
        "granted; if you need one, stop and surface it rather than retrying.",
        "When every step is committed, run `waypoint done`.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/worker.py tests/test_worker.py
git commit -m "feat(worker): pure seed_prompt (adopt plan, checkpoint, policy)"
```

---

## Task 3: worker `PreToolUse` deny-guard

**Files:**
- Create: `hooks/pre_tool_use_guard.py`
- Test: `tests/test_hooks.py`

Contract (same as `hooks/pre_tool_use.py`): exit 0 allows; exit 2 + stderr
blocks. Errs open (allow) on any internal error — the allowlist is the primary
gate; this is defense-in-depth.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_hooks.py`:

```python
def test_guard_blocks_local_delete(root, monkeypatch):
    from waypoint import model, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("pre_tool_use_guard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    rc = _run(mod, {"tool_name": "Bash",
                    "tool_input": {"command": "rm -rf build"},
                    "cwd": root}, monkeypatch)
    assert rc == 2


def test_guard_blocks_ungranted_push_and_records_needs_auth(root, monkeypatch):
    from waypoint import model, runtime, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("pre_tool_use_guard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    rc = _run(mod, {"tool_name": "Bash",
                    "tool_input": {"command": "git push origin main"},
                    "cwd": root}, monkeypatch)
    assert rc == 2
    evs = runtime.read_events(root, "t1")
    assert evs and evs[-1]["kind"] == "needs-auth" and evs[-1]["op"] == "push"


def test_guard_allows_granted_push(root, monkeypatch):
    from waypoint import model, store
    t = model.new_task("t1", "g")
    model.set_grant(t, model.GRANT_PUSH)
    store.save(root, t)
    mod = _load("pre_tool_use_guard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    rc = _run(mod, {"tool_name": "Bash",
                    "tool_input": {"command": "git push"},
                    "cwd": root}, monkeypatch)
    assert rc == 0


def test_guard_allows_safe_bash_and_non_bash(root, monkeypatch):
    from waypoint import model, store
    store.save(root, model.new_task("t1", "g"))
    mod = _load("pre_tool_use_guard")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", root)
    assert _run(mod, {"tool_name": "Bash",
                      "tool_input": {"command": "ls -la"}, "cwd": root},
                monkeypatch) == 0
    assert _run(mod, {"tool_name": "Edit",
                      "tool_input": {"file_path": "x"}, "cwd": root},
                monkeypatch) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hooks.py -k guard -v`
Expected: FAIL (`pre_tool_use_guard` module doesn't exist).

- [ ] **Step 3: Create `hooks/pre_tool_use_guard.py`**

```python
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
     re.compile(r"\bcurl\b.*(--upload-file|\s-T\b|-X\s*(PUT|POST))"),
     model.GRANT_REMOTE_WRITE),
    ("remote-write", re.compile(r"\bwget\b.*--post"), model.GRANT_REMOTE_WRITE),
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_hooks.py -k guard -v`
Expected: PASS. Then full suite `.venv/bin/python -m pytest -q` → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add hooks/pre_tool_use_guard.py tests/test_hooks.py
git commit -m "feat(hooks): worker deny-guard (no local delete; ungranted remote -> needs-auth)"
```

---

## Self-review notes (author)

- **Spec coverage (Slice 2a only):** `grants` data model (T1) · the worker seed
  prompt with adopt-plan + checkpoint + no-delete + no-ungranted-remote policy
  (T2) · the deny-guard enforcing local-delete→`to-be-deleted/` and
  ungranted-remote→`needs-auth` (T3). The permission *posture* args
  (`--allowedTools`/`--disallowedTools`/`--permission-mode`), `worker.build_command`,
  the auth-gate UI, the `run` launcher, worker.json, and manual resume are
  **Slice 2b** (need a `--allowedTools` syntax spike + a fake-worker harness).
- **Launches nothing** — no `claude`, no subprocess, no tmux in this slice.
- **Naming consistency:** `model.GRANT_PUSH/GRANT_REMOTE_WRITE/GRANT_REMOTE_DELETE`,
  `model.set_grant/has_grant`, `worker.seed_prompt`, and the `needs-auth` event
  (`{op, command}`) are used identically across tasks and match the Slice 1
  `runtime.append_event` signature.
- **Err-open guard:** matches the existing hooks' never-wedge discipline; the
  allowlist posture (Slice 2b) is the primary gate, this is defense-in-depth.
