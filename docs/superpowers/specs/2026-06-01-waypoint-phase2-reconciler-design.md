# Waypoint Phase 2 — Reconciler / Orchestrator Design

**Date:** 2026-06-01
**Status:** Draft for review
**Builds on:** Phase 1 (CLI improvements, shipped on `main` @ `7dac059`) — the
permanent `plan` roadmap, `status`/`steps`/`where`, `resume`, `check`
(fingerprinted artifacts), and the atomic on-disk state under
`<project>/.claude/waypoint/<task>/`.

---

## North star (the product principle)

> The human's surface is **pane A**, and it is dead simple: **start** a big
> task → occasionally **glance at status** (or hit **resume**) on pane A → get
> a **"done" notification** from pane A. They should almost never touch
> pane B. All the durability/reliability/resumability machinery in A+B is
> hidden.

Everything below serves that: minimal human operation, pane A as the entire
control surface, pane B autonomous and near-zero-touch. Full autonomy is not
optional here — it *is* the product.

## Goal

A two-pane tmux orchestrator where **pane A** (reconciler/watchdog) supervises
**pane B** (an interactive Claude Code worker doing the real work), so that a
long, multi-step task runs to completion **durably, reliably, and resumably**
with near-zero human babysitting. When pane B dies or stalls, pane A
autonomously takes over and resumes from the last waypoint checkpoint.

## Scope

**In scope (one cohesive spec; build staged so the risky auto-kill lands
last):** the full low-touch autonomous system — spawn, worker bootstrap,
liveness, the autonomous watchdog (3 triggers), guaranteed resume, the
progress-gated loop guard, the permission/safety policy, the pane-A control
surface, and completion notification.

**Out of scope:** cronq (the separate generic task-queue project); cross-host
orchestration; non-tmux multiplexers (documented fallback only); GUI.

**Ships as Phase 2** — its own branch (`feat/phase2-reconciler`) and its own
push, kept separate from Phase 1.

## Locked decisions (from brainstorming)

1. **Takeover trigger:** fully autonomous — **death** (process exit) **+
   explicit stall signals** (worker `Notification` "waiting for input") **+
   heartbeat timeout** (no tool activity for N).
2. **Liveness signal:** a worker-side **`PostToolUse` hook touches a heartbeat
   file** on every tool call; "stalled" = no tool activity for N. (Not
   checkpoint-based — a legit step can run minutes between commits.)
3. **Resume mechanics:** **try `claude --resume <session-id>` first**, fall
   back to a deterministic **waypoint brief** if that errors/fails.
4. **Loop guard:** **progress-gated** — keep taking over while each takeover
   yields a *new committed step*; after **K consecutive no-progress
   takeovers**, stop and **escalate to the human** (notification + pane-A
   banner).
5. **Permission/safety policy:** outbound/remote ops require **up-front human
   authorization**; **local delete is forbidden** (move to `to-be-deleted/`).

---

## Architecture

```
 ┌─ tmux session: waypoint:<task> ──────────────────────────────────────┐
 │  PANE A — guard (the product surface)   │  PANE B — worker (the engine) │
 │  • renders status: N of M, current step,│  • claude, interactive, plan  │
 │    last-active, takeover count          │    mode; near-zero-touch      │
 │  • runs the watchdog state machine      │  • borrows the `plan` roadmap │
 │  • auto-takeover on death/stall         │  • set-step / commit          │
 │  • keys: r=resume now  p=pause  q=quit  │  • hooks emit heartbeat/events│
 │  • fires "DONE" notification            │  • escape hatch: human can chat│
 └───────────────┬─────────────────────────┴────────────┬──────────────────┘
                 │   both sides talk ONLY through         │
                 ▼   <project>/.claude/waypoint/<task>/   ▼
   durable (Phase 1):  waypoint.json · STATUS.md · plan · fingerprinted artifacts
   runtime  (Phase 2):  runtime/heartbeat  runtime/events.jsonl
                        runtime/worker.json (pid, session_id)  runtime/takeovers.jsonl
                        runtime/guard.json (FSM state)
```

