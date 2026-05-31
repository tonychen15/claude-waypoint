# Waypoint CLI Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make waypoint's inspection commands intuitive — remove the confusing `current`, add a declarable plan/roadmap with a progress counter, add `steps`/`where`, and make output informative-by-default — all scoped to the current folder.

**Architecture:** A new permanent `plan` roadmap replaces the consumed-away `pending` queue in the JSON model; a small `waypoint/progress.py` module centralizes the roadmap math (done/total/remaining/progress-line) so every consumer (`status`, `steps`, `list`, the commit/set-step beats, and `STATUS.md`) shares one source of truth. Legacy tasks migrate on load. The CLI gains `plan`, `steps`, `where`, a `-q/--quiet` global flag, and clearer error/help text; `current` is removed.

**Tech Stack:** Python 3.12 stdlib only (`argparse`, `json`, `os`, `subprocess`), pytest. No new dependencies.

**Review protocol:** Per the repo/global `CLAUDE.md`, every commit that edits a source file MUST pass the Gemini cross-LLM review (`gemini -p "Review the following changes as a staff engineer: $(git diff --staged)"`) with no CRITICAL/WARNING items before moving on. A `waypoint` step (`cli-redesign`) is already open, so file edits are permitted by the PreToolUse hook.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/model.py` | task dict construction, validation, **migration** | Modify |
| `waypoint/progress.py` | roadmap math + human render helpers (single source of truth) | **Create** |
| `waypoint/store.py` | apply migration on load; expose `waypoint_dir()` | Modify |
| `waypoint/statusmd.py` | render roadmap from `plan`/derived-remaining + progress line | Modify |
| `waypoint/cli.py` | command surface: remove `current`; add `plan`/`steps`/`where`; `--quiet`; beats; clearer errors | Modify |
| `tests/test_model.py` | model + migration tests | Modify |
| `tests/test_progress.py` | progress math unit tests | **Create** |
| `tests/test_store.py` | load-migration test | Modify |
| `tests/test_statusmd.py` | roadmap-from-plan + progress line | Modify |
| `tests/test_cli.py` | new commands, removed `current`, beats, errors | Modify |
| `README.md` | document new command surface | Modify |

---

## Task 1: Data model — permanent `plan` roadmap + migration

**Files:**
- Modify: `waypoint/model.py` (`new_task`, `validate`; add `migrate`)
- Test: `tests/test_model.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_model.py`:

```python
def test_new_task_has_empty_plan_and_no_pending():
    t = model.new_task("t1", "g")
    assert t["plan"] == []
    assert "pending" not in t


def test_migrate_legacy_pending_reconstructs_full_roadmap():
    # A legacy task: committed a, b; between steps; one planned 'c'.
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00", "steps": [
            {"id": "a", "purpose": "first", "status": "succeeded"},
            {"id": "b", "purpose": "second", "status": "succeeded"},
        ],
        "current_step": None,
        "pending": [{"id": "c", "purpose": "third"}],
    }
    model.migrate(legacy)
    assert [p["id"] for p in legacy["plan"]] == ["a", "b", "c"]
    assert legacy["plan"][2]["purpose"] == "third"
    assert "pending" not in legacy


def test_migrate_legacy_without_pending_means_no_plan():
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00",
        "steps": [{"id": "a", "purpose": "first", "status": "succeeded"}],
        "current_step": None, "pending": [],
    }
    model.migrate(legacy)
    assert legacy["plan"] == []


