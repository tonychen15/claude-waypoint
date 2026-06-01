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
        against the step's stated goal. Use the project's **own** test runner /
        interpreter (e.g. its venv), not a bare `python3`. If you cannot run
        the check or it is inconclusive, treat the step as a **failure**
        (retry/escalate) — never commit unverified work.
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
