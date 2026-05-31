# Waypoint CLI Improvements — Design

**Date:** 2026-05-31
**Status:** Approved-pending-review

## Problem

The waypoint CLI's inspection commands are confusing in practice:

1. `list` looks like it spans projects, but actually only shows the current
   folder — and that folder currently holds two unrelated tasks (one misfiled
   from a different repo), making the scope ambiguous.
2. `current` dumps the in-progress step as raw JSON; its name suggests
   "current project," and its output is hard to read.
3. The difference between `check` and `status` is unexplained.
4. The `pass --id` error gives no hint about *which* ids to pass.
5. There is no way to see the planned roadmap as a progress counter
   ("step 3 of 5") or to list step names — and, critically, **no command
   ever populates the planned-steps list**, so a total like "of 5" can never
   appear today.
6. There is no command to show where waypoint stores its state.

## Scope

In scope: a redesign of the **inspection** command surface plus a small
`plan` command and a `where` command. Out of scope (explicitly deferred):
cross-project listing / a global registry. All commands operate on the
**current folder** only.

## Decisions (locked with user)

- **No `list-all` / no global registry.** Deferred. `list` stays
  current-folder-only, made clearer with a folder-name header.
- **Remove `current`.** Its information moves into `status` (current step in
  context) and a new `steps` command (full list by name).
- **Add `plan`** so a roadmap can be declared and totals become meaningful.
- **Add `steps`** to list step names with state markers.
- **Add `where`** to print the storage location.
- **Improve `check` help/output** so it explains itself; behavior unchanged.
- **Improve the `--id` error** to list the candidate ids.
- **Abandon** the misfiled `2026-05-31-add-accel-zone-industry-value-normalized`
  task (archived, never deleted). [done]

## Command surface (after)

Lifecycle (unchanged): `start`, `set-step`, `commit`, `resume`, `done`, `abandon`.

| Command | Behavior |
|---|---|
| `list` | Tasks in the current folder under a header line: `# <folder-name>  <abs-path>`. Each task: `<id>   <progress>   <goal>`. |
| `status [--id T] [--json]` | Roadmap + a **progress line** + current-step detail + next-on-resume. |
| `steps [--id T]` | Lists every step by name with markers and the counter. |
| `plan --step ID --purpose TEXT [--id T]` | Append a step to the declared roadmap (the permanent plan). |
| `where [--id T]` | Print the `.claude/waypoint` dir and the resolved task dir(s). |
| `check [--id T]` | Verify the last committed step's artifacts; clearer help + labeled output. |
| ~~`current`~~ | Removed. |

## Progress line semantics

Let `done = len(steps)`, `cur = current_step`, and
`plan = task["plan"]` (the permanent declared roadmap; see Data model).
`has_plan = len(plan) > 0`.

- **Plan declared, working/between steps:**
  `2 of 5 done — curr: step 3 (add status command)`
  - When a step is in progress, `curr` is that step.
  - When between steps, `curr` names the next not-yet-started planned step.
- **Plan declared, all committed:** `5 of 5 done ✓`
- **No plan declared (`has_plan` false):** never show "step N" — that is
  meaningless without a roadmap. Show `3 steps committed (no plan declared)`,
  plus the current step name if one is in progress.

`no plan` and `plan exhausted` are therefore distinct states and must be
distinguishable in the data — which is why the roadmap is persisted
permanently rather than consumed away.

## Output verbosity

Decision: **informative by default, `-q`/`--quiet` to silence.**

- By default, the mutating commands print a short progress beat so each
  checkpoint is a visible "still alive / here's where we are" signal:
  - `start`: echoes the task id, the goal, and where state is stored, plus a
    next-action hint (`declare steps with 'waypoint plan' or start one`).
  - `set-step`: `▶ started step 'c' (3rd) — add status command`.
  - `commit`: `✓ step 3/5 done — next: step 4 (run tests)` (uses the same
    progress semantics above; no-plan form when no roadmap).
