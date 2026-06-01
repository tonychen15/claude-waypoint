# Waypoint Phase 2 — Slice 2b-i: Worker Command Construction (pure)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure, launch-nothing construction of the headless worker's launch command — the permission posture (`dontAsk` + allowlist/denylist, grant-conditional), the session `--settings` JSON that wires the four Phase-2 worker hooks, and the full `claude` argv. Launches nothing; everything is unit-tested.

**Architecture:** Extend `waypoint/worker.py` (already has `seed_prompt`) with three pure functions: `permission_args(task)`, `worker_settings(root, task_id)`, `build_command(...)`. The posture is **deny-by-default** (`--permission-mode dontAsk` + an explicit `--allowedTools` allowlist), matching the Phase 2 permission policy; the deny-guard hook (Slice 2a) is defense-in-depth. `--settings` is passed as **inline JSON** (verified supported), pointing at waypoint's own hook scripts by absolute path.

**Tech Stack:** Python 3.12 stdlib (`json`, `os`), pytest. Reuses Phase 1 `model` and Slice 2a `worker.seed_prompt`, `model.has_grant`.

**Verified facts (claude 2.1.159):** permission modes include `dontAsk`; `--allowedTools/--disallowedTools` are comma/space-separated globs like `"Bash(git *) Edit"`; `--settings <file-or-json>` accepts inline JSON; positional `[prompt]` seeds the run; `-p`, `--output-format`, `--add-dir`, `--resume`, `--session-id` exist.

**Review protocol:** Per `CLAUDE.md`, every source commit passes the Gemini cross-LLM review (no CRITICAL/WARNING) before moving on. A `waypoint` step (`phase2-design`) is open. Branch `feat/phase2-reconciler`. Tests: `.venv/bin/python -m pytest`.

**Scope discipline:** This slice **launches nothing** — no `subprocess`, no `run` command, no auth-gate UI (Slice 2b-ii). It only *constructs* argv/settings/prompt. If a reviewer suggests spawning, tmux, or the `run` command, DECLINE as out of scope. The exact allowlist contents are a documented best-effort default (adjustable); the user validates against a real `claude` run before relying on autonomy.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/worker.py` | add `permission_args`, `worker_settings`, `build_command` (pure) | Modify |
| `tests/test_worker.py` | tests for the three functions | Modify |

---

## Task 1: `permission_args(task)` — deny-by-default posture

**Files:**
- Modify: `waypoint/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_worker.py`:

```python
def test_permission_args_dont_ask_with_allow_and_deny():
    args = worker.permission_args(_task())
    assert "--permission-mode" in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    allow = args[args.index("--allowedTools") + 1]
    deny = args[args.index("--disallowedTools") + 1]
    assert "Edit" in allow and "Bash(waypoint*)" in allow
    assert "Bash(rm*)" in deny and "Bash(git push*)" in deny


def test_permission_args_push_grant_moves_push_to_allow():
    t = _task()
    model.set_grant(t, model.GRANT_PUSH)
    args = worker.permission_args(t)
    allow = args[args.index("--allowedTools") + 1]
    deny = args[args.index("--disallowedTools") + 1]
    assert "Bash(git push*)" in allow
    assert "Bash(git push*)" not in deny


def test_permission_args_remote_write_grant_allows_transfer_tools():
    t = _task()
    model.set_grant(t, model.GRANT_REMOTE_WRITE)
    allow = worker.permission_args(t)[
        worker.permission_args(t).index("--allowedTools") + 1]
    assert "Bash(scp*)" in allow and "Bash(rsync*)" in allow
```

(`_task()` and the `model` import already exist in this test file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k permission -v`
Expected: FAIL (`permission_args` undefined).

- [ ] **Step 3: Implement in `waypoint/worker.py`**

Add at module level (after the imports — add `from . import model` to the top):

```python
# Deny-by-default worker posture. The allowlist enumerates what an autonomous
# worker may do; everything else is denied (``dontAsk``). The deny-guard hook
# is defense-in-depth. These are best-effort defaults — validate against a
# real ``claude`` run before relying on autonomy.
_ALLOW_BASE = [
    "Read", "Edit", "Write",
    "Bash(waypoint*)",
    "Bash(git add*)", "Bash(git commit*)", "Bash(git status*)",
    "Bash(git diff*)", "Bash(git log*)", "Bash(git restore*)",
    "Bash(ls*)", "Bash(cat*)", "Bash(grep*)", "Bash(find*)",
    "Bash(mkdir*)", "Bash(mv*)", "Bash(cp*)", "Bash(touch*)",
    "Bash(python*)", "Bash(python3*)", "Bash(pytest*)",
    "Bash(npm*)", "Bash(node*)", "Bash(pip*)",
]
_DENY_BASE = ["Bash(rm*)", "Bash(git rm*)", "Bash(git push*)", "Bash(sudo*)"]
_REMOTE_WRITE_TOOLS = ["Bash(scp*)", "Bash(rsync*)", "Bash(curl*)", "Bash(wget*)"]


def permission_args(task: dict) -> list:
    """Return the ``--permission-mode``/``--allowedTools``/``--disallowedTools``
    argv for the worker, adjusted for the task's grants (deny-by-default)."""
    allow = list(_ALLOW_BASE)
    deny = list(_DENY_BASE)
    if model.has_grant(task, model.GRANT_PUSH):
        allow.append("Bash(git push*)")
        deny = [d for d in deny if d != "Bash(git push*)"]
    if model.has_grant(task, model.GRANT_REMOTE_WRITE):
        allow.extend(_REMOTE_WRITE_TOOLS)
    return [
        "--permission-mode", "dontAsk",
        "--allowedTools", " ".join(allow),
        "--disallowedTools", " ".join(deny),
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k permission -v`
Expected: PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/worker.py tests/test_worker.py
git commit -m "feat(worker): deny-by-default permission posture (grant-conditional)"
```

---

## Task 2: `worker_settings(root, task_id)` — session hooks JSON

**Files:**
- Modify: `waypoint/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_worker.py`:

```python
def test_worker_settings_wires_all_four_phase2_hooks(tmp_path):
    s = worker.worker_settings(str(tmp_path), "t1")
    hooks = s["hooks"]
    assert set(hooks) == {"PostToolUse", "Notification", "Stop", "PreToolUse"}
    # Each references the corresponding hook script by absolute path.
    flat = json.dumps(s)
    for script in ("post_tool_use.py", "notification.py", "stop.py",
                   "pre_tool_use_guard.py"):
        assert script in flat
    assert os.path.isabs(_first_command(hooks["Stop"]))


