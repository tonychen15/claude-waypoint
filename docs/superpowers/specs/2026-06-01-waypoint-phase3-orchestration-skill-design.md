# Waypoint Phase 3 — Intra-Claude Orchestration Skill

**Date:** 2026-06-01
**Status:** Draft for review
**Builds on:** Phase 1 (durable checkpoint CLI + hooks + resume, shipped on
`main`) and Phase 2 (subprocess orchestrator, on `feat/phase2-reconciler` —
demoted by this phase).

---

## Guiding principle

> **Use Claude-native functions as much as possible; waypoint only fills the
> gap Claude Code doesn't cover.**

The gap is **durable, verified, resumable multi-step execution that survives
crashes / session close / token limits / context compaction.** Claude Code
natively gives us subagents (the Task tool), tool permissions, hooks, and
within-session orchestration — we reuse all of it. Waypoint contributes the
on-disk checkpoint spine that makes a long agent run survivable and resumable
with *guaranteed forward progress from verified checkpoints*.

## Problem

Phase 2 spawns a headless `claude -p` worker subprocess and supervises it with
a Python watchdog (tmux, liveness hooks, an FSM, permission posture). It works
(validated end-to-end), but it reinvents a lot of what Claude Code already does
natively, and it needs a lot of machinery (launcher, guard, worker-command
construction, heartbeat hooks) to manage a process the harness could manage for
us. It also drifted from the north star: the *human* surface accreted many
commands (`plan`/`run`/`guard`/`watch`/…) instead of staying minimal.

## North star (unchanged from the user)

> The human starts one task, occasionally checks status or resumes, and gets a
> "done" notification. Three commands and a ping. Everything else is internal.

**Human surface:**
- `waypoint start "build X"` → kicks off the orchestration (via the skill).
- `waypoint status` → glance at progress.
- `waypoint resume` → after a new session, continue from the last commit.
- **Completion → the human is notified.**

`plan` / `set-step` / `commit` / `check` / `run` / `guard` / `watch` / `steps`
remain as **internal/advanced** verbs (used by the orchestrator and the
headless fallback), not the documented front door.

## Locked decisions (from brainstorming)

1. **Plan gate:** the orchestrator decomposes the goal into a plan, **shows it,
   waits for one human approval**, then runs autonomously.
2. **Per-step verification (pluggable, in precedence):** **manual** (human, if
   opted in) → **configured reviewer** (a reviewer subagent or an external one
   like the project's Gemini/codex cross-LLM review) → **orchestrator
   self-check** (run tests / read the diff against the step goal). Only verified
   work is committed.
3. **Step failure (worker blocks or review rejects):** retry with a fresh
   worker up to **K** times (default 2), then **pause + escalate** to the
   human. No infinite loop (mirrors the Phase-2 loop guard).
4. **Resume:** human-initiated (`waypoint resume`); the orchestrator reloads the
   plan + STATUS, re-checks artifacts (`check`), and continues from the last
   commit. Forward-recovery: an interrupted step is simply re-run.
5. **Phase 2 fate:** demoted, not deleted — kept for the corner case native
   Claude can't cover (**truly unattended / headless / cron / rate-limit
   auto-resume — no live session**). The skill is the primary path.

---

## Architecture

```
   You ──"waypoint start 'build X'"──▶  /waypoint SKILL
                                         │
                 ┌───────────────────────┴────────────────────────┐
                 │  PANE A — orchestrator = the MAIN Claude agent   │
                 │  • decompose goal -> plan; show; get 1 approval  │
                 │  • per step: set-step -> dispatch worker ->      │
                 │    verify (pluggable) -> commit                  │
                 │  • failure: retry K -> escalate                  │
                 │  • notify on done                                │
                 └───────┬───────────────────────────┬─────────────┘
                native Task tool                waypoint CLI (Bash)
                         │                             │
            PANE B — worker subagents      durable spine: .claude/waypoint/
            (do the actual work,           plan · committed steps · STATUS.md
             report results back)          · fingerprinted artifacts
```

- **Pane A (orchestrator)** = the main Claude agent, driven by the `/waypoint`
  skill. It never edits project files itself for a step — it delegates.
- **Pane B (workers)** = subagents via the native Task tool. Each gets one
  step's goal + context, does the work, reports back. Workers do **not** manage
  waypoint state (the orchestrator owns `set-step`/`commit`).
- **Durable spine** = the Phase-1 CLI + on-disk state, unchanged. This is the
  only thing that survives a dead session.

This is the pattern proven across this very session: a main agent decomposing a
plan and dispatching subagents per task, reviewing between them.

## Components