- `-q`/`--quiet` (a global flag on the common parent parser) collapses each
  command to its current one-line/terse output.
- Read-only commands (`status`, `steps`, `list`, `where`, `check`) are
  unaffected by `--quiet`; they are already explicit requests for output.

This keeps the worried-user case reassured out of the box while letting
quiet-lovers opt out, and the progress beats double as the heartbeat the
Phase 2 reconciler watches (see appendix).

## Data model change

Replace the consumed-away `pending` list with a **permanent roadmap**:

- `task["plan"]`: an ordered list of `{ "id": str, "purpose": str }` — the
  full intended roadmap. It is **append-only via `plan`** and is never shrunk
  by `set-step`/`commit`. A committed step keeps its id in `plan`.
- "Remaining" / not-yet-started steps are **derived**: plan entries whose id
  is neither among committed `steps` ids nor the `current_step` id.
- `total = len(ordered_ids)` where `ordered_ids` = plan ids, followed by any
  committed/current ids not already in plan (handles ad-hoc steps gracefully).
- `has_plan = len(task["plan"]) > 0`.

`set-step` no longer mutates the roadmap (today it pops from `pending`); it
just opens `current_step`. `STATUS.md` rendering switches from `pending` to
derived-remaining.

### Migration (legacy tasks without a `plan` key)

On load, if `task` has no `plan` key:

- **If `pending` is non-empty** (forward intent exists): reconstruct the full
  roadmap as `plan = [{id,purpose} for each committed step] + ([current] if
  current) + pending`. This preserves the planned next step.
  - For `2026-05-30-build-waypoint`: plan becomes `a..g` (from steps) + `h`
    (from pending) = 8 entries → `7 of 8 done — curr: step 8 (h — Implement
    cronq)`.
- **If `pending` is empty:** `plan = []` (no plan). This preserves the
  no-plan-vs-plan-done distinction for legacy data.

Migration is applied in-memory on load; the new shape is persisted on the
next `save`. The legacy `pending` key is dropped going forward.

## `--id` error message

When a command cannot infer the task (zero or multiple active tasks), replace
`waypoint: no active task; pass --id` / `...; pass --id` with a message that
lists the candidates, e.g.:

```
waypoint: 2 active tasks here — rerun with --id <one of>:
  2026-05-30-build-waypoint
  2026-05-31-...
```

and for the empty case: `waypoint: no active task in this folder.`

## `steps` output (illustrative)

```
Steps for 2026-05-30-build-waypoint   (7 of 8 done)
  ✓ a  scaffold the CLI
  ✓ b  implement store
  ...
  ✓ g  <purpose>
  ☐ h  Implement cronq (separate project)
```

Markers: `✓` committed, `▶` current (in progress), `☐` planned/not started.

## `where` output (illustrative)

```
state dir:  /home/tong/src/tonychen15/claude-waypoint/.claude/waypoint
task dir:   /home/tong/.../.claude/waypoint/2026-05-30-build-waypoint
  waypoint.json, STATUS.md
```

## Error handling

- `plan` on a step id that already exists in the roadmap: reject with a clear
  message (no silent duplicates).
- All inspection commands tolerate a task with no plan (show the no-plan form).
- Migration never raises; a malformed legacy task degrades to "no plan."

## Testing

- Progress line: the four states (plan+working, plan+between, plan-done,
  no-plan), including the no-plan vs plan-done distinction.
- `plan` appends; duplicate id rejected; `set-step` no longer mutates roadmap.
- Migration: build-waypoint-shaped task (steps + non-empty pending) →
  reconstructed plan and `7 of 8`; steps-only-no-pending task → no plan.
- `steps` markers for committed/current/planned.
- `where` prints existing paths.
- `--id` error lists candidate ids for zero and multiple active tasks.
- `list` header shows folder name; removal of `current` (command gone).
- Update existing tests that reference `current`/`pending`.

