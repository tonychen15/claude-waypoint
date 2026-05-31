# waypoint

**Resume an interrupted multi-step task in Claude Code as if you'd just taken a coffee break — not recovered from a crash.**

`waypoint` is a generic, file-based mechanism for Claude Code that records the state of a tracked multi-step task as it progresses, so a fresh session can pick up exactly where the last one stopped — after a close, a crash, or a token-limit interruption.

It is **forward-recovery checkpoint-restart**, not a saga: completed steps are durable and good, and resume continues *forward* from the last good point (re-running only the interrupted step idempotently). See [the design doc](docs/design.md) for why that distinction matters.

## The idea in one picture

```
plan (plan mode) ──/waypoint start──▶ steps seeded as pending
   │ for each step:
   │   declare → do work → commit checkpoint (durable, fingerprinted) → advance
   ▼
interruption (close / crash / token limit)
   ▼
new session ──SessionStart hook──▶ "⏸ Paused task 'X' at step c — resume?"
   ▼ you confirm
re-hydrate from the last committed step's artifacts → continue forward (coffee break)
```

## Core guarantees

- **At most one uncommitted step at any instant** — enforced by a `PreToolUse` tripwire — so the last succeeded step is always durable *before* any new work, and a crash loses at most the in-flight step.
- **Resume integrity** — each step's result artifacts are fingerprinted (`git hash-object`); on resume, a changed/missing file is detected and surfaced rather than silently trusted.
- **Idempotent side effects** — outbound third-party writes (Telegram, email, POST, `git push`) use a write-ahead ledger so they are never double-fired on re-run.
- **No silent state mutation** — paused tasks persist byte-for-byte; the system reports staleness but never auto-archives by age. You decide.
- **Autonomous resume across a rate-limit break** (opt-in `--auto`) — a thin cron trigger relaunches the task headless, and on a usage-limit it reschedules itself to wake at the reset time. Headless runs stop at every human gate (outbound writes, ambiguous effects) rather than firing them unattended. Adapted from a proven `research.sh` orchestrator pattern.

## Status

Design approved. Implementation not started. See [`docs/design.md`](docs/design.md).

## Why "waypoint"

A waypoint is a marked point on a route that you pass through and **continue forward from** — which is exactly the recovery model (forward recovery from the last good point). The Claude Code ecosystem already saturates *checkpoint / handoff / session / resume / memory*; `waypoint` is a clean, accurate lane.
