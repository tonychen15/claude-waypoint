# Waypoint Phase 3 — Intra-Claude Orchestration Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn waypoint into a native-first intra-Claude orchestrator — a `/waypoint` skill where the main agent decomposes a goal, gets one approval, then spawns subagents per step and commits verified checkpoints — backed by small CLI additions (`review`/`reviewer`/`max_retries`) and a docs recuration that demotes the Phase-2 subprocess path to "headless mode."

**Architecture:** The orchestration lives in `skills/waypoint/SKILL.md` (an imperative recipe the main agent follows), using the native Task tool for workers and the existing waypoint CLI for the durable checkpoint spine. The only new code is three task fields + `start` flags + migration; everything else is the skill recipe and docs.

**Tech Stack:** Python 3.12 stdlib + pytest (CLI bits), Markdown (the skill + docs). No new deps.

**Review protocol:** Per `CLAUDE.md`, every source commit passes the Gemini cross-LLM review (no CRITICAL/WARNING) before moving on — **run gemini foreground and wait**. **Commit only the files named per task** (never `.gitignore`/`.gstack/`). Branch `feat/phase3-orchestration-skill`. Tests: `.venv/bin/python -m pytest`.

**Scope discipline:** This phase adds NO new orchestration *code* (no process spawning, FSM, or heartbeat — those are Phase 2, now headless-only). The orchestration is the skill recipe. If a reviewer suggests building runtime orchestration code here, DECLINE.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `waypoint/model.py` | add `review`/`reviewer`/`max_retries` to the task (new_task/migrate/validate) | Modify |
| `waypoint/cli.py` | `start` flags `--review`/`--reviewer`/`--max-retries` | Modify |
| `skills/waypoint/SKILL.md` | the orchestration recipe (the heart of Phase 3) | **Rewrite** |
| `README.md` | front-door = start/status/resume + the skill; demote run/guard to "Advanced: headless" | Modify |
| `tests/test_model.py`, `tests/test_cli.py` | field + flag tests | Modify |

---

## Task 1: Model — `review` / `reviewer` / `max_retries` fields

**Files:**
- Modify: `waypoint/model.py`
- Test: `tests/test_model.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_model.py`:

```python
def test_new_task_review_defaults():
    t = model.new_task("t1", "g")
    assert t["review"] == "auto"
    assert t["reviewer"] == ""
    assert t["max_retries"] == 2


def test_new_task_review_overrides():
    t = model.new_task("t1", "g", review="manual", reviewer="gemini",
                       max_retries=3)
    assert t["review"] == "manual" and t["reviewer"] == "gemini"
    assert t["max_retries"] == 3


def test_migrate_adds_review_defaults():
    legacy = {"task_id": "t", "goal": "g", "status": "in_progress",
              "created_at": "2026-01-01T00:00:00+00:00", "steps": [],
              "current_step": None, "plan": []}
    model.migrate(legacy)
    assert legacy["review"] == "auto"
    assert legacy["reviewer"] == "" and legacy["max_retries"] == 2


def test_validate_rejects_bad_review_mode():
    t = model.new_task("t1", "g")
    t["review"] = "sometimes"
    assert any("review" in e for e in model.validate(t))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model.py -k "review or max_retries" -v`
Expected: FAIL (`KeyError: 'review'` / signature rejects kwargs).

- [ ] **Step 3: Implement in `waypoint/model.py`**

(a) After the grants constants, add:
```python
# Orchestration policy (Phase 3, intra-Claude skill).
REVIEW_AUTO = "auto"
REVIEW_MANUAL = "manual"
REVIEW_MODES = {REVIEW_AUTO, REVIEW_MANUAL}
```

(b) Extend `new_task`'s signature — add keyword params (after `auto`):
```python
def new_task(task_id, goal, *, scope=None, owner_session="", auto=False,
             review="auto", reviewer="", max_retries=2, clock=None):
```
and add to the returned dict (next to `"grants": {}`):
```python
        "review": review,
        "reviewer": reviewer,
        "max_retries": int(max_retries),
```

(c) In `validate`, after the grants check, add:
```python
    if task.get("review", REVIEW_AUTO) not in REVIEW_MODES:
        errors.append(f"invalid review mode: {task.get('review')!r}")
    if not isinstance(task.get("max_retries", 0), int):
        errors.append("max_retries must be an int")
```