## Docs

Update `README.md` and the module docstring in `cli.py` to reflect the new
command surface (remove `current`, add `plan`/`steps`/`where`, note `list` is
current-folder-only).

---

## Appendix — Phase 2 (DEFERRED): Reconciler / orchestrator

Captured here so it is not lost; **out of scope for this spec.** Gets its own
brainstorm → spec → plan cycle after Phase 1 ships. Aligns with the existing
roadmap's pending step `h` ("Implement cronq — separate project").

**Vision (user's words, normalized):** two visible tmux panes.

- **Pane A — reconciler:** runs waypoint with a *very simple* CLI surface
  (e.g. `waypoint watch` + the inspection commands). The calm, structured
  view of progress. Acts as a **watchdog** over pane B.
- **Pane B — worker:** a *regular, interactive* Claude Code session (NOT
  headless) doing the real planning and step execution, so it can still
  interact with the user. The worker drives waypoint checkpoints
  (`set-step`/`commit`) as it works — those checkpoints are the progress
  signal pane A reflects.

**Watchdog contract (the core value):** when pane B dies or stalls, pane A
**takes over: kills pane B and performs a *guaranteed* resume** — re-read
STATUS.md, re-check artifacts (`check`), and continue from the last committed
step. Native `claude --resume` is best-effort and may or may not succeed;
waypoint's checkpoint discipline is what makes resume *guaranteed*. This is
the reason the tool exists.

**Pane A ⇄ pane B handoff protocol (user, 2026-05-31):**

- **Plan ownership lives in pane A.** The reconciler holds the declared
  roadmap (the `plan` from Phase 1: `waypoint plan ...`).
- **Pane B starts in *plan mode*.** The spawned Claude Code worker launches in
  plan mode by default (does not edit until it has a plan), and **borrows the
  steps pane A already planned** — i.e. it loads the task's `plan` roadmap and
  works through it rather than re-planning from scratch.
- **Pane B writes checkpoints.** As it executes, pane B's CC calls
  `waypoint set-step`/`commit`, saving checkpoint state into the shared
  `.claude/waypoint/<task>/` folder.
- **The waypoint folder *is* the channel.** Pane A and pane B do not talk over
  a socket; they communicate **through the checkpoint files** in the waypoint
  folder. Pane A watches those files to know what pane B is doing, detect
  stalls, and drive the guaranteed-resume takeover. Single source of truth,
  crash-durable, inspectable by the human at any time.

This means Phase 1's on-disk state (`waypoint.json`/`STATUS.md`, the permanent
`plan`, fingerprinted artifacts) is also the Phase 2 IPC substrate — a reason
to keep that format clean and atomic (it already is, via tmp+rename writes).

**Research notes (feasibility, to be verified against live `claude --help`):**

- Headless spawn exists (`claude -p ... --output-format stream-json
  --session-id <uuid>`), but Phase 2 deliberately wants pane B **interactive**,
  not headless — so spawning is `tmux split-window` launching a normal
  `claude` session, while pane A monitors the shared waypoint state files
  (and/or a hook-written activity log) rather than parsing a headless stream.
- Lifecycle observability: worker-side hooks (`Stop`, `PostToolUse`,
  `SessionStart`, `Notification`) can append to a log pane A tails to detect
  stall/death.
- Guaranteed-resume building blocks already exist in Phase 1: STATUS.md,
  `check` (artifact fingerprints), `resume`, and the permanent `plan` roadmap.
- Safety surface to design carefully in Phase 2: autonomous permissions for
  any take-over relaunch, runaway/turn caps, worktree isolation, and how pane
  A decides "pane B is actually dead" vs merely thinking.
- Flags/SDK names from the Phase-2 research are **unverified** and must be
  checked against this machine before building.

**Explicitly deferred — not part of Phase 1.**