def _first_command(entry):
    return entry[0]["hooks"][0]["command"].split()[-1]
```

Add `import json` and `import os` at the top of `tests/test_worker.py` if not
already present.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k worker_settings -v`
Expected: FAIL (`worker_settings` undefined).

- [ ] **Step 3: Implement in `waypoint/worker.py`**

Add `import os` to the top of the module, then:

```python
def _hooks_dir() -> str:
    """Absolute path to waypoint's own hook scripts (``<repo>/hooks``)."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")


def _hook_entry(script: str, matcher: str | None = None) -> dict:
    cmd = f'python3 "{os.path.join(_hooks_dir(), script)}"'
    entry: dict = {"hooks": [{"type": "command", "command": cmd}]}
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def worker_settings(root: str, task_id: str) -> dict:
    """Return an inline ``--settings`` dict wiring the four Phase-2 worker
    hooks (heartbeat, notification, stop, deny-guard) by absolute path."""
    return {
        "hooks": {
            "PostToolUse": [_hook_entry("post_tool_use.py")],
            "Notification": [_hook_entry("notification.py")],
            "Stop": [_hook_entry("stop.py")],
            "PreToolUse": [_hook_entry("pre_tool_use_guard.py", matcher="Bash")],
        }
    }
```

(`root`/`task_id` are accepted for interface stability and future per-task
settings; the hooks resolve the active task themselves at runtime.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k worker_settings -v`
Expected: PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/worker.py tests/test_worker.py
git commit -m "feat(worker): session --settings JSON wiring the Phase-2 hooks"
```

---

## Task 3: `build_command(...)` — full worker argv

**Files:**
- Modify: `waypoint/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_worker.py`:

```python
def test_build_command_assembles_headless_worker(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t)
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--permission-mode" in argv and "dontAsk" in argv
    assert "--settings" in argv
    # the seed prompt is the final positional arg
    assert "waypoint set-step" in argv[-1]
    assert "--resume" not in argv


def test_build_command_with_resume_and_custom_bin(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t,
                                resume_session="sess-123", claude_bin="/x/fake")
    assert argv[0] == "/x/fake"
    assert argv[argv.index("--resume") + 1] == "sess-123"


def test_build_command_settings_is_valid_json(tmp_path):
    t = _task()
    argv = worker.build_command(str(tmp_path), t["task_id"], t)
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert "hooks" in settings
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k build_command -v`
Expected: FAIL (`build_command` undefined).

- [ ] **Step 3: Implement in `waypoint/worker.py`**

Add `import json` to the top of the module, then:

```python
def build_command(root: str, task_id: str, task: dict, *,
                  resume_session: str | None = None,
                  claude_bin: str = "claude") -> list:
    """Assemble the full headless-worker ``claude`` argv (launches nothing).

    Headless (``-p``) autonomous run: permission posture + the session hooks +
    the project dir + the seed prompt. With ``resume_session`` it resumes that
    session id; otherwise a fresh run.
    """
    argv = [claude_bin, "-p"]
    if resume_session:
        argv += ["--resume", resume_session]
    argv += permission_args(task)
    argv += ["--settings", json.dumps(worker_settings(root, task_id))]
    argv += ["--add-dir", root]
    argv += ["--output-format", "stream-json", "--verbose"]
    argv += [seed_prompt(task)]
    return argv
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k build_command -v`
Expected: PASS. Then full suite `.venv/bin/python -m pytest -q` → ALL PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/worker.py tests/test_worker.py
git commit -m "feat(worker): build_command assembles the headless worker argv"
```

---

## Self-review notes (author)

- **Spec coverage (Slice 2b-i only):** the worker permission posture
  (`dontAsk` + allowlist/denylist, grant-conditional) (T1) · the session
  `--settings` hooks JSON (T2) · the full launch argv with resume + custom-bin
  seams (T3). The actual **spawn**, the **auth gate**, **worker.json**, and
  **manual resume** are **Slice 2b-ii**; the **autonomous guard** is **Slice 3**.
- **Launches nothing** — pure construction; the `claude_bin` seam lets Slice
  2b-ii point tests at a fake-worker stub.
- **Naming consistency:** `worker.seed_prompt/permission_args/worker_settings/
  build_command` and `model.GRANT_PUSH/GRANT_REMOTE_WRITE/has_grant` are used
  identically across tasks. `build_command` returns argv consumed (later) by
  the Slice 2b-ii launcher.
- **Posture is best-effort/adjustable** — the allowlist is a documented
  constant; real-`claude` validation is the user's pre-autonomy check, called
  out in the spec's "unverified dependencies."