(d) In `migrate`, before `return task`, add:
```python
    task.setdefault("review", REVIEW_AUTO)
    task.setdefault("reviewer", "")
    task.setdefault("max_retries", 2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_model.py -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review)

```bash
git add waypoint/model.py tests/test_model.py
git commit -m "feat(model): review/reviewer/max_retries orchestration policy fields"
```

---

## Task 2: CLI — `start` policy flags

**Files:**
- Modify: `waypoint/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_start_persists_review_policy(root):
    cli.main(["start", "--goal", "g", "--id", "t1", "--review", "manual",
              "--reviewer", "gemini", "--max-retries", "3", "--root", root])
    t = store.load(root, "t1")
    assert t["review"] == "manual" and t["reviewer"] == "gemini"
    assert t["max_retries"] == 3


def test_start_review_defaults(root):
    cli.main(["start", "--goal", "g", "--id", "t2", "--root", root])
    t = store.load(root, "t2")
    assert t["review"] == "auto" and t["max_retries"] == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "review_policy or review_defaults" -v`
Expected: FAIL (`--review` unrecognized).

- [ ] **Step 3: Implement in `waypoint/cli.py`**

In `cmd_start`, pass the new args into `new_task`. Change the `new_task(...)`
call to:
```python
    task = model.new_task(task_id, args.goal, scope=args.scope,
                          owner_session=args.session or "", auto=args.auto,
                          review=args.review, reviewer=args.reviewer,
                          max_retries=args.max_retries)
