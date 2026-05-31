# waypoint — Design

**Date:** 2026-05-30
**Status:** Design approved, pending implementation plan
**Scope:** Generic, file-based, checkpoint-restart resume mechanism for Claude Code. Prototyped inside one project, intended for promotion to `~/.claude` doctrine so every project can use it.

---

## 1. Problem

A multi-step task in Claude Code can be interrupted before it finishes — you close the session, the CLI crashes, or work stops at a token limit. Today the only recovery is the native transcript (`claude --resume`), which replays raw conversation, is mangled by `/compact`, and is not a legible "where was I" view.

We want: when a tracked task is interrupted mid-flight, a freshly started Claude Code session can read a small, legible state from `<project>/.claude/waypoint/`, understand what was done and what is next, and **continue as if it had just taken a coffee break** — not "recover from a crash."

### Design goal (north star)

> Resume should feel like Claude finished step b, stepped away for coffee, and came back to start step c — seamless continuation, not a painful manual reconstruction. (Crash, token-limit, and close are all recovered; the *feel* is a coffee break, not a fire drill.)

### Non-goals (explicitly out of scope)

- **Judging whether a step is *semantically* correct.** "Success" here is operational (§4), not a quality verdict. Catching a wrong-but-completed step remains the human's job, exactly as today.
- **Catching the crash itself.** We never rely on running code at the moment of a crash. State is durable *before* the crash because we commit only at clean step boundaries.
- **A general workflow engine.** This is a lightweight convention + a few hooks, not Temporal/LangGraph. (It does map cleanly onto the project's LangGraph target — §11.)

---

## 2. The model: forward-recovery checkpoint-restart (and why it is NOT a saga)

This was named "saga" early in design; that label is **inaccurate** and was dropped. The distinction is load-bearing, so it is stated up front.

- A **saga** (Garcia-Molina & Salem, 1987) is a long-lived transaction whose committed sub-transactions each have a **compensating transaction**; on failure it runs those compensations to *semantically undo* committed steps — **backward recovery**. Its defining feature is rollback of completed work.
- **We do the opposite.** Completed steps are **durable and correct**; an interruption (crash / token limit / closed window) does **not** invalidate them, so there is **nothing to compensate**. Resume continues *forward* from the last committed step and re-runs only the *uncommitted* in-flight step (idempotent retry).

So the model is **checkpoint-restart with forward recovery** — the lineage of HPC checkpointing, Temporal/LangGraph durable execution, and write-ahead logging. The only consistency concern we have (irreversible outbound effects) is solved with **idempotency keys / a write-ahead ledger** (§5), which is the durable-execution toolbox — not saga compensation.

### Core invariant

A **task** is one tracked unit of work: an ordered list of **steps** derived from a plan. Each committed step is a **waypoint**. The governing rule:

1. A step enters the **committed ledger only after it succeeds** (operational success — §4).
2. **At most one step is uncommitted** (`in_progress`) at any instant.
3. On resume, the in-progress step is **discarded and re-run** from the last waypoint — never half-recovered.

Because every step *except* the current in-flight one is durably on disk before any new work begins, a crash loses at most the current step — which the model already throws away and re-runs.

### Keying: by **task**, not by session

State is keyed by a stable `task_id` minted at `/waypoint start` (e.g. `2026-05-30-<slug>`), **not** by Claude Code session ID.

- **Why not session ID:** a resumed session gets a *new* session ID, so session-keyed state could never be found on resume. Session keying solves concurrency but destroys resume.
- **Task keying gives both:** two concurrent sessions on *different* tasks → different `task_id` → different directories → no shared writes. A new session resuming a task **adopts** it by `task_id` and writes the same directory (continuity across the crash boundary).

---

## 3. State layout (`.claude/waypoint/`)

```
<project>/.claude/waypoint/
├── <task_id>/
│   ├── waypoint.json    # machine SSOT (atomic write: tmp + os.replace)
│   └── STATUS.md        # human-readable roadmap, regenerated from waypoint.json
├── archive/             # completed / abandoned tasks, MOVED here (never rm)
└── .locks/              # lease locks for concurrent mode (§8)
```

- Big working data (input files, result files) **stays where it lives** in the repo; the checkpoint only **binds pointers** to it plus small declared text. This keeps `waypoint.json` small enough to survive `/compact` and to fit the SessionStart injection cap (§6).
- Nothing is ever `rm`'d — completion/abandonment **moves** the task to `archive/`.

### `waypoint.json` schema

```jsonc
{
  "task_id": "2026-05-30-resumable-checkpoints",
  "goal": "one-line overall objective",
  "status": "in_progress",            // in_progress | completed | abandoned
  "created_at": "...", "updated_at": "...",
  "owner_session": "<session-id>",    // current adopter
  "heartbeat": "...",                 // refreshed by the live session (§8)
  "session_history": ["<sid1>", "..."],
  "scope": ["src/", "knowledge/"],    // declared folders/files (from plan) for overlap detection
  "steps": [                          // committed = succeeded only (the waypoints)
    {
      "id": "b",
      "purpose": "...",
      "context": "why this step; conditions/assumptions",          // captures intent
      "inputs": [
        { "path": "knowledge/raw/x.json", "git_blob": "…", "size": 1, "mtime": "…" }
      ],
      "expected_result": "what 'done' looks like — the contract",
      "actual_result": {
        "summary": "what was produced",
        "artifacts": [
          { "path": "knowledge/analysis/x.json", "git_blob": "…", "size": 1,
            "mtime": "…", "step_commit": "9f8e7d…" }
        ]
      },
      "effects": [                    // outbound third-party effects ledger (§5)
        { "action": "telegram_push", "key": "digest-2026-05-30",
          "status": "completed", "at": "…" }
      ],
      "status": "succeeded",
      "completed_at": "..."
    }
  ],
  "current_step": {                   // the in-flight / interrupted step
    "id": "c", "purpose": "...", "target": "...",
    "context": "...", "inputs": [ /* … with fingerprints */ ],
    "expected_result": "...",
    "status": "in_progress"
  }
}
```

`STATUS.md` mirrors this as a roadmap so you can see where you are *and what's ahead*:

```
# Task: resumable-checkpoints   (in_progress, last touched 2026-05-30 09:12)
✓ a  bootstrap state layout
✓ b  write checkpoint library
▶ c  wire the PreToolUse tripwire     ← current
☐ d  add SessionStart resume
☐ e  tests
```

---

## 4. What is a "step", and what is "success"

### Granularity: two levels, anchored to plan mode

`/waypoint start "goal"` has Claude **plan** the task (plan mode), and the **approved plan** becomes the step skeleton. This anchors granularity to a *human-reviewed* decomposition rather than Claude's unaided judgment.

- **Plan steps** (coarse, human-approved): the named intent units, seeded as `pending` upfront so `STATUS.md` shows the full roadmap.
- **Waypoints** (the commit unit): fire at **durable-state change** — at minimum once per plan step on completion, finer if a plan step produces several durable outputs or contains an outbound effect to isolate at a boundary.

A good step satisfies all three:

1. **One purpose/target.** Purpose changes ⇒ new step.
2. **Coffee-break-able.** Ends in a state describable in a sentence + artifact pointers.
3. **≤1 outbound/irreversible effect, placed at its *end*.** This shrinks the dangerous "interrupted mid-effect" window to almost nothing.

**Mnemonic:** *step size = the blast radius of an interruption* — how much you're willing to redo. Too big if re-running repeats an expensive LLM/API call or minutes of work → split before the expensive part. Too small if there's no durable result yet → wait for a coherent result; a step is a sub-goal, not a tool call.

### Success = operational forward progress

A step **succeeded** if the model returned something that **did not require human intervention and let Claude proceed to the next step**. The act of opening the next `current_step` *is* the commit of the previous one — one atomic transition.

- If a step needs human intervention (asks a question, hits an unresolvable error, stalls), Claude **does not advance**, so the step stays `in_progress` — exactly the resume boundary.
- We do **not** run a semantic verifier. `expected_result` vs `actual_result` is recorded and auditable (visible in `STATUS.md`) so a bad call can be *caught by the human*, but the system does not block on judging correctness.

---

## 5. Effects policy (auto mode)

The axis that decides auto-resume vs. human gate is **where the effect lands**:

| Effect class | Examples | On resume |
|---|---|---|
| **Read (anywhere)** | local reads, WebFetch/GET, external API reads | **auto** — safe, no external mutation |
| **Local write** | filesystem, local `git commit`, markers | **auto** — via observe-then-act / idempotent overwrite |
| **Outbound third-party write** | Telegram send, email, POST/PUT/DELETE, `git push`, posting | **human gate** — write-ahead effects ledger |

Even in auto mode, **only outbound third-party writes break the coffee break.** They use a **two-phase write-ahead ledger** (closes the write-gap race where the effect fires but the snapshot wasn't yet updated):

| Ledger state on resume | Meaning | Action |
|---|---|---|
| no entry | never started | **execute** |
| `pending` (intent, no `completed`) | crashed mid-fire — ambiguous | **ask human** ("did the digest send? [y/n]") |
| `completed` | confirmed done | **skip, never repeat** |

The human is pulled in **only** for the ambiguous `pending` row. For local/observable effects the rule is simply **observe-then-act**: before re-running the in-progress step, look at what's on disk / which markers exist and do only what remains.

---

## 6. Resume flow (coffee-break)

1. **Surface (SessionStart hook).** On a new session, the hook scans `.claude/waypoint/*/waypoint.json` for unfinished tasks and surfaces them with an **age label** for information only — *it never mutates state by age* (§7):
   > ⏸ Paused task **"resumable-checkpoints"** — last touched **2 hours ago (active)**. Resume / discard / leave it?
2. **Confirm.** Claude offers; the human confirms. Control stays with the human.
3. **Integrity check (§9)** of the last committed step's artifacts.
4. **Continue seamlessly.** Claude re-hydrates from `context` + `expected_result` + the *actual artifact files* (not the prose summary) and continues — no "RESUMING FROM CRASH" drama. The in-progress step is re-run via observe-then-act.

### Hook-mechanics constraints (from research — must design around these)

- **`PreCompact` runs *shell only*; it cannot make Claude reflect-and-write** before compaction (Anthropic open request, not shipped as of early 2026). So our `PreCompact` hook does **not** ask Claude to summarize — it just **copies/timestamps the already-current `waypoint.json`** (which is maintained at step boundaries anyway). This is consistent with "never rely on catching the moment": the snapshot is of already-committed state, not a last-second reflection.
- **`SessionStart` `additionalContext` is capped at ~10,000 chars and is injected at user-weight priority** (Claude can't selectively ignore it) — good for surfacing, but the injected resume summary **must be compact** (point at `STATUS.md` + the current step, not dump the whole task). Because the model **honors injected guidance inconsistently**, the `/waypoint` skill must *also* re-assert the resume contract, not rely on the hook injection alone.

---

## 6A. Autonomous resume via cron (rate-limit-aware break)

§6 covers *interactive* resume (you reopen Claude Code; SessionStart surfaces the task; you confirm). This section adds an **opt-in autonomous mode** that resumes a task **without you reopening anything** — most importantly across a usage-limit "break," where Claude must stop, wait for the limit to reset, and pick the task back up on its own.

**Prior art (idea borrowed, not depended on):** `research.sh` (`~/Documents/Tech/Distributed-Systems/Core-Technologies/research.sh`) is a working implementation of this pattern — a file-as-queue (`learning_topics.md`, status by line prefix `none|@|#`), `flock`-serialized mutations (lock held only for the fast critical section, released during the long `claude` run), PID-file stale recovery, and a self-managed crontab that **on hitting a usage limit parses the reset time, reschedules itself to fire at the reset, reverts in-flight work to waiting, and exits** — then resumes when cron re-fires.

> **Decoupling:** the *general* form of this mechanism (a reusable file-based, cron-driven, rate-limit-aware task queue) is being extracted into a **separate task-queue project**. waypoint deliberately does **not** depend on it — `waypoint-cron.sh` here is a thin, self-contained borrowing of the *idea* (manual resume is the default; cron auto-resume is opt-in). If the two ever converge, waypoint could later delegate scheduling to that engine, but the MVP stays standalone.

### Mechanism

- **`waypoint-cron.sh`** — a thin trigger (modeled on `research.sh`), fired by cron. It is *not* a `while true` loop. It:
  1. uses `flock` for single-instance safety (held only briefly);
  2. runs `revert_stale`-style reclaim (dead `owner`/stale `heartbeat` → in-flight step stays `in_progress`, lock released) — this is the §8 lease/heartbeat reclaim, concretely;
  3. relaunches `claude -p "<resume contract for the active task>"` **headless** with scoped `--allowedTools` (not blanket skip-permissions, so human gates stay real).
- **Rate-limit reschedule:** after the headless run, scan output for the usage-limit signal and reset time; compute the reset moment and install a **one-shot** wake-up (`at`, or a self-removing cron entry) at that time. On limit, the task is left `in_progress` (the in-flight step re-runs on resume — §2). A **safety-net heartbeat** cron (default every 2h, configurable) catches stuck/orphaned tasks.
- **Self-chaining:** on a clean finish with steps still `pending`, re-invoke to keep going; on `/waypoint done|abandon`, **remove the cron entry** (`set_cron ""`).

### Two resume modes

| Mode | Trigger | Behavior |
|---|---|---|
| **Interactive** (default) | you reopen Claude Code → SessionStart hook | surfaces the task, you confirm, then seamless continue (§6) |
| **Autonomous** (`/waypoint start --auto`) | `waypoint-cron.sh` via cron | headless relaunch + rate-limit reschedule + heartbeat; **no confirmation** |

### Headless has no human — so it stops at every human gate

Autonomous resume runs **forward only up to the next human-gate**, then **stops, leaves the task `in_progress`, and notifies** (same stop-and-reschedule path as a rate-limit). It never auto-fires a human-gated action. The gates that halt an autonomous run:

- an **outbound third-party write** (§5) — always human in this design;
- an **ambiguous `pending`** effect-ledger entry (§5);
- an integrity **"go deep" mismatch** (§9) — a file changed underneath the task.

So autonomous mode makes progress on the *auto-safe* portion of a task across breaks, and hands the human-gated remainder back to the next interactive session. This keeps the coffee-break guarantee and the safety guarantees simultaneously.

### Improvements over the `research.sh` reference

- **One-shot, not recurring:** `research.sh` installs `0 $hour * * *` (fires *every* day at that hour). waypoint computes a one-shot wake-up at the actual reset instant and removes it after firing.
- **Date-rollover + timezone:** parse minutes and day-rollover (a limit that resets after midnight), and compute against the project timezone (the knowledge-base project uses UTC+8), not a bare same-day hour.
- **Structured signal:** prefer a structured rate-limit signal over grepping `"hit your limit"` prose when one is available from the harness.

## 7. Staleness: report, never auto-act

The system **never mutates a task based on age.** A paused task persists byte-for-byte until *you* touch it. Age is only a **display label** (`active` / `inactive`).

- Closing is explicit and as easy as opening: `/waypoint done`, `/waypoint abandon` (both **move** to `archive/`).
- The decisive moment is the **next** `/waypoint start`: starting a new task while one is active forces the choice right when it matters — *resume / set aside (kept as-is) / cancel*.
- **Accepted residual:** parked tasks accumulate forever (nothing auto-cleans). Deliberate — never lose a task to a guess. They are tiny JSON; discard manually if desired.

---

## 8. Concurrency

**Isolate first; detection is the just-in-case.**

- **Detecting contention:** `/waypoint start` checks other *active* tasks' declared `scope` (folders/files, from the plan). Overlap → **concurrent mode**.
- **Live mutual exclusion — lease lock, not `flock(2)`.** `flock(2)` is process-lifetime: it releases the instant a session pauses/crashes (exactly the window we need to protect), and Claude's Write/Edit tools can't be wrapped in a held `flock` from inside the agent. Instead, concurrent mode uses a **lease lock** in `.claude/waypoint/.locks/`: a lockfile carrying `owner` + `heartbeat`, checked by a `PreToolUse` hook before writing a contended file. A dead session's **stale heartbeat ⇒ orphaned ⇒ safe to take over** (no deadlock on a dead session).
- **Same-task, two sessions:** adoption writes `owner` + `heartbeat`. A second session adopting a task whose heartbeat is *recent* is **warned** and falls back to report-and-decide ("looks active in another window — take over? [y/n]"). A stale heartbeat means safe to adopt.

Across-break staleness (a paused task resuming onto a file another task changed) is **not** covered by any live lock — it is covered by the fingerprint detection in §9.

---

## 9. Resume integrity detection (is the last step's result still there?)

Each result artifact is **fingerprinted at checkpoint time** and re-checked on resume. Three layers, cheapest first:

- **Layer 1 — existence:** `Path(p).exists()` — catches deletion.
- **Layer 2 — fast stamp:** compare `(size, mtime)`; match ⇒ almost certainly untouched, no read.
- **Layer 3 — authoritative integrity:** `git hash-object <path>` (working-tree blob SHA) vs recorded `git_blob`. Git's own content identity — exact, reproducible, near-free in a git repo. Non-git files fall back to `sha256`.

**Decision tree on the last committed step's artifacts:**

| Check result | Meaning | Action |
|---|---|---|
| missing | gone (deleted) | **surface to human** |
| present and `git_blob` matches | intact, byte-identical | **keep going** (seamless) |
| present but `git_blob` differs | changed / maybe replaced | **"go deep"** ↓ |

**"Go deep" — did the step's contribution survive or was it replaced?** Requires a reference for what the step wrote. The clean way is **checkpoint = git commit** (the dominant community pattern — see §10; recorded as `step_commit`):

- `git diff <step_commit> -- <path>` shows exactly what changed since the step.
- Diff only **adds**, doesn't touch the step's lines ⇒ **survived → keep going** (record a note).
- Diff **removes/overwrites** the step's lines ⇒ **replaced → surface to human.**
- Without per-step commits, "go deep" degrades to re-reading the file + judging whether `expected_result` still holds (LLM judgment) → conservative default is **surface to human.**

**Detection ≠ prevention.** With lease-lock isolation this should not happen; the fingerprint is the backstop for the window the lock can't cover. The strong guarantee is: *a resumed task never silently builds on a file that changed underneath it.*

---

## 10. Prior art & positioning (research, 2026-05-30)

Nothing in the Claude Code ecosystem combines what this design does. The closest projects each have *some* pillars:

| Project | ~Stars | Has | Missing vs. waypoint |
|---|---|---|---|
| **maestro** (ReinaMacCredy) | 179 | on-disk task ledger, structured continuations, verdict gating | task=PR granularity, no per-step replay, no PreToolUse enforcement |
| **planning-with-files** (OthmanAdi) | 22.4k | plan-derived steps, durable progress, PreToolUse re-read | markdown checkboxes not JSON state; no last-good replay or artifact-pointer schema |
| **Continuous-Claude** (parcadei) | 3.8k | 30 lifecycle hooks, YAML handoffs | context-continuity oriented, not per-step success markers |
| **task plugin** (mmmprod) | — | snapshots, decisions log, handoff | manual checkpoints, no enforcement |
| native `/rewind` | built-in | code+chat snapshot per prompt | "local undo," **doesn't track bash changes**, not a plan/step model |

**The gap waypoint fills:** (1) no one combines *last-good-checkpoint forward replay* with *plan-derived discrete steps* and a *structured JSON per-step schema*; (2) no one enforces *resume integrity at the tool boundary* as a first-class design; (3) *crash-recovery-with-artifact-pointers as an idempotent contract* is absent.

**Findings that validate the design:**

- **Git-commit-per-step is the dominant community pattern** (HN: 80–160 commits/session; PostToolUse-commit hooks; Aider auto-commits). Confirms the `step_commit` / `git hash-object` mechanism.
- **Auto-compaction mid-task is *the* documented hazard** ("compaction kept destroying my work"). Confirms the "small state that survives `/compact`" goal and the `PreCompact` snapshot.
- **`SessionStart` `additionalContext` injects at user-weight priority** — ideal for surfacing resume (constraints noted in §6).

**Naming:** *handoff / checkpoint / session / resume / memory* are saturated or native. "waypoint" is unused and accurately describes forward recovery from a marked point.

---

## 11. Commands & components

### `/waypoint` skill (lifecycle command)

- `/waypoint start "goal" [--auto]` — plan the task (plan mode), mint `task_id`, seed `waypoint.json` from the approved plan, arm the gate. `--auto` enables autonomous cron resume (§6A) and installs the safety-net heartbeat. On collision with an active task, prompt resume/set-aside/cancel.
- `/waypoint done` — set `completed`, move to `archive/`, disarm, **remove any cron entry** (§6A).
- `/waypoint abandon` — set `abandoned`, move to `archive/`, **remove any cron entry**.
- `/waypoint resume <task_id>` — adopt an orphaned task.
- `/waypoint status` — print `STATUS.md`.

### Hooks

- **SessionStart** — surface unfinished tasks with age labels; offer resume (§6). Compact injection; skill re-asserts the contract.
- **PreToolUse** (`Write|Edit|MultiEdit`) — enforce ≤1 uncommitted step (§10/§2); in concurrent mode, enforce the lease lock on contended files (§8). Exempt writes to `.claude/waypoint/**`.
- **PreCompact** — copy/timestamp the current `waypoint.json` before token-limit compaction (shell-only — §6).

### `waypoint-cron.sh` (autonomous mode — §6A)

A thin cron-fired trigger (modeled on `research.sh`): `flock` single-instance, lease/heartbeat reclaim, headless `claude -p "resume <task_id>"` with scoped `--allowedTools`, rate-limit one-shot reschedule, safety-net heartbeat, self-chaining, and `set_cron`-style "remove-all-then-write-one" crontab management. Only installed for `--auto` tasks.

### Checkpoint library (shared by skill + hooks)

A small module: atomic write (`tmp` + `os.replace`), fingerprinting (`git hash-object` / `sha256`, size+mtime), optional per-step git commit, `STATUS.md` regeneration, schema validation. Pure stdlib + git CLI so it can live globally.

### Data flow

```
plan (plan mode) ──/waypoint start──▶ seed waypoint.json (steps = pending)
   ▼ step loop
declare current_step → do work → commit waypoint (fingerprint + optional git commit) → advance
   ▼
/waypoint done ──▶ archive/

resume:
SessionStart surfaces task → human confirms → integrity check last step (§9)
   → intact: continue (coffee-break)  |  gone/replaced: surface to human
```

### Mapping to the LangGraph target

The approved plan ≈ the graph definition (nodes); a durable-state waypoint ≈ a LangGraph checkpointer at a node boundary; `task_id` ≈ a LangGraph thread. The mental model is identical, so this file-based prototype is forward-compatible.

---

## 12. Global rollout

- **`~/.claude/CLAUDE.md` = doctrine only** — the rule, how it works, the edge cases. **No task instance / no state ever lives in `~/.claude`.** It is a template guiding each project to keep state in its own folder.
- **Global = doctrine + reusable tooling** — the `/waypoint` skill and hook scripts live in `~/.claude/skills/` + `~/.claude/hooks/` (code, not state). Paths resolve via `$CLAUDE_PROJECT_DIR`.
- **Per-project = state only** — `.claude/waypoint/`.
- **Two phases:** (1) prototype skill + hooks + state inside one project; (2) once proven, lift skill + hooks to `~/.claude/` and add doctrine to `~/.claude/CLAUDE.md`, leaving state per-project.

---

## 13. Testing (pytest, `tests/unit/`)

- **Fingerprinting:** `git hash-object` vs `sha256` fallback; size+mtime fast path; detection tree (missing / match / differ).
- **"Go deep":** git-diff classification of survived vs replaced.
- **State machine:** atomic commit = step transition; ≤1 uncommitted invariant; replanning preserves committed steps, mutates only the pending tail.
- **Effects ledger:** `none` → run, `pending` → ask, `completed` → skip.
- **Lease lock:** fresh heartbeat ⇒ contended/warn; stale heartbeat ⇒ orphaned/safe.
- **Atomic write:** `tmp` + `os.replace`, no torn file on mid-write kill.
- **Archive:** completion/abandonment moves (never deletes); `archive/` is the only destination.
- **STATUS.md:** regenerates correctly from `waypoint.json`.

---

## 14. Resolved design threads (audit trail)

| # | Thread | Resolution |
|---|---|---|
| 1 | Discipline — making Claude checkpoint | PreToolUse tripwire (hard) + plan-anchored content (soft); gate armed only by `/waypoint start` |
| 2 | Self-reported success | De-scoped to *operational forward progress*; `expected`/`actual` recorded & auditable, not verified |
| 3 | Re-running a step with side effects | observe-then-act for local; write-ahead effects ledger + human gate for outbound |
| 4 | What is a "step" | Two-level: plan steps (human-approved) + durable-state waypoints; blast-radius mnemonic |
| 5 | Token-limit timing | `PreCompact` snapshot (shell-only, copies current state); tripwire keeps prior steps durable |
| 6 | Interrupted vs abandoned | Report, never auto-act; age = label only; human decides; tasks persist as-is |
| 7 | Lossy summary | Structured checkpoint: `context` + `expected_result` capture intent; resume re-reads artifacts |
| 8 | Concurrency / global rollout | Task-keyed isolation + lease lock (not flock); global = doctrine + code, state per-project |
| — | "Is this a saga?" | No — forward-recovery checkpoint-restart; renamed `saga` → `waypoint` (§2) |
| — | Autonomous resume across a rate-limit break | Opt-in cron mode (§6A) adapted from `research.sh`: file-as-queue + `flock` + PID/lease reclaim + reset-time reschedule; headless run stops at every human gate |

---

## 15. Open items (non-blocking, future work)

- Lease-lock heartbeat interval / staleness threshold — concrete values during implementation.
- Whether per-step git commits go on a side branch or the working branch (Aider uses the working branch).
- Global promotion details (skill/hook discovery precedence when a project defines its own).
- `archive/` retention (unbounded by design — revisit only if it ever matters).
- SessionStart-injection reliability — measure how often the model acts on it; reinforce via the skill if low.
- Autonomous mode (§6A): robust reset-time detection (structured signal vs prose grep), one-shot wake-up mechanism (`at` vs self-removing cron), timezone/date-rollover handling, and headless `--allowedTools` scoping per task.