**The waypoint folder is the only channel** (no sockets). Durable state is the
Phase-1 source of truth; a new **`runtime/`** subdir holds ephemeral liveness
signals. `runtime/` is gitignored and safe to delete between runs.

### Components (each independently testable)

| Component | Responsibility | Notes |
|---|---|---|
| `waypoint run --id <task>` | The human's start command. Pre-flight (task exists + has a `plan`), the **authorization gate**, lay out the tmux two-pane session, start guard (A) and worker (B). | Refuses if no tmux and no `--no-tmux` fallback. |
| worker bootstrap `waypoint worker --id <task>` | Pane B. Build the **seed prompt**, install the worker-side **session hooks**, exec `claude` with the **scoped permission posture**. On a takeover relaunch: try `--resume <session_id>`, else seed with the **brief**. | Invoked by `run`/guard, not usually by hand. |
| worker hooks | `PostToolUse`→touch `runtime/heartbeat`; `Notification`→append event (`waiting`/`idle`); `Stop`→`turn_done`; `SessionStart`→adopt task. Also a `PreToolUse` deny-guard for destructive/ungranted-remote ops. | Scoped to the worker session; write only into `runtime/`. |
| guard `waypoint guard --id <task>` | Pane A. Render the live display **and** run the watchdog loop: detect, decide, take over, enforce the loop guard, escalate, notify on completion. | Pure decision logic separated from tmux/process side-effects for unit tests. |
| watch `waypoint watch --id <task>` | Read-only live display (the render half of guard), runnable from any terminal. No control. | Reuses guard's renderer. |
| resume-brief `waypoint brief --id <task>` | Emit the deterministic resume brief: goal, STATUS.md roadmap, `check` result, next planned step, "continue from here." | Human-inspectable; used by worker on fallback. |

---

## Liveness & the watchdog state machine

**Liveness inputs the guard reads each tick (~every few seconds):**
- **Process:** is `runtime/worker.json.pid` still alive? (death detection)
- **Heartbeat:** age of `runtime/heartbeat` mtime (tool-activity recency).
- **Events:** tail `runtime/events.jsonl` for `waiting`/`idle`/`turn_done`/`needs-auth`.
- **Progress:** committed-step count from `waypoint.json` (for the loop guard).

**States:** `WATCHING → TAKING_OVER → RELAUNCHING → WATCHING`, plus terminal
`HALTED` (escalated) and `DONE` (task complete).

**Transitions out of `WATCHING` (a takeover is triggered when):**
1. **Death:** worker pid is gone and task is not `completed`.
2. **Waiting-timeout:** last event is `waiting`/`idle` (worker asked the human
   something) and **no human answered within `T_wait`** (default 5 min). The
   relaunch directive tells the worker to *make a reasonable call and proceed*.
3. **Heartbeat-timeout:** `runtime/heartbeat` age > `T_idle` (default 10 min)
   while the pid is alive (silent hang).

**The interactive↔autonomous tension is resolved by trigger #2:** a present
human answers pane B directly (heartbeat resumes, no takeover); an absent human
means the guard proceeds after `T_wait`. This is what keeps the experience
low-touch without ever truly blocking.

`T_wait` and `T_idle` are configurable (flags + task config); defaults chosen
conservative to avoid killing a worker mid-think.

---

## Takeover & guaranteed resume

On `TAKING_OVER`:
1. **Record** the attempt in `runtime/takeovers.jsonl`: `{ts, reason,
   committed_before}`.
2. **Kill** pane B's worker (term the pid / `tmux respawn-pane`), confirm dead.
3. **Re-check artifacts** of the last committed step (`check`); if any are
   `MISSING`/`CHANGED`, include that in the brief so the worker reconciles
   reality before proceeding (Phase-1 §9 "go deep").