```

In `build_parser`, add to the `start` subparser block:
```python
    s.add_argument("--review", choices=["auto", "manual"], default="auto",
                   help="per-step verification: auto (orchestrator/reviewer) or manual (you)")
    s.add_argument("--reviewer", default="",
                   help="name/command of a configured reviewer (e.g. gemini); empty = none")
    s.add_argument("--max-retries", type=int, default=2,
                   help="per-step worker retries before escalating (default 2)")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "review_policy or review_defaults" -v` → PASS. Then full suite → ALL PASS.

- [ ] **Step 5: Commit** (after foreground Gemini review)

```bash
git add waypoint/cli.py tests/test_cli.py
git commit -m "feat(cli): start --review/--reviewer/--max-retries policy flags"
```

---

## Task 3: The `/waypoint` orchestration skill (the heart)

**Files:**
- Rewrite: `skills/waypoint/SKILL.md`
- (No pytest — verified structurally here, then dogfooded in Task 5.)

- [ ] **Step 1: Replace `skills/waypoint/SKILL.md` with EXACTLY this content:**

````markdown
---
name: waypoint
description: >
  Use to RUN a multi-step project resumably: you (the main agent) orchestrate,
  spawning subagents per step and committing durable checkpoints in
  .claude/waypoint/ so a fresh session continues forward from the last verified
  step. Invoke when the user says "waypoint start", "/waypoint", "build/ship X
  resumably", "track and run this", or kicks off long multi-step work.
---

# waypoint — intra-Claude orchestrator

**Principle: use Claude-native functions; waypoint only adds the durable,
resumable checkpoint spine.** You are pane A (the orchestrator). Subagents
(the Task tool) are pane B (the workers). The `waypoint` CLI is your durable
backend — committed steps survive a dead session; nothing else does.

The model is **forward-recovery checkpoint-restart**: a committed step is
durable and good; on resume you continue forward and re-run only the
uncommitted step. You never compensate/rollback.

## Front door (what the human types)

- `waypoint start "build X"` (or `/waypoint build X`) → you run this loop.
- `waypoint status` → they glance. `waypoint resume` → they continue later.
- They should not type `plan`/`set-step`/`commit`/`run`/`guard` — those are
  YOUR internal verbs (or the headless fallback).

## The loop

1. **Start.** Create durable state:
   ```
   waypoint start --goal "<one line>" [--review manual] [--reviewer <name>] [--max-retries K]
   ```
   Read the policy back with `waypoint status --json` (fields `review`,
   `reviewer`, `max_retries`).

2. **Decompose → approve (ONE gate).** Break the goal into an ordered list of
   small steps (decompose yourself, or dispatch one planning subagent for a
   large goal). **Show the human the step list and wait for a single
   approval** (accept edits). Then record it:
   ```
   waypoint plan --step <id> --purpose "<what>"      # one call per step
   ```

3. **Execute each pending step** (drive the loop off `waypoint steps`):
   1. `waypoint set-step --step <id> --purpose "<what>" [--expected "<done looks like>"]`
      — this arms the tripwire (undeclared edits are blocked), so always
      set-step before any work.
   2. **Dispatch a worker subagent** (Task tool) with: the step's goal, the
      context it needs, and the instruction *"do the work; DO NOT run any
      `waypoint` command — the orchestrator owns checkpoints."* Workers cannot
      spawn their own workers; if a step is too big, split it into more
      `waypoint plan` steps instead.
   3. **Verify** (highest applicable wins):
      - `review == manual` → show the diff + a summary and **wait for the human**.
      - else `reviewer` set (or the project declares one, e.g. a Gemini/codex
        cross-LLM protocol in CLAUDE.md) → run that reviewer on the changes.
      - else → **verify yourself**: run the project's tests / read the diff
        against the step's stated goal.
   4. **Pass → commit** (durable checkpoint):
      ```
      waypoint commit --summary "<what was produced>" [--artifact <path> ... --git]
      ```
      **Fail** (worker BLOCKED, or review rejects) → re-dispatch a fresh worker
      with the failure as context, up to `max_retries`. If it still fails,
      **stop and escalate to the human** (leave the task at the open step).

4. **Finish.** When `waypoint steps` shows all done:
   ```
   waypoint done
   ```
   Then **notify the human**: a one-line "✅ <goal> — done (N steps)".

## Resuming (guaranteed forward-recovery)

A new session's SessionStart hook surfaces the unfinished task. **Wait for the
human to say resume** (they run `waypoint resume`). Then:
1. Reload with `waypoint status` and `waypoint steps` (the plan + what's
   committed).
2. `waypoint check` — re-verify the last committed step's artifacts; if it
   reports `GONE`/`CHANGED`, **surface it to the human** before continuing.
3. Continue the loop from the **first uncommitted step**. The in-progress step
   (if any) was never committed → re-run it via observe-then-act (inspect
   current state, do only what remains).

## Rules that keep it safe

- **Commit only verified work.** A commit is a durable claim that the step
  succeeded — never commit an unverified or failing step.
- **One uncommitted step at a time.** Commit (or leave open + escalate) before
  the next.
- **Outbound third-party writes** (email, POST, `git push`, deploy) are a
  **human gate** unless explicitly granted — don't fire them unattended.
- **Keep checkpoints small:** summary + artifact *pointers*, not file contents.
- **You orchestrate; you don't do step work directly** — delegate to a worker
  so your own context stays clean for coordination and resume.

## Headless / unattended (advanced)

When there is **no live session** to host you (cron, rate-limit auto-resume,
CI), the subprocess path runs a worker without an orchestrator agent:
`waypoint run --id <task> --guard`. That is the fallback, not the default —
prefer this skill whenever a session is available.
````

- [ ] **Step 2: Structural verification**

Run:
```bash
grep -qE "intra-Claude orchestrator" skills/waypoint/SKILL.md && \
grep -q "Dispatch a worker subagent" skills/waypoint/SKILL.md && \
grep -q "wait for a single" skills/waypoint/SKILL.md && \
grep -q "max_retries" skills/waypoint/SKILL.md && \
grep -q "waypoint check" skills/waypoint/SKILL.md && \
grep -qi "headless" skills/waypoint/SKILL.md && echo "SKILL structure OK"
```
Expected: `SKILL structure OK`. Also confirm the full suite is unaffected:
`.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 3: Sync the globally-installed copy** (so the live skill matches):

```bash
cp skills/waypoint/SKILL.md ~/.claude/skills/waypoint/SKILL.md
```

- [ ] **Step 4: Commit**

```bash
git add skills/waypoint/SKILL.md
git commit -m "feat(skill): rewrite /waypoint as the intra-Claude orchestrator recipe"
```

(No Gemini review needed — it is a prose skill, not source code. Task 5
dogfoods it.)

