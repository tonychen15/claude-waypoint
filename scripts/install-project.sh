#!/usr/bin/env bash
# install-project.sh — install the waypoint CLI and enable it for a SINGLE
# project (tooling lives in that project's .claude/). Task state stays in the
# same project's .claude/waypoint/.
#
# Usage:  ./scripts/install-project.sh [/path/to/project]   (default: $PWD)
# Idempotent: safe to re-run (hook entries are not duplicated).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJ="$(cd "${1:-$PWD}" && pwd)"
DEST="$PROJ/.claude"

echo "[waypoint] 1/3 installing CLI into your user site…"
# Try a normal user install; fall back to --break-system-packages only if the
# environment is externally managed (PEP 668). Works across pip versions.
python3 -m pip install --user "$REPO" 2>/dev/null \
  || python3 -m pip install --user --break-system-packages "$REPO"

echo "[waypoint] 2/3 copying hooks + skill into $DEST…"
mkdir -p "$DEST/hooks" "$DEST/skills/waypoint"
cp "$REPO"/hooks/*.py "$DEST/hooks/"
cp "$REPO"/skills/waypoint/SKILL.md "$DEST/skills/waypoint/"

echo "[waypoint] 3/3 registering hooks in $DEST/settings.json…"
# Hook commands use the literal $CLAUDE_PROJECT_DIR (expanded by Claude Code at
# runtime), so the wiring is portable if the project directory moves.
python3 - "$DEST/settings.json" '$CLAUDE_PROJECT_DIR/.claude/hooks' <<'PY'
import json, os, sys, pathlib
settings_path, hooks_dir = sys.argv[1], sys.argv[2]
p = pathlib.Path(settings_path)
raw = p.read_text() if p.exists() else ""          # read once
data = json.loads(raw) if raw.strip() else {}
hooks = data.setdefault("hooks", {})
def ensure(event, matcher, cmd):
    arr = hooks.setdefault(event, [])
    if any(h.get("command") == cmd for g in arr for h in g.get("hooks", [])):
        return  # already registered — idempotent
    grp = {"hooks": [{"type": "command", "command": cmd}]}
    if matcher:
        grp["matcher"] = matcher
    arr.append(grp)
ensure("SessionStart", None,                f'python3 "{hooks_dir}/session_start.py"')
ensure("PreToolUse",   "Write|Edit|MultiEdit", f'python3 "{hooks_dir}/pre_tool_use.py"')
ensure("PreCompact",   None,                f'python3 "{hooks_dir}/pre_compact.py"')
p.parent.mkdir(parents=True, exist_ok=True)
tmp = p.with_name(p.name + ".tmp")                  # atomic write
tmp.write_text(json.dumps(data, indent=2) + "\n")
os.replace(tmp, p)
print(f"  updated {p}")
PY

echo "[waypoint] done for $PROJ — $(waypoint --version 2>/dev/null || echo 'CLI installed')."
echo "[waypoint] make sure ~/.local/bin is on your PATH."