4. **Relaunch** the worker in pane B:
   - **Try** `claude --resume <session_id>` (recover the prior transcript).
   - **Fall back** to a fresh `claude` seeded with `waypoint brief` (goal +
     roadmap + check result + next step). The brief is **authoritative** —
     this is what makes resume *guaranteed* rather than best-effort.
5. Back to `WATCHING`, with `committed_before` captured as the progress baseline.

## Loop guard (progress-gated)

- After each relaunch, the guard watches for a **new committed step**.
- If a new step commits → the takeover "made progress"; the consecutive
  no-progress counter resets.
- If **K consecutive takeovers** (default `K=2`) produce **no new committed
  step** → transition to **`HALTED`**: stop relaunching, fire a **human
  escalation** (PushNotification/desktop + a persistent pane-A banner with the
  last error and the `brief`), and leave pane B's last state intact for
  inspection. Never an infinite kill→relaunch loop.

## Completion

- The worker calls `waypoint done` when the roadmap is finished (or the guard
  detects `status == completed` / all plan steps committed with nothing
  remaining).
- Guard → `DONE`: fire the **completion notification** (PushNotification +
  desktop), show a final summary in pane A, and stop the watchdog. Pane B may
  exit or idle.

---

## Permission & safety policy

The worker runs autonomously, so it **cannot block mid-run on a permission
prompt** (that would be indistinguishable from a stall). Therefore **all
outbound/destructive permissions are resolved up front**, and anything not
granted is denied — never silently performed.

### Up-front authorization gate (`waypoint run`)
Before launching, `run` presents a one-time gate (pane A or B) recording grants
into `task["grants"]`:
- ☐ **git push** to remote
- ☐ **remote write / copy** (local → remote)
- **remote delete** — default **blocked** (grantable only by explicit opt-in)

The worker's tool allow/deny posture is **built from these grants**.

### Hard rules (regardless of grants)
- **Local delete is never allowed.** The worker **moves to `to-be-deleted/`**
  (repo convention; matches the global "no direct delete" rule) instead of
  deleting. Enforced by a `PreToolUse` deny-guard on `rm`, `git rm`, `mv … /dev/null`,
  truncating redirects over existing files, etc.
- **No `--dangerously-skip-permissions`.** The worker uses an
  **allowlist-primary posture (deny by default):** only explicitly allowed
  tools/commands run — local read/edit/build/test + **local** git commit, plus
  whatever the grants enable. Everything else, including any unlisted remote or
  destructive command, is denied automatically, so the policy does **not**
  depend on enumerating every dangerous command. The `PreToolUse` deny-guard is
  defense-in-depth (the local-delete → `to-be-deleted/` rule, and a `needs-auth`
  signal for ungranted-but-recognized remote ops).

### Mid-run, ungranted outbound op
If the worker reaches an op needing an ungranted grant, the `PreToolUse` guard
**denies it and writes a `needs-auth` event** to `runtime/events.jsonl`. The
guard surfaces it in pane A so the human can **grant it from pane A** (updates
`task["grants"]`, worker proceeds) — or the watchdog escalates. The worker
never blocks-waits and never performs the op unilaterally.

---

## Data model additions (Phase 1 + these)

Durable (`waypoint.json`):
- `grants`: `{push: bool, remote_write: bool, remote_delete: bool}` — set by
  the authorization gate; consulted by the worker posture and `PreToolUse`.

Runtime (`runtime/`, gitignored, ephemeral):
- `heartbeat` — empty file; **mtime = last tool activity**.
- `events.jsonl` — `{ts, kind, ...}` for `waiting`/`idle`/`turn_done`/`needs-auth`.
- `worker.json` — `{pid, session_id, started_at}` for the current pane-B worker.
- `takeovers.jsonl` — ledger `{ts, reason, committed_before}` (loop-guard input + audit).
- `guard.json` — the FSM state `{state, no_progress_count, baseline_committed}`
  for crash-safe guard restart.

## CLI surface (new)

