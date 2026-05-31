#!/usr/bin/env bash
# waypoint-cron.sh — autonomous resume trigger (§6A).
#
# Fired by cron for tasks started with `waypoint start --auto`. Relaunches a
# headless Claude to resume the active task and, on a usage-limit, reschedules
# itself to wake at the reset time. NOT a while-true loop; flock guards
# single-instance; the lock is released during the (long) claude run.
#
# Usage: waypoint-cron.sh <project_root> [task_id]
#
# Improvements over the research.sh reference: one-shot (self-removing)
# wake-ups instead of a recurring daily entry, and timezone/date-rollover
# handling deferred to the reset-time parse below.

set -euo pipefail

ROOT="${1:?usage: waypoint-cron.sh <project_root> [task_id]}"
TASK_ID="${2:-}"
LOCK_DIR="$ROOT/.claude/waypoint/.locks"
LOG="$ROOT/.claude/waypoint/.locks/cron.log"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
TAG="# waypoint-cron:$ROOT"

mkdir -p "$LOCK_DIR"

# Ensure claude + waypoint are reachable under cron's minimal PATH.
export PATH="$HOME/.local/bin:$PATH"

log() { echo "[$(date -Iseconds)] $*" >> "$LOG"; }

# Single-quote-escape a value so it embeds safely in a shell command line
# (each ' becomes '\''). Used for every variable written into crontab / at.
shq() { local s=${1//\'/\'\\\'\'}; printf "'%s'" "$s"; }

# Model is configurable; default to a current Sonnet.
MODEL="${WAYPOINT_CLAUDE_MODEL:-claude-sonnet-4-6}"

# Remove all waypoint-cron entries for this root, then optionally write one.
set_cron() {  # set_cron "<cron schedule>"  |  set_cron ""  (remove)
  local schedule="$1" existing filtered
  existing=$(crontab -l 2>/dev/null || true)
  filtered=$(echo "$existing" | grep -vF "$TAG" || true)
  if [[ -z "$schedule" ]]; then
    echo "$filtered" | grep -v '^[[:space:]]*$' | crontab - || crontab -r 2>/dev/null || true
    log "cron removed"
  else
    (echo "$filtered"; echo "$schedule $(shq "$SELF") $(shq "$ROOT") $(shq "$TASK_ID") $TAG") \
      | grep -v '^[[:space:]]*$' | crontab -
    log "cron set: $schedule"
  fi
}

# Single-instance guard: skip if another run holds the lock.
exec 9>"$LOCK_DIR/cron.lock"
if ! flock -n 9; then
  log "another run holds the lock; skipping"
  exit 0
fi

# Pick the task to resume.
if [[ -z "$TASK_ID" ]]; then
  TASK_ID=$(waypoint --root "$ROOT" list 2>/dev/null | awk 'NR==1{print $1}')
fi
if [[ -z "$TASK_ID" || "$TASK_ID" == "(no" ]]; then
  log "no active task; removing cron"
  set_cron ""
  exit 0
fi

# Run claude headless to resume. Scoped tools keep human gates real: no
# blanket --dangerously-skip-permissions.
RESUME_PROMPT="Resume waypoint task '$TASK_ID'. Run \`waypoint resume --id $TASK_ID\`, \
re-read STATUS.md and the last step's artifacts, then continue FORWARD only up \
to the next human gate (an outbound third-party write, an ambiguous pending \
effect, or an integrity 'go-deep' mismatch). At a gate, stop and leave the \
task in_progress. If you hit a usage limit, stop immediately."

TMP_LOG=$(mktemp)
log "resuming task $TASK_ID (headless)"
# Release the lock during the long run so it doesn't block the next tick's
# single-instance check from reporting accurately. fd 9 stays held only here;
# we intentionally keep it for the duration to serialize resumes of one task.
set +e
claude --model "$MODEL" -p "$RESUME_PROMPT" \
  --allowedTools "Agent,Bash,Edit,Glob,Grep,Read,Write,WebSearch,WebFetch" \
  >>"$TMP_LOG" 2>&1
set -e
cat "$TMP_LOG" >> "$LOG"

# Rate-limit handling: park + schedule a one-shot wake at the reset time.
if grep -qiE "usage limit|hit your limit" "$TMP_LOG"; then
  reset=$(grep -oiE "resets? [0-9]{1,2}(:[0-9]{2})?[[:space:]]*[ap]m" "$TMP_LOG" | head -1 || true)
  if [[ -n "$reset" ]] && command -v at >/dev/null 2>&1; then
    # Prefer `at` for a true one-shot wake-up (parses "2pm", "2:30am", etc.).
    when=$(echo "$reset" | grep -oiE "[0-9]{1,2}(:[0-9]{2})?[[:space:]]*[ap]m")
    echo "$(shq "$SELF") $(shq "$ROOT") $(shq "$TASK_ID")" | at "$when" 2>>"$LOG" \
      && log "rate-limited; one-shot scheduled via at for $when" \
      || { set_cron "*/30 * * * *"; log "at failed; fallback every 30m"; }
  else
    set_cron "*/30 * * * *"
    log "rate-limited; reset time unknown — polling every 30m until it clears"
  fi
  rm -f "$TMP_LOG"
  exit 0
fi

rm -f "$TMP_LOG"

# Still work to do? keep a heartbeat; else remove the cron.
if waypoint --root "$ROOT" list 2>/dev/null | grep -q "$TASK_ID"; then
  set_cron "0 */2 * * *"   # safety-net heartbeat
  log "task still active; heartbeat scheduled"
else
  set_cron ""
  log "task closed; cron removed"
fi