| Component | Responsibility | Build / Reuse / Keep |
|---|---|---|
| `skills/waypoint/SKILL.md` | the orchestration recipe (the heart of Phase 3) | **Build** (rewrite/extend the existing Phase-1 skill) |
| Task tool (subagents) | the workers | Reuse (native) |
| Hooks (SessionStart, PreToolUse tripwire, PreCompact) | surface on new session; enforce step-before-edit; snapshot before compaction | Keep (Phase 1) |
| waypoint CLI (`start`/`plan`/`set-step`/`commit`/`check`/`done`/`status`/`resume`) | durable state + queries | Keep (Phase 1); small additions below |
| `run`/`guard`/`launcher`/`worker` + worker hooks | headless/unattended fallback only | Keep, **demote** (Phase 2) |

**Small CLI additions (this phase):**
- `waypoint start … [--review auto|manual] [--reviewer <name-or-cmd>]
  [--max-retries K]` → persist `review`, `reviewer`, `max_retries` on the task
  so the orchestrator (and a resumed session) know the policy. Defaults:
  `review=auto`, `reviewer=` (none), `max_retries=2`.
- No new orchestration code in the CLI — orchestration lives in the skill.

## The orchestration loop (skill recipe)

The human's `waypoint start "build X"` (typed as intent / `/waypoint`) invokes
the skill; the skill runs the `waypoint start` **CLI** to create durable state,
then drives the loop. (The skill is the orchestrator; the CLI is its durable
backend — the human never types the inner verbs.)

1. **Decompose** the goal into an ordered plan (the orchestrator's own
   reasoning, or a one-shot planning subagent for a big goal). Record it with
   `waypoint plan --step … --purpose …` (one call per step).
2. **Show the plan** to the human and **wait for one approval** (or edits).
   Re-record if edited.
3. **For each pending step** (`waypoint steps` drives the loop):
   a. `waypoint set-step --step <id> --purpose <p>` (opens the step; arms the
      tripwire so undeclared edits are blocked).
   b. **Dispatch a worker subagent** (Task tool) with the step goal, the
      relevant context, and "do the work; do not touch waypoint state."
   c. **Verify** per the precedence in decision #2.
   d. **Pass →** `waypoint commit --summary <s> [--artifact <paths> --git]`
      (the step becomes a durable, fingerprinted checkpoint).
      **Fail →** retry from (b) with a fresh worker (carry the failure as
      context) up to `max_retries`; then **pause and escalate** to the human
      (leave the task at the open step for inspection).
4. **All steps committed →** `waypoint done` (archives the task) and **notify**
   the human (a concise "✅ <goal> — done" message; optional desktop ping).

The orchestrator may add steps mid-run (`waypoint plan`) if the work reveals
more is needed — the plan is not frozen at approval time.

## Verification policy (decision #2, detailed)

Resolve once per step, highest precedence first:
1. **Manual** — if `review=manual`: the orchestrator presents the step's diff +
   a summary and **waits for the human** to approve/reject before commit.
2. **Configured reviewer** — else if `reviewer` is set (or the project declares
   a reviewer, e.g. a Gemini/codex cross-LLM protocol in CLAUDE.md): run that
   reviewer on the step's changes; commit only on pass, treat a reject as a
   step failure (→ retry/escalate).
3. **Orchestrator self-check** — else: the orchestrator verifies directly (run
   the project's tests, read the diff against the step's stated goal); commit on
   pass.

"Configured reviewer" reuses what's already in the project — it does not
reinvent review. A reviewer subagent and an external CLI reviewer are both
valid; the skill picks whichever is declared.

## Resume (guaranteed forward-recovery)

A new session (crash / close / token limit / next day):
1. The **SessionStart hook** surfaces the unfinished task (Phase 1, already
   built): "Paused task X at step c — resume?".
2. The human types **`waypoint resume`**.
3. The orchestrator reloads the **plan + STATUS**, runs **`waypoint check`** to
   re-verify the last committed step's artifacts (surfaces drift), and
   **continues the loop from the first uncommitted step**.

Because only committed steps are durable and they are fingerprinted, resume is
*guaranteed* to move forward from a known-good point — the in-flight step (if
any) is simply re-run by a fresh worker. This is waypoint's core gap-fill:
Claude's native `--resume` is best-effort transcript replay; this is
checkpoint-anchored forward recovery.

## Phase 2 demotion

`run`/`guard`/`launcher`/`worker` + the worker hooks remain in the tree but:
- are **re-documented as the "headless / unattended" mode** — for cron,
  rate-limit auto-resume, CI, or any context with **no live Claude session** to
  host the orchestrator;
- drop out of the primary README/skill flow (a short "Advanced: headless mode"
  note instead).