```
waypoint run    --id <task> [--no-tmux] [--idle-timeout S] [--wait-timeout S] [--max-noprogress K]
waypoint guard  --id <task>            # pane A: watchdog + display (internal to run)
waypoint watch  --id <task>            # read-only live display, runnable anywhere
waypoint worker --id <task> [--resume <session_id>]   # pane B bootstrap (internal)
waypoint brief  --id <task>            # print the deterministic resume brief
```
Pane-A keys: **r** = resume now (manual takeover), **p** = pause/resume
autonomy, **q** = quit guard (stop the watchdog; pane B untouched).

## Unverified external dependencies (MUST verify before building)

The Phase-1 research flags are **unconfirmed**. Before implementation, verify
against the installed `claude --help` and a scratch run:
- `claude --resume <session-id>` / `--session-id` — existence, exact behavior,
  exit codes on a missing/corrupt session.
- Headless vs interactive launch flags; `--permission-mode` values;
  `--allowedTools`/`--disallowedTools` syntax; whether per-session hooks can be
  injected for the worker (settings path / env).
- `tmux` availability and the minimal command set (`new-session`,
  `split-window`, `respawn-pane`, `capture-pane`, `send-keys`).
If a capability is missing, the spec section that depends on it must be
re-designed (e.g., heartbeat-only if `--resume` is unreliable → brief-only).

## Error handling / failure modes

- **No tmux:** `run` errors with guidance, or `--no-tmux` runs guard headless
  (worker as a background process, display as periodic prints). Documented.
- **Guard crash:** `guard.json` lets a restarted guard recover its FSM state;
  `runtime/` is rebuildable from durable state + a fresh worker.
- **Worker won't die:** escalate after a bounded kill retry; never leave two
  workers racing on the same task.
- **Corrupt `runtime/`:** treated as "unknown liveness" → conservative (no
  takeover until a fresh heartbeat or a confirmed death).
- **`check` shows drift on takeover:** surfaced in the brief; worker reconciles
  before continuing.
- Hooks **never raise** (Phase-1 discipline) — a hook bug must not wedge the
  worker.

## Testing strategy

- **Pure decision logic** (the FSM): table-driven unit tests over synthetic
  liveness inputs → expected transitions (death, both timeouts, progress reset,
  K-no-progress → HALTED, completion). No tmux/process needed.
- **Liveness store:** heartbeat mtime, events append/tail, takeover ledger,
  grant read/write, guard-state persistence.
- **Permission guard:** `PreToolUse` deny-guard blocks `rm`/destructive and
  ungranted remote; allows granted ops; emits `needs-auth`.
- **Resume brief:** deterministic content from a known task (goal, roadmap,
  check result, next step).
- **Integration (gated/optional, needs tmux + a stub "worker"):** a fake worker
  script that touches heartbeat / exits / hangs on cue, driving a real guard
  through death/stall/takeover/halt/done. Real `claude` not required for CI.

## Build staging (auto-kill lands last)

1. **Liveness plumbing + `watch`** — `runtime/` store, worker hooks
   (heartbeat/events), read-only `watch` display. Safe, immediately useful.
2. **`run` spawn + worker bootstrap + auth gate + permission posture** — the
   two-pane session, seed prompt, plan-mode worker, grants, deny-guard.
   Operate manually; guard only *displays* death/stall + offers **manual**
   resume (`r`). No auto-kill yet.
3. **Autonomous guard** — the FSM, the three triggers, auto-takeover
   (`--resume`→brief), progress-gated loop guard, escalation, completion
   notification. The dangerous part, last, on proven foundations.

Each slice is independently shippable; the user can stop after any slice.

## Open risks

- **False-positive takeover** killing a long-thinking worker — mitigated by
  tool-activity heartbeat + conservative `T_idle` + progress-gating, but the
  defaults need real-world tuning.
- **Resume fidelity** — `--resume` behavior is unverified; the brief fallback
  is the guarantee, but a worker resumed only from the brief loses transcript
  nuance. Acceptable by design (waypoint's premise).
- **Autonomy safety** — the permission policy is the backstop; worktree
  isolation for the worker is a possible future hardening (noted, not in this
  spec).
