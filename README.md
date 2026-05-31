# waypoint

**Resume an interrupted multi-step task in Claude Code — after a crash, a token-limit stop, or a closed window — and continue forward as if you'd just taken a coffee break.**

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

## Installation

### Requirements

- **Python ≥ 3.12** — standard library only, no third-party dependencies.
- **git** — used for `git hash-object` artifact fingerprints (falls back to SHA-256 if unavailable).
- *Optional:* `at` (or cron) for autonomous resume (`--auto`).

### Install the CLI

```bash
cd claude-waypoint
pip install --user .          # add --break-system-packages on PEP 668 distros (Debian/Ubuntu)
# or isolated:  pipx install .
waypoint --help               # verify
```

> The hooks `import` the `waypoint` package, so it must be importable by the `python3` that runs them. `pip install --user .` provides both the `waypoint` command and the import. If you use `pipx` or a venv instead, point the hook commands below at that environment's interpreter.

### Enable inside Claude Code

State always lives per-project in `.claude/waypoint/`; the *tooling* can be wired globally or per-project.

**Global (recommended — available in every project):**

```bash
mkdir -p ~/.claude/hooks ~/.claude/skills/waypoint
cp hooks/*.py               ~/.claude/hooks/
cp skills/waypoint/SKILL.md ~/.claude/skills/waypoint/
```

Merge into `~/.claude/settings.json` (absolute paths so they resolve everywhere; swap `python3` for your venv/pipx interpreter if needed):

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/session_start.py" }] }],
    "PreToolUse":   [{ "matcher": "Write|Edit|MultiEdit", "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/pre_tool_use.py" }] }],
    "PreCompact":   [{ "hooks": [{ "type": "command", "command": "python3 ~/.claude/hooks/pre_compact.py" }] }]
  }
}
```

**Single project:** copy `hooks/` and `skills/waypoint/` into that project's `.claude/`, then merge this repo's `.claude/settings.json` hook block (it already uses `$CLAUDE_PROJECT_DIR`).

### Develop / run the tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/python -m pytest          # 31 tests
```

### Autonomous resume (optional)

Needs `at` (preferred) or cron. Start a task with `waypoint start --auto`; on a usage limit it reschedules itself to wake at the reset time (design §6A). Override the headless model with `WAYPOINT_CLAUDE_MODEL`.

## Status

Implemented and tested — 31 passing tests, cross-LLM (Gemini) reviewed. See [`docs/design.md`](docs/design.md) for the full design.

## Why "waypoint"

A waypoint is a marked point on a route that you pass through and **continue forward from** — which is exactly the recovery model (forward recovery from the last good point). The Claude Code ecosystem already saturates *checkpoint / handoff / session / resume / memory*; `waypoint` is a clean, accurate lane.
