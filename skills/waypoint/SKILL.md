---
name: waypoint
description: >
  Use for any multi-step task you want to survive interruption (close, crash,
  token limit). Tracks the task as durable checkpoints in .claude/waypoint/ so
  a fresh session continues forward from the last committed step — like a
  coffee break, not a crash recovery. Invoke when the user says "track this",
  "make this resumable", "/waypoint", or starts long multi-step work.
---

# waypoint — resumable checkpoints

You drive the `waypoint` CLI. The model is **forward-recovery checkpoint-restart**
(NOT a saga — no compensation): committed steps are durable and good; on
resume you continue forward and re-run only the uncommitted step.

## The loop

1. **Start** (after the user approves a plan in plan mode):
   ```
   waypoint start --goal "<one line>" [--scope <dir> ...] [--auto]
   ```
   Seed the approved plan's steps as your mental roadmap.

2. **For each step** — declare it BEFORE editing files, then work, then commit:
   ```
   waypoint set-step --step <id> --purpose "<what>" \
       --target "<done looks like>" --expected "<acceptance>" \
       [--input <path> ...] [--context "<why/assumptions>"]
   # ... do the work (edits, commands) ...
   waypoint commit --summary "<what was produced>" \
       --artifact <path> ... [--git]
   ```
   The `PreToolUse` tripwire **blocks file edits when no step is declared** —
   so always `set-step` before mutating files, and `commit` when the step
   succeeds (it produced a result and you can proceed without the user).

3. **Finish:** `waypoint done`  (or `waypoint abandon`).

## Resuming

On a new session the SessionStart hook surfaces unfinished tasks. **Offer to
resume and wait for the user.** When they confirm:
```
waypoint resume [--id <task_id>]
```
Then **re-read the actual artifact files** (not just the summary), honor any
integrity warning (`GONE`/`CHANGED` → surface to the user, don't silently
build on it), and re-run the in-progress step via **observe-then-act**:
inspect current state and do only what remains.

## Rules that keep it safe

- **Success = operational forward progress** (you produced a result and didn't
  need the user). Don't over-think semantic correctness — that's the user's job.
- **Outbound third-party writes** (Telegram, email, POST, `git push`) are a
  **human gate**: record them in the step's `effects` ledger and never repeat a
  `completed` one; if `pending` (fired-but-unconfirmed) on resume, ask the user.
- **One uncommitted step at a time.** Commit the current step before starting
  the next.
- Keep checkpoints small: summary + artifact **pointers**, not file contents.

## Autonomous mode (`--auto`)

`--auto` installs `scripts/waypoint-cron.sh` via cron to resume headless and,
on a usage-limit, reschedule itself for the reset time. Headless runs stop at
every human gate rather than firing it unattended.