---

## Task 4: Docs — front door + demote Phase 2 to headless

**Files:**
- Modify: `README.md`, `waypoint/cli.py` (module docstring)

- [ ] **Step 1: Restructure the README Usage section.** Add a "Run a project
  (the simple way)" block at the TOP of Usage and move the run/guard rows under
  an "Advanced: headless mode" note. Concretely, insert before the command
  table:

```markdown
### Run a project (the simple way)

Three commands and a notification:

```console
$ waypoint start "Build a CLI todo app with tests"   # you approve the plan once
… the agent decomposes the goal, runs each step via subagents, and
  checkpoints verified work …
$ waypoint status      # glance anytime
$ waypoint resume      # after a new session, continue from the last commit
✅ done — you're notified
```

Everything below is the machinery the agent drives for you (and a headless
fallback) — you don't normally type it.
```

And add, after the command table, a note:

```markdown
> **Advanced — headless mode.** With no live Claude session (cron, CI,
> rate-limit auto-resume), `waypoint run --id <t> --guard` spawns a worker
> subprocess supervised by a watchdog instead of the in-session agent. Prefer
> the skill above whenever a session is available.
```

- [ ] **Step 2: Update the `cli.py` module docstring** — add one line to the
  prose (after the `guard` sentence):
```python
The primary way to run a project is the ``/waypoint`` skill (the in-session
agent orchestrates subagents); ``run``/``guard`` are the headless fallback.
```

- [ ] **Step 3: Verify**

Run: `grep -q "Run a project (the simple way)" README.md && grep -qi "headless mode" README.md && echo OK`.
Run: `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 4: Commit** (after foreground Gemini review of the docstring change)

```bash
git add README.md waypoint/cli.py
git commit -m "docs: skill is the front door; demote run/guard to headless mode"
```

---

## Task 5: Dogfood acceptance run

**Files:** none (validation only). This is the real test of the skill.

- [ ] **Step 1: Run the skill end-to-end** on a small but multi-step throwaway
  task (a temp git repo). Drive it through the recipe yourself (you are the
  orchestrator): `waypoint start "<2-3 step goal>"` → decompose → **show plan,
  get approval** → for each step: `set-step` → dispatch a worker subagent →
  verify → `commit` → `done` → notify.

- [ ] **Step 2: Capture evidence** of each guarantee:
  - the plan-approval gate fired (you waited for a go);
  - each `commit` happened only after verification;
  - a deliberately-failed step retried then escalated (force one failure);
  - after `waypoint done`, the task is archived `completed` with all steps;
  - resume: in a fresh shell, `waypoint resume` reloads and would continue
    (start a 2nd task, commit step 1, then `resume` and confirm it points at
    step 2 via `waypoint steps`).

- [ ] **Step 3: Record the outcome** in a short note at the bottom of the spec
  (`docs/superpowers/specs/2026-06-01-waypoint-phase3-orchestration-skill-design.md`)
  under a new "## Acceptance (dogfood)" heading: what was run, the evidence,
  and any rough edges found. Commit:

```bash
git add docs/superpowers/specs/2026-06-01-waypoint-phase3-orchestration-skill-design.md
git commit -m "docs(phase3): record dogfood acceptance run"
```

---

## Self-review notes (author)

- **Spec coverage:** policy fields + migration (T1) · `start` flags (T2) · the
  orchestration recipe with plan-gate, subagent dispatch, pluggable
  verification precedence, retry-K-then-escalate, and forward-recovery resume
  (T3) · Phase-2 demotion + minimal front door (T4) · dogfood acceptance (T5).
  All five locked decisions and the native-first principle are realized in T3.
- **No new orchestration code** — per the spec, orchestration is the skill
  recipe; the only code is the three task fields + `start` flags + migration.
- **Naming consistency:** `model.REVIEW_AUTO/REVIEW_MANUAL/REVIEW_MODES`, the
  task fields `review`/`reviewer`/`max_retries`, and the `start`
  `--review/--reviewer/--max-retries` flags are used identically across T1–T3.
- **Skill is prose** → verified structurally (T3) + dogfooded (T5), not unit-
  tested; the CLI/model changes carry the pytest coverage.
