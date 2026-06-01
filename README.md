# waypoint

[![CI](https://github.com/tonychen15/claude-waypoint/actions/workflows/ci.yml/badge.svg)](https://github.com/tonychen15/claude-waypoint/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

**Resume an interrupted multi-step task in Claude Code — after a crash, a token-limit stop, or a closed window — and continue forward as if you'd just taken a coffee break.**

`waypoint` is a generic, file-based mechanism for Claude Code that records the state of a tracked multi-step task as it progresses, so a fresh session can pick up exactly where the last one stopped — after a close, a crash, or a token-limit interruption.

It is **forward-recovery checkpoint-restart**, not a saga: completed steps are durable and good, and resume continues *forward* from the last good point (re-running only the interrupted step idempotently). See [the design doc](docs/design.md) for why that distinction matters.

## The idea in one picture

```
plan (plan mode) ──/waypoint start──▶ steps seeded as the roadmap
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

Two one-command setups, both idempotent (safe to re-run). Each installs the `waypoint` CLI **and** wires the hooks + skill into Claude Code. Task state always lives per-project in `.claude/waypoint/`.

### 1. Install & enable globally (every project)

One script installs the CLI, copies the hooks + skill into `~/.claude/`, and registers the hooks in `~/.claude/settings.json`:

```bash
git clone https://github.com/tonychen15/claude-waypoint.git
cd claude-waypoint
./scripts/install-global.sh
waypoint --version            # verify
```

Make sure `~/.local/bin` is on your PATH. The script tries a normal `pip install --user` first and only falls back to `--break-system-packages` on PEP 668 "externally-managed" systems (Debian/Ubuntu) — safe here, since the package has zero dependencies and writes only to `~/.local`. To undo, see [Uninstall](#uninstall).

### 2. Install & enable in a single project

Wire it into just one repo — the hooks + skill live in that project's `.claude/` (registered with `$CLAUDE_PROJECT_DIR`, so the wiring survives moving the project):

```bash
cd /path/to/claude-waypoint
./scripts/install-project.sh /path/to/your/project    # defaults to $PWD
```

This installs the CLI (user-global) and copies the hooks + skill into `<project>/.claude/`, registering them in `<project>/.claude/settings.json`.

> Prefer a fully isolated install (no `--break-system-packages`)? Create a venv, `pip install .` into it, symlink its `waypoint` onto your PATH, and re-point the hook commands at that venv's `python`. Contributors developing the tool can `pip install -e .` and run `python -m pytest` (the CI runs the same).

### Autonomous resume (optional)

Needs `at` (preferred) or cron. Start a task with `waypoint start --auto`; on a usage limit it reschedules itself to wake at the reset time (design §6A). Override the headless model with `WAYPOINT_CLAUDE_MODEL`.

### Uninstall

```bash
pip uninstall claude-waypoint          # or: pipx uninstall claude-waypoint
rm -rf ~/.claude/hooks/session_start.py ~/.claude/hooks/pre_tool_use.py \
       ~/.claude/hooks/pre_compact.py ~/.claude/skills/waypoint
# then remove the waypoint "hooks" block you added to ~/.claude/settings.json
```

Per-project cleanup (optional): delete `<project>/.claude/waypoint/` to drop saved task state. If you used `--auto`, remove the cron line (`crontab -e`, delete the `# waypoint-cron:` entry) and any pending one-shot jobs (`atq` / `atrm`). waypoint makes **no network requests** itself; the only outbound traffic is the optional `--auto` mode invoking `claude` (Anthropic API). It never uses `--dangerously-skip-permissions` — headless runs use a scoped tool allowlist.

## Usage

In practice **Claude drives these commands for you** via the `waypoint` skill — you just say "track this" and approve a plan. The commands below are what runs under the hood (and what you'd type to inspect or steer a task yourself).

> **Where does this run?** `waypoint` is an ordinary terminal command — *not* a subcommand of the `claude` CLI. Inside a session, Claude Code runs it for you through its Bash tool; you can also run it yourself in any shell (e.g. `waypoint status`) to inspect or recover a task. The hooks are executed automatically by the Claude Code harness (never by hand), and `waypoint-cron.sh` runs from cron. This is why the install puts `waypoint` on your PATH and makes `waypoint` importable by the hook interpreter — see [Installation](#installation).

| Command | What it does |
|---|---|
| `waypoint start --goal "<g>" [--scope <p>…] [--auto]` | Begin a tracked task; arms the tripwire. |
| `waypoint plan --step <id> --purpose "<p>"` | Declare a planned step (the roadmap), so progress reads "step N of M". |
| `waypoint set-step --step <id> --purpose "<p>" [--expected "<e>"] [--input <path>…]` | Declare the next step (required before editing files). |
| `waypoint commit --summary "<s>" [--artifact <path>…] [--git]` | Mark the current step succeeded; fingerprint artifacts (and optionally git-commit them). |
| `waypoint status` / `waypoint steps` / `waypoint list` | Show the roadmap + progress / each step by name with ✓ ▶ ☐ / active tasks **in this folder**. |
| `waypoint resume [--id <t>]` | Re-hydrate after an interruption; integrity-checks the last step's artifacts. |
| `waypoint check` | Re-verify the last step's artifacts — INTACT / MISSING / CHANGED (exit 1 if any drift). |
| `waypoint where [--id <t>]` | Print where state is stored (the `.claude/waypoint` dir and the task dir). |
| `waypoint watch [--id <t>] [--once] [--interval S]` | Read-only live monitor: progress + worker liveness (Phase 2 reconciler). |
| `waypoint done` / `waypoint abandon` | Close the task; move it to `archive/`. |

Every command accepts `--id <task>` to target a specific task; mutating commands (`start`, `plan`, `set-step`, `commit`) print an informative progress beat by default, and `-q`/`--quiet` collapses output to one line.

### Example: add a `/health` endpoint, resumably

```console
$ waypoint start --goal "Add a /health endpoint with a test" --scope src tests
started task 2026-05-31-add-a-health-endpoint-with-a-test
  goal: Add a /health endpoint with a test
  state: .../.claude/waypoint/2026-05-31-add-a-health-endpoint-with-a-test
  next: declare steps with `waypoint plan`, then `waypoint set-step`

# Declare the roadmap up front, so progress reads "step N of M":
$ waypoint plan --step api  --purpose "Add GET /health -> {status: ok}"
planned step 'api' — 0 of 1 done — curr: step 1 (api — Add GET /health -> {status: ok})
$ waypoint plan --step test --purpose "Test /health returns 200"
planned step 'test' — 0 of 2 done — curr: step 1 (api — Add GET /health -> {status: ok})

$ waypoint set-step --step api --purpose "Add GET /health -> {status: ok}" \
      --expected "src/app.py serves /health" --input src/app.py
▶ started step 'api' (step 1) — Add GET /health -> {status: ok}

# ...edit src/app.py... (the PreToolUse tripwire allows it — a step is declared)

$ waypoint commit --summary "added /health route" --artifact src/app.py --git
✓ committed step 'api' — 1 of 2 done; next: step test (Test /health returns 200)  @ 4f3a9c1

# Between steps, editing is blocked until you declare the next one:
$ # (try to edit tests/test_health.py now)
#   waypoint: no step in progress ... declare the next step before editing files

$ waypoint set-step --step test --purpose "Test /health returns 200" --input tests/test_health.py
▶ started step 'test' (step 2) — Test /health returns 200
# ...write tests/test_health.py, run them...
$ waypoint commit --summary "added passing test" --artifact tests/test_health.py --git
✓ committed step 'test' — 2 of 2 done; plan complete ✓  @ 9b2e7d4

$ waypoint done
task '2026-05-31-add-a-health-endpoint-with-a-test' completed; archived to .../archive/...
```

**Now suppose the session crashed right after the `api` commit.** A fresh session's SessionStart hook surfaces the task; you confirm, and:

```console
$ waypoint resume
# Resuming task '2026-05-31-add-a-health-endpoint-with-a-test': Add a /health endpoint with a test

Last committed step: api — Add GET /health -> {status: ok}
  result: added /health route
  [ok] src/app.py

No step in progress. Next planned: test — Test /health returns 200. Declare it with `waypoint set-step`.
```

`src/app.py` verified intact, the roadmap is clear, and you continue forward from exactly where you stopped — re-running only what wasn't committed.

## Status

Implemented and tested — 31 passing tests, cross-LLM (Gemini) reviewed. See [`docs/design.md`](docs/design.md) for the full design.

## Why "waypoint"

A waypoint is a marked point on a route that you pass through and **continue forward from** — which is exactly the recovery model (forward recovery from the last good point). The Claude Code ecosystem already saturates *checkpoint / handoff / session / resume / memory*; `waypoint` is a clean, accurate lane.

## License

[MIT](LICENSE) © 2026 tonychen15. Free to use, modify, and distribute — including commercially. The one condition is to **keep the copyright and license notice** (which links back to this repo) in copies and substantial portions.