No code is deleted; the surface is recurated so the skill is the front door.

## Data model additions (Phase 1 + these)

On the task (`waypoint.json`), set by `start`:
- `review`: `"auto"` (default) | `"manual"`.
- `reviewer`: optional string (reviewer name/command); empty = none.
- `max_retries`: int (default 2) — per-step failure bound.

Migration: default all three when absent (mirrors the Phase-1 `grants`/`plan`
migration pattern). No change to the step/plan structures.

## What we are NOT building (YAGNI / native-first)

- No new process-spawning, no FSM, no heartbeat, no liveness store **for the
  skill path** — subagents are managed by the harness; failures return
  synchronously to the orchestrator.
- No new permission machinery — subagents inherit the session's tools/perms.
- No replacement for the Task tool, hooks, or session management.

## Error handling / edge cases

- **Worker returns BLOCKED / NEEDS_CONTEXT:** the orchestrator supplies context
  and retries, or (after `max_retries`) escalates — same path as a failure.
- **Review rejects repeatedly:** counts toward `max_retries`; then escalate.
- **Session dies mid-step:** the open step was never committed → on resume it is
  re-run; no half-committed state (commit is the only durability boundary).
- **Artifact drift on resume** (`check` shows MISSING/CHANGED): surfaced to the
  human before continuing (Phase-1 §9 "go deep").
- **Goal too vague / plan rejected at the gate:** the human edits the plan or
  the goal before any work; no wasted execution.
- **Subagent nesting:** workers cannot spawn their own workers — the
  orchestrator decomposes big steps into more steps instead.

## Testing strategy

- **CLI additions** (`review`/`reviewer`/`max_retries` fields, `start` flags,
  migration) — unit-tested like the rest of Phase 1/2 (pytest).
- **The skill recipe** — validated by **dogfooding**: run `/waypoint` on a real
  moderate task and confirm (a) the plan-approval gate fires, (b) steps are
  committed only after verification, (c) a forced failure retries then
  escalates, (d) killing the session then `waypoint resume` continues from the
  last commit, (e) completion notifies. Skills are instructions, not unit-
  testable code; the acceptance test is an end-to-end run with evidence.

## Open risks

- **Skill adherence:** a skill is guidance the agent follows, not deterministic
  code — the recipe must be tight and checklist-shaped so the orchestrator
  reliably commits only verified work and resumes correctly. Mitigation: a
  short, imperative SKILL.md with an explicit per-step checklist.
- **Orchestrator context growth** over long runs → compaction. The durable
  state + PreCompact snapshot are the memory; resume reloads. Risk is the agent
  failing to reload faithfully — mitigated by the resume recipe re-reading
  STATUS + plan rather than relying on in-context memory.
- **Two paths to maintain** (skill + headless) — bounded by keeping the headless
  path explicitly secondary and sharing the same on-disk state.

## Acceptance (dogfood)

Ran the recipe end-to-end as the orchestrator on a throwaway 2-step task
(`/tmp/wp-dogfood.*`), dispatching real worker subagents per step.

**Evidence of each guarantee:**
- **Plan-approval gate:** the recipe stops to show the plan and wait for one
  approval (narrated in the run; it is step 2 of the loop).
- **Orchestrator → subagent → verify → commit loop:** two steps, each done by a
  real worker subagent (haiku), verified by the orchestrator, then
  `waypoint commit` — durable, git-anchored checkpoints (`@8b2fa35`, `@46b4831`).
- **Completion:** `waypoint done` archived the task `completed` (2/2 steps); the
  orchestrator emitted a one-line "✅ … done (2 steps)".
- **Resume (forward-recovery):** a second task with step 1 committed →
  `waypoint resume` reports "Next planned: two" and `waypoint steps` shows
  `✓ one / ☐ two` — a fresh session continues from the last commit.
- **Failure path:** a worker dispatched an impossible step returned a clean
  **BLOCKED** synchronously to the orchestrator (which would retry up to
  `max_retries`, then escalate).

**Rough edges found (fixed / noted):**
1. The orchestrator's self-verify must use the project's **own** test runner —
   a bare `python3` lacked `pytest`, the check errored, and the step was
   committed anyway (violating "commit only verified work"). **Fixed** by
   sharpening the SKILL verify rule: use the project's interpreter/venv, and an
   inconclusive verify counts as a failure (no commit).
2. (Headless path, separate) after `waypoint done` archives a task, a trailing
   PostToolUse hook can recreate an empty `runtime/heartbeat` for the archived
   id — harmless, to tidy later.

**Verdict:** the intra-Claude orchestration recipe works as designed; the core
loop and resume are sound.