def test_migrate_is_idempotent():
    t = model.new_task("t1", "g")
    t["plan"] = [{"id": "a", "purpose": "p"}]
    model.migrate(t)
    assert t["plan"] == [{"id": "a", "purpose": "p"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_model.py -v`
Expected: the new tests FAIL (`KeyError: 'plan'` / `AttributeError: module ... has no attribute 'migrate'`).

- [ ] **Step 3: Implement the model changes**

In `waypoint/model.py`, change `new_task`'s returned dict: replace the line
`"pending": [],` with `"plan": [],`.

Extend `validate` — after the `current_step` block, before `return errors`, add:

```python
    if not isinstance(task.get("plan", []), list):
        errors.append("plan must be a list")
```

Add this function at the end of the module:

```python
def migrate(task: dict) -> dict:
    """Upgrade a task dict in place to the current shape.

    Ensures a permanent ``plan`` roadmap exists. Legacy tasks stored a
    consumed-away ``pending`` queue and no ``plan``; when there is forward
    intent (non-empty ``pending``), reconstruct the full roadmap from
    committed steps + the current step + pending. Otherwise leave the plan
    empty (no plan was ever declared). The obsolete ``pending`` key is
    dropped. Idempotent and never raises on a well-formed task.

    Args:
        task: The task dict (mutated in place).

    Returns:
        The same task dict, for chaining.
    """
    if "plan" not in task:
        pending = task.get("pending") or []
        if pending:
            plan = [{"id": s.get("id"), "purpose": s.get("purpose", "")}
                    for s in task.get("steps", [])]
            cur = task.get("current_step")
            if cur:
                plan.append({"id": cur.get("id"),
                             "purpose": cur.get("purpose", "")})
            plan.extend({"id": p.get("id"), "purpose": p.get("purpose", "")}
                        for p in pending)
            task["plan"] = plan
        else:
            task["plan"] = []
    task.pop("pending", None)
    return task
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_model.py -v`
Expected: PASS (all, including pre-existing model tests).

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/model.py tests/test_model.py
git commit -m "feat(model): permanent plan roadmap + legacy pending migration"
```

---

## Task 2: Apply migration on load; expose `waypoint_dir`

**Files:**
- Modify: `waypoint/store.py` (`load`, `list_tasks`; add `waypoint_dir`)
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`:

```python
def test_load_migrates_legacy_pending_to_plan(tmp_path):
    import json, os
    root = str(tmp_path)
    tdir = store.task_dir(root, "t1")
    os.makedirs(tdir, exist_ok=True)
    legacy = {
        "task_id": "t1", "goal": "g", "status": "in_progress",
        "created_at": "2026-01-01T00:00:00+00:00",
        "steps": [{"id": "a", "purpose": "first", "status": "succeeded"}],
        "current_step": None, "pending": [{"id": "b", "purpose": "second"}],
    }
    with open(store.state_path(root, "t1"), "w") as fh:
        json.dump(legacy, fh)
    t = store.load(root, "t1")
    assert [p["id"] for p in t["plan"]] == ["a", "b"]
    assert "pending" not in t


def test_waypoint_dir_points_under_dot_claude(tmp_path):
    import os
    root = str(tmp_path)
    assert store.waypoint_dir(root) == os.path.join(root, ".claude", "waypoint")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: FAIL (`'plan'` missing on load; `waypoint_dir` undefined).

- [ ] **Step 3: Implement the store changes**

In `waypoint/store.py`, add a public accessor just after `_wp_dir`:

```python
def waypoint_dir(root: str) -> str:
    """Public path to ``<root>/.claude/waypoint`` (the state directory)."""
    return _wp_dir(root)
```

In `load`, migrate before returning:

```python
    with open(state_path(root, task_id), encoding="utf-8") as fh:
        return model.migrate(json.load(fh))
```

In `list_tasks`, migrate each task as it is read — change the append line
inside the `try` block from:

```python
                    out.append((name, json.load(fh)))
```

to:

```python
                    out.append((name, model.migrate(json.load(fh))))
```

(`model` is already imported in `store.py`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/store.py tests/test_store.py
git commit -m "feat(store): migrate legacy tasks on load; expose waypoint_dir"
```

---

## Task 3: Progress module (roadmap math + render helpers)

**Files:**
- Create: `waypoint/progress.py`
- Test: `tests/test_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_progress.py`:

```python
"""Tests for roadmap math and progress rendering."""

from waypoint import model, progress


def _task_with_plan():
    t = model.new_task("t1", "g")
    t["plan"] = [{"id": i, "purpose": p} for i, p in
                 (("a", "first"), ("b", "second"), ("c", "third"))]
    return t


def test_no_plan_summary_counts_committed_steps():
    t = model.new_task("t1", "g")
    t["steps"] = [{"id": "a", "purpose": "x", "status": "succeeded"},
                  {"id": "b", "purpose": "y", "status": "succeeded"}]
    assert progress.has_plan(t) is False
    line = progress.summary(t)
    assert "2 steps committed" in line and "no plan" in line


def test_plan_in_progress_summary():
    t = _task_with_plan()
    t["steps"] = [{"id": "a", "purpose": "first", "status": "succeeded"}]
    t["current_step"] = {"id": "b", "purpose": "second", "status": "in_progress"}
    line = progress.summary(t)
    assert "1 of 3 done" in line
    assert "curr: step 2" in line
    assert "second" in line


def test_plan_between_steps_points_at_next():
    t = _task_with_plan()
    t["steps"] = [{"id": "a", "purpose": "first", "status": "succeeded"},
                  {"id": "b", "purpose": "second", "status": "succeeded"}]
    line = progress.summary(t)
    assert "2 of 3 done" in line
    assert "curr: step 3" in line and "third" in line


def test_plan_done_summary_has_checkmark():
    t = _task_with_plan()
    t["steps"] = [{"id": i, "purpose": p, "status": "succeeded"} for i, p in
                  (("a", "first"), ("b", "second"), ("c", "third"))]
    line = progress.summary(t)
    assert "3 of 3 done" in line and "✓" in line


def test_remaining_excludes_done_and_current():
    t = _task_with_plan()
    t["steps"] = [{"id": "a", "purpose": "first", "status": "succeeded"}]
    t["current_step"] = {"id": "b", "purpose": "second", "status": "in_progress"}
    assert [p["id"] for p in progress.remaining(t)] == ["c"]


def test_ad_hoc_step_not_in_plan_grows_total():
    t = _task_with_plan()
    t["steps"] = [{"id": "z", "purpose": "extra", "status": "succeeded"}]
    # plan has 3, plus one ad-hoc committed id -> total 4
    assert progress.total_count(t) == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_progress.py -v`
Expected: FAIL (`No module named 'waypoint.progress'`).

- [ ] **Step 3: Implement `waypoint/progress.py`**

```python
"""Roadmap math and progress rendering — one source of truth (§6).

Consumed by ``status``, ``steps``, ``list``, the commit/set-step beats, and
STATUS.md so the "step N of M" semantics are identical everywhere. A task
"has a plan" iff its permanent ``plan`` roadmap is non-empty; without one we
never speak of "step N" (meaningless), only a committed count.
"""

from __future__ import annotations


def has_plan(task: dict) -> bool:
    """True if a roadmap has been declared (``plan`` is non-empty)."""
    return bool(task.get("plan"))


def done_count(task: dict) -> int:
    """Number of committed (succeeded) steps."""
    return len(task.get("steps") or [])


def ordered_ids(task: dict) -> list:
    """Ordered union of step ids: plan ids, then committed/current ids not
    already in the plan (so ad-hoc steps still count toward the total)."""
    ids: list = []
    seen: set = set()

    def _add(sid):
        if sid is not None and sid not in seen:
            ids.append(sid)
            seen.add(sid)

    for p in task.get("plan") or []:
        _add(p.get("id"))
    for s in task.get("steps") or []:
        _add(s.get("id"))
    cur = task.get("current_step")
    if cur:
        _add(cur.get("id"))
    return ids


def total_count(task: dict) -> int:
    """Total distinct steps in the roadmap (incl. ad-hoc committed/current)."""
    return len(ordered_ids(task))


def position_of(task: dict, step_id: str) -> int:
    """1-based position of ``step_id`` in the ordered roadmap."""
    ids = ordered_ids(task)
    return ids.index(step_id) + 1 if step_id in ids else done_count(task) + 1


def remaining(task: dict) -> list:
    """Plan entries not yet committed and not the current step."""
    done_ids = {s.get("id") for s in task.get("steps") or []}
    cur = task.get("current_step")
    cur_id = cur.get("id") if cur else None
    return [p for p in (task.get("plan") or [])
            if p.get("id") not in done_ids and p.get("id") != cur_id]


def summary(task: dict) -> str:
    """One-line progress summary (the canonical wording used by ``status``)."""
    done = done_count(task)
    cur = task.get("current_step")
    if not has_plan(task):
        line = f"{done} step{'s' if done != 1 else ''} committed (no plan declared)"
        if cur:
            line += f"; in step '{cur.get('id')}' — {cur.get('purpose', '')}"
        return line
    total = total_count(task)
    focus = cur or (remaining(task)[0] if remaining(task) else None)
    if focus is None:
        return f"{done} of {total} done ✓"
    pos = position_of(task, focus.get("id"))
    return (f"{done} of {total} done — curr: step {pos} "
            f"({focus.get('id')} — {focus.get('purpose', '')})")


def token(task: dict) -> str:
    """Compact status token for one-line listings (``list``)."""
    cur = task.get("current_step")
    if has_plan(task):
        total = total_count(task)
        if cur:
            return f"step {position_of(task, cur.get('id'))}/{total}"
        if remaining(task):
            return f"{done_count(task)}/{total} done"
        return f"{total}/{total} done ✓"
    if cur:
        return f"step {cur.get('id')}"
    return "between steps"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/progress.py tests/test_progress.py
git commit -m "feat(progress): roadmap math + progress-line/token helpers"
```

---

## Task 4: STATUS.md renders from the plan + shows the progress line

**Files:**
- Modify: `waypoint/statusmd.py`
- Test: `tests/test_statusmd.py`

- [ ] **Step 1: Update the failing tests**

Replace the body of `tests/test_statusmd.py` with:

```python
"""Tests for STATUS.md rendering."""

from waypoint import model, statusmd


def test_render_shows_roadmap_and_progress():
    t = model.new_task("t1", "build the thing")
    t["plan"] = [{"id": "a", "purpose": "first"},
                 {"id": "b", "purpose": "second"},
                 {"id": "c", "purpose": "third"}]
    t["steps"].append({"id": "a", "purpose": "first", "status": "succeeded"})
    t["current_step"] = {"id": "b", "purpose": "second", "status": "in_progress"}
    out = statusmd.render(t)
    assert "build the thing" in out
    assert "✓ a  first" in out
    assert "▶ b  second" in out
    assert "☐ c  third" in out
    assert "1 of 3 done" in out          # progress line
    assert "Next on resume" in out


def test_render_between_steps():
    t = model.new_task("t1", "g")
    t["steps"].append({"id": "a", "purpose": "first", "status": "succeeded"})
    out = statusmd.render(t)
    assert "no current step" in out.lower() or "declare the next" in out.lower()
    assert "no plan declared" in out      # no-plan progress wording
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_statusmd.py -v`
Expected: FAIL (progress line absent; `☐ c` now sourced from plan, not `pending`).

- [ ] **Step 3: Update `waypoint/statusmd.py`**

Add `from . import progress` next to the existing `from . import model`.

Insert the progress line into the header block — change the `lines = [...]`
list so the blank line before the ```` ``` ```` fence is replaced by the
progress summary:

```python
    lines = [
        f"# Task: {tid}   ({status}, last touched {updated})",
        "",
        f"**Goal:** {task.get('goal', '')}",
        "",
        f"**Progress:** {progress.summary(task)}",
        "",
        "```",
    ]
```

Replace the pending loop:

```python
    for step in task.get("pending", []):
        lines.append(f"☐ {step.get('id', '?')}  {step.get('purpose', '')}")
```

with the derived-remaining loop:

```python
    for step in progress.remaining(task):
        lines.append(f"☐ {step.get('id', '?')}  {step.get('purpose', '')}")
```

Replace the "Next on resume" `elif` that reads `task.get("pending")`:

```python
    elif task.get("pending"):
        nxt = task["pending"][0]
```

with:

```python
    elif progress.remaining(task):
        nxt = progress.remaining(task)[0]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_statusmd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/statusmd.py tests/test_statusmd.py
git commit -m "feat(statusmd): render roadmap from plan + add progress line"
```

---

## Task 5: CLI `plan` command (+ stop `set-step` mutating the roadmap)

**Files:**
- Modify: `waypoint/cli.py` (add `cmd_plan`; edit `cmd_set_step`; register subparser)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_plan_appends_and_rejects_duplicates(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    assert cli.main(["plan", "--step", "a", "--purpose", "first",
                     "--id", "t1", "--root", root]) == 0
    assert cli.main(["plan", "--step", "b", "--purpose", "second",
                     "--id", "t1", "--root", root]) == 0
    t = store.load(root, "t1")
    assert [p["id"] for p in t["plan"]] == ["a", "b"]
    # Duplicate id is refused.
    assert cli.main(["plan", "--step", "a", "--purpose", "dup",
                     "--id", "t1", "--root", root]) == 1


def test_set_step_does_not_shrink_plan(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    t = store.load(root, "t1")
    assert [p["id"] for p in t["plan"]] == ["a"]   # roadmap intact
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -k "plan or shrink" -v`
Expected: FAIL (`invalid choice: 'plan'`).

- [ ] **Step 3: Implement the `plan` command**

In `waypoint/cli.py`, remove the now-obsolete pending-mutation line from
`cmd_set_step` (the roadmap is permanent now):

```python
    # Drop the matching pending entry, if any.
    task["pending"] = [s for s in task.get("pending", [])
                       if s.get("id") != args.step]
```

Delete those two lines entirely.

Add the command function (place it just before `cmd_current`, which a later
task removes):

```python
def cmd_plan(root: str, args) -> int:
    task_id, task = _resolve(root, args.id)
    plan = task.setdefault("plan", [])
    if any(p.get("id") == args.step for p in plan):
        print(f"waypoint: step {args.step!r} is already in the plan",
              file=sys.stderr)
        return 1
    plan.append({"id": args.step, "purpose": args.purpose})
    store.save(root, task)
    if not getattr(args, "quiet", False):
        from . import progress
        print(f"planned step {args.step!r} — {progress.summary(task)}")
    else:
        print(f"planned {args.step}")
    return 0
```

Register the subparser in `build_parser` (after the `commit` parser block):

```python
    s = sub.add_parser("plan", parents=[common]); s.set_defaults(fn=cmd_plan)
    s.add_argument("--step", required=True)
    s.add_argument("--purpose", required=True)
    s.add_argument("--id")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -k "plan or shrink" -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'plan' command; set-step no longer mutates roadmap"
```

---

## Task 6: CLI `steps` command

**Files:**
- Modify: `waypoint/cli.py` (add `cmd_steps`; register subparser)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_steps_lists_markers_and_counter(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["plan", "--step", "b", "--purpose", "second", "--id", "t1",
              "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "did a", "--id", "t1", "--root", root])
    capsys.readouterr()  # clear
    assert cli.main(["steps", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "1 of 2 done" in out
    assert "✓ a" in out
    assert "☐ b" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cli.py -k steps_lists -v`
Expected: FAIL (`invalid choice: 'steps'`).

- [ ] **Step 3: Implement the `steps` command**

Add to `waypoint/cli.py`:

```python
def cmd_steps(root: str, args) -> int:
    from . import progress
    task_id, task = _resolve(root, args.id)
    if progress.has_plan(task):
        head = (f"Steps for {task_id}   "
                f"({progress.done_count(task)} of {progress.total_count(task)} done)")
    else:
        head = (f"Steps for {task_id}   "
                f"({progress.done_count(task)} committed, no plan declared)")
    print(head)
    cur = task.get("current_step")
    cur_id = cur.get("id") if cur else None
    done_ids = {s.get("id") for s in task.get("steps", [])}
    for sid in progress.ordered_ids(task):
        if sid in done_ids:
            mark, purpose = "✓", _purpose_for(task, sid)
        elif sid == cur_id:
            mark, purpose = "▶", cur.get("purpose", "")
        else:
            mark, purpose = "☐", _purpose_for(task, sid)
        print(f"  {mark} {sid}  {purpose}")
    return 0


def _purpose_for(task: dict, step_id: str) -> str:
    """Best-effort purpose text for a step id (committed step or plan entry)."""
    for s in task.get("steps", []):
        if s.get("id") == step_id:
            return s.get("purpose", "")
    for p in task.get("plan", []):
        if p.get("id") == step_id:
            return p.get("purpose", "")
    return ""
```

Register the subparser — add `("steps", cmd_steps)` to the existing tuple loop
that builds the simple `--id`-only parsers (keep `current` for now; Task 8
removes it):

```python
    for name, fn in (("current", cmd_current), ("resume", cmd_resume),
                     ("check", cmd_check), ("done", cmd_done),
                     ("abandon", cmd_abandon), ("steps", cmd_steps)):
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_cli.py -k steps_lists -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'steps' command listing roadmap with markers"
```

---

## Task 7: CLI `where` command

**Files:**
- Modify: `waypoint/cli.py` (add `cmd_where`; register subparser)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_where_prints_state_and_task_dirs(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    capsys.readouterr()
    assert cli.main(["where", "--id", "t1", "--root", root]) == 0
    out = capsys.readouterr().out
    assert store.waypoint_dir(root) in out
    assert store.task_dir(root, "t1") in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cli.py -k where_prints -v`
Expected: FAIL (`invalid choice: 'where'`).

- [ ] **Step 3: Implement the `where` command**

Add to `waypoint/cli.py`:

```python
def cmd_where(root: str, args) -> int:
    print(f"state dir:  {store.waypoint_dir(root)}")
    # Show the resolved task dir(s): the named/inferred one, else all active.
    if args.id:
        targets = [args.id]
    else:
        active = store.active_tasks(root)
        targets = [tid for tid, _ in active]
    for tid in targets:
        td = store.task_dir(root, tid)
        print(f"task dir:   {td}")
        print(f"  {store.STATE_FILE}, {store.STATUS_FILE}")
    if not targets:
        print("(no active task)")
    return 0
```

Register the subparser (add `("where", cmd_where)` to the same simple
`--id`-only tuple loop; `current` still present until Task 8):

```python
    for name, fn in (("current", cmd_current), ("resume", cmd_resume),
                     ("check", cmd_check), ("done", cmd_done),
                     ("abandon", cmd_abandon), ("steps", cmd_steps),
                     ("where", cmd_where)):
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_cli.py -k where_prints -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'where' command showing state/task dirs"
```

---

## Task 8: Remove `current`; ensure `status` shows the progress line

**Files:**
- Modify: `waypoint/cli.py` (delete `cmd_current`; drop from parser; status note)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_current_command_is_removed(root):
    import pytest as _pytest
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    with _pytest.raises(SystemExit):   # argparse: invalid choice
        cli.main(["current", "--id", "t1", "--root", root])


def test_status_shows_progress_line(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    assert cli.main(["status", "--id", "t1", "--root", root]) == 0
    assert "0 of 1 done" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -k "current_command or status_shows" -v`
Expected: `test_current_command_is_removed` FAILS (currently a valid command).

- [ ] **Step 3: Remove `current`**

In `waypoint/cli.py`:

1. Delete the entire `cmd_current` function:

```python
def cmd_current(root: str, args) -> int:
    _, task = _resolve(root, args.id)
    print(json.dumps(task.get("current_step"), indent=2, ensure_ascii=False))
    return 0
```

2. Remove `("current", cmd_current)` from the simple `--id` parser tuple loop,
   leaving:
   `("resume", cmd_resume), ("check", cmd_check), ("done", cmd_done),
   ("abandon", cmd_abandon), ("steps", cmd_steps), ("where", cmd_where)`.
   (Task 11 then pulls `check` out into its own parser for help text.)

`status` already renders the progress line via `statusmd.render` (Task 4), so
no change to `cmd_status` is required — the second test verifies this.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -k "current_command or status_shows" -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): remove 'current' (folded into status/steps)"
```

---

## Task 9: `list` — folder header + progress token

**Files:**
- Modify: `waypoint/cli.py` (`cmd_list`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_list_shows_folder_header_and_progress(root, capsys):
    import os
    cli.main(["start", "--goal", "build it", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    assert cli.main(["list", "--root", root]) == 0
    out = capsys.readouterr().out
    assert os.path.basename(root) in out      # folder name header
    assert root in out                          # abs path header
    assert "t1" in out and "0/1 done" in out    # task line + progress token
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cli.py -k list_shows_folder -v`
Expected: FAIL (no header; old token format).

- [ ] **Step 3: Update `cmd_list`**

Replace `cmd_list` in `waypoint/cli.py` with:

```python
def cmd_list(root: str, args) -> int:
    from . import progress
    import os
    print(f"# {os.path.basename(root.rstrip('/')) or root}  {root}")
    active = store.active_tasks(root)
    if not active:
        print("(no active tasks)")
        return 0
    for tid, t in active:
        print(f"{tid}  [{progress.token(t)}]  {t.get('goal')}")
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_cli.py -k list_shows_folder -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): list shows folder header + progress token"
```

---

## Task 10: `-q/--quiet` global flag + informative beats

**Files:**
- Modify: `waypoint/cli.py` (`common` parser; `cmd_start`, `cmd_set_step`, `cmd_commit`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_commit_beat_shows_progress_by_default(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["plan", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    cli.main(["plan", "--step", "b", "--purpose", "second", "--id", "t1",
              "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "first", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    cli.main(["commit", "--summary", "did a", "--id", "t1", "--root", root])
    out = capsys.readouterr().out
    assert "1 of 2 done" in out          # progress beat
    assert "next" in out.lower() and "b" in out


def test_quiet_collapses_commit_output(root, capsys):
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "p", "--id", "t1",
              "--root", root])
    capsys.readouterr()
    cli.main(["commit", "--summary", "x", "--id", "t1", "-q", "--root", root])
    out = capsys.readouterr().out
    assert "of" not in out               # no progress beat
    assert "committed" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -k "commit_beat or quiet_collapses" -v`
Expected: FAIL (`-q` unrecognized; no progress beat in commit output).

- [ ] **Step 3: Implement `--quiet` and the beats**

In `build_parser`, add the flag to the shared `common` parser:

```python
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="project root (default: auto-detect)")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="collapse mutating-command output to one line")
```

Update `cmd_start`'s success output (replace the final `print(task_id)` /
`return 0`):

```python
    store.save(root, task)
    if args.quiet:
        print(task_id)
    else:
        print(f"started task {task_id}")
        print(f"  goal: {task.get('goal')}")
        print(f"  state: {store.task_dir(root, task_id)}")
        print("  next: declare steps with `waypoint plan`, then `waypoint set-step`")
    return 0
```

Update `cmd_set_step`'s success line (replace `print(f"started step {args.step!r}")`):

```python
    store.save(root, task)
    if args.quiet:
        print(f"started step {args.step}")
    else:
        from . import progress
        pos = progress.position_of(task, args.step)
        print(f"▶ started step {args.step!r} (step {pos}) — {args.purpose}")
    return 0
```

Update `cmd_commit`'s success output (replace the final `print(...)` call):

```python
    store.save(root, task)
    if args.quiet:
        print(f"committed step {cur.get('id')!r}"
              + (f" @ {step_commit}" if step_commit else ""))
        return 0
    from . import progress
    done, total = progress.done_count(task), progress.total_count(task)
    rem = progress.remaining(task)
    if progress.has_plan(task):
        beat = f"✓ committed step {cur.get('id')!r} — {done} of {total} done"
        beat += (f"; next: step {rem[0]['id']} ({rem[0]['purpose']})"
                 if rem else "; plan complete ✓")
    else:
        beat = (f"✓ committed step {cur.get('id')!r} — "
                f"{done} step{'s' if done != 1 else ''} committed (no plan)")
    if step_commit:
        beat += f"  @ {step_commit}"
    print(beat)
    return 0
```

(Remove the previous multi-line `print(f"committed step ...")` statement that
this replaces.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -k "commit_beat or quiet_collapses" -v`
Expected: PASS.

- [ ] **Step 5: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): informative-by-default beats + -q/--quiet flag"
```

---

## Task 11: Clearer `--id` error and `check` help/output

**Files:**
- Modify: `waypoint/cli.py` (`_resolve`, `cmd_check`, `check` help text)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_ambiguous_id_error_lists_candidates(root, capsys):
    cli.main(["start", "--goal", "g1", "--id", "t1", "--root", root])
    cli.main(["start", "--goal", "g2", "--id", "t2", "--root", root])
    # No --id with two active tasks -> exit 1 and both ids surfaced.
    rc = cli.main(["status", "--root", root])
    err = capsys.readouterr().err
    assert rc == 1
    assert "t1" in err and "t2" in err
    assert "--id" in err


def test_check_output_labels_artifacts(root, tmp_path, capsys):
    art = tmp_path / "out.txt"
    art.write_text("r")
    cli.main(["start", "--goal", "g", "--id", "t1", "--root", root])
    cli.main(["set-step", "--step", "a", "--purpose", "p", "--id", "t1",
              "--root", root])
    cli.main(["commit", "--summary", "s", "--artifact", str(art),
              "--id", "t1", "--root", root])
    capsys.readouterr()
    cli.main(["check", "--id", "t1", "--root", root])
    out = capsys.readouterr().out
    assert "INTACT" in out and str(art) in out
```

Note: `_resolve` currently raises `SystemExit` for ambiguity. `cmd_status`
short-circuits only the zero-active case, so two active tasks reach `_resolve`.
`main()` does not catch `SystemExit`, so update `_resolve` to print to stderr
and return via a caught exception. Implementation below converts the ambiguity
path to a `ValueError` (already caught by `main`, which returns 1).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -k "ambiguous_id or check_output" -v`
Expected: `test_ambiguous_id_error_lists_candidates` FAILS (message lacks the
listed ids / wrong exit path); `check` output is lowercase verdict only.

- [ ] **Step 3: Improve `_resolve` and `cmd_check`**

Replace `_resolve` in `waypoint/cli.py` with:

```python
def _resolve(root: str, task_id: Optional[str]) -> tuple:
    """Return ``(task_id, task)``; infer the single active task if id omitted.

    Raises:
        ValueError: If the id is missing and the active task is ambiguous
            (zero or multiple). ``main`` catches this and exits 1.
    """
    if task_id:
        return task_id, store.load(root, task_id)
    active = store.active_tasks(root)
    if len(active) == 1:
        return active[0]
    if not active:
        raise ValueError("no active task in this folder")
    ids = "\n  ".join(tid for tid, _ in active)
    raise ValueError(
        f"{len(active)} active tasks here — rerun with --id <one of>:\n  {ids}"
    )
```

Update `cmd_check` to label its output and document itself:

```python
def cmd_check(root: str, args) -> int:
    """Re-verify the last committed step's artifacts (drift detection)."""
    _, task = _resolve(root, args.id)
    results = _check(task)
    bad = [(p, v) for p, v in results if v != fingerprint.INTACT]
    if not results:
        print("no artifacts recorded on the last committed step")
        return 0
    print("last committed step's artifacts:")
    for path, verdict in results:
        print(f"  {verdict.upper():8} {path}")
    return 1 if bad else 0
```

In `build_parser`, give the `check` subparser help text. Since `check` is
registered in the shared tuple loop, pull it out into its own parser with a
description. Remove `("check", cmd_check)` from the tuple and add:

```python
    s = sub.add_parser(
        "check", parents=[common],
        help="re-verify the last committed step's artifacts (INTACT/MISSING/CHANGED)",
    )
    s.set_defaults(fn=cmd_check)
    s.add_argument("--id")
```

(Final simple tuple loop becomes:
`("resume", cmd_resume), ("done", cmd_done), ("abandon", cmd_abandon),
("steps", cmd_steps), ("where", cmd_where)`.)

Verify `fingerprint.INTACT`/`MISSING`/`CHANGED` are the lowercase strings
`"intact"/"missing"/"changed"` (they are — `verdict.upper()` yields the labels).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -k "ambiguous_id or check_output" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all PASS. If `test_resume_reports_and_done_archives` or others broke
on output wording, reconcile them with the new beats.

- [ ] **Step 6: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): list candidate ids on ambiguity; label check output"
```

---

## Task 12: Docs — README + module docstring

**Files:**
- Modify: `waypoint/cli.py` (module docstring usage block)
- Modify: `README.md`

- [ ] **Step 1: Update the `cli.py` module docstring**

In `waypoint/cli.py`, update the usage block at the top of the module to the
new surface (remove `current`; add `plan`, `steps`, `where`; note `-q`):

```python
    waypoint start    --goal G [--id ID] [--scope P ...] [--auto]
    waypoint plan     --step b --purpose P [--id TASK]
    waypoint set-step --step b --purpose P [--target T] [--expected E]
                      [--context C] [--input PATH ...] [--id TASK]
    waypoint commit   --summary S [--artifact PATH ...] [--git] [--id TASK]
    waypoint status   [--id TASK] [--json]
    waypoint steps    [--id TASK]
    waypoint resume   [--id TASK]
    waypoint check    [--id TASK]
    waypoint where    [--id TASK]
    waypoint done     [--id TASK]
    waypoint abandon  [--id TASK]
    waypoint list

Global: --root PATH, -q/--quiet. ``current`` was removed — use ``status``
(current step in context) or ``steps`` (full roadmap).
```

- [ ] **Step 2: Update `README.md`**

Find the command/usage section of `README.md` and update it to match: remove
any `current` references; add `plan` (declare roadmap), `steps` (list step
names + counter), `where` (storage location); note `list` is current-folder
only and that output is informative by default with `-q/--quiet` to silence.
Keep the existing worked example style; if a `/health` example references
`current`, switch it to `status`/`steps`.

Run to find references:
`grep -n "current\|pending\|waypoint list\|Usage" README.md`

- [ ] **Step 3: Verify docs match behavior**

Run: `waypoint --help` and each `waypoint <cmd> --help`; confirm the README
and docstring match the actual parser output.

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 4: Commit** (after Gemini review passes)

```bash
git add waypoint/cli.py README.md
git commit -m "docs: update command surface (plan/steps/where; remove current)"
```

---

## Task 13: Close the waypoint step + final verification

- [ ] **Step 1: Full suite + manual smoke**

```bash
pytest -q
waypoint status --id 2026-05-30-build-waypoint   # legacy task migrates to a plan
waypoint steps  --id 2026-05-30-build-waypoint
waypoint where  --id 2026-05-30-build-waypoint
waypoint list
```

Expected: tests green; the legacy build-waypoint task (7 committed a–g, the
open `cli-redesign` step as current, `h` pending) migrates on load to a 9-step
plan and its progress reads `7 of 9 done — curr: step 8 (cli-redesign — …)`.
Do not assert exact counts in code — the live task is mutable; just confirm a
`X of Y done` progress line and the `✓/▶/☐` markers render without error.

- [ ] **Step 2: Commit the waypoint checkpoint**

```bash
waypoint commit --summary "Redesign inspection CLI: plan/steps/where, progress counter, --quiet, clearer errors; remove current" --git
```

This closes the open `cli-redesign` step and records the work as a waypoint.

---

## Self-review notes (author)

- **Spec coverage:** list header (T9) · status/steps progress (T3,T4,T6,T8) ·
  `plan` (T5) · `where` (T7) · remove `current` (T8) · `--id` error (T11) ·
  `check` clarity (T11) · verbosity `--quiet` + beats (T10) · data model +
  migration (T1,T2) · docs (T12). All spec sections mapped.
- **Naming consistency:** `progress.summary`/`token`/`remaining`/`ordered_ids`/
  `position_of`/`done_count`/`total_count`/`has_plan` and `model.migrate`/
  `store.waypoint_dir` are used identically across tasks.
- **Phase 2** (reconciler/orchestrator) is explicitly out of scope — separate
  spec/plan per the design appendix.
