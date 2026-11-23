#!/usr/bin/env bash
# Installs the RSI kit hooks for Claude Code: copies hooks/rsi*.py to <claude dir>/hooks
# and registers two hook groups (SessionStart, PreToolUse) in settings.json.
# Usage: bash install.sh [--claude-dir DIR] [--remove]
# --remove only removes the settings.json registration (files and ~/.rsi-kit are left alone).
# settings.json is edited only when needed, after a .bak-<timestamp> copy.
set -euo pipefail

kit="$(cd "$(dirname "$0")" && pwd)"
claude="$HOME/.claude"
remove=0
while [ $# -gt 0 ]; do
  case "$1" in
    --remove) remove=1; shift ;;
    --claude-dir) [ $# -ge 2 ] || { echo "--claude-dir needs a value" >&2; exit 1; }; claude="$2"; shift 2 ;;
    *) echo "Unknown option: $1 (use --claude-dir DIR, --remove)" >&2; exit 1 ;;
  esac
done

py=python3; python3 -c "" 2>/dev/null || py=python   # Windows Git Bash: python3 may be a Store alias
"$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' ||
  { echo "Python 3.9+ not found as $py. Install it and run again." >&2; exit 1; }

if [ "$remove" -eq 1 ]; then
  "$py" "$kit/tools/setup.py" remove --claude-dir "$claude"
else
  "$py" "$kit/tools/setup.py" install --python "$py" --claude-dir "$claude"
  echo "Next: $py \"$claude/hooks/rsi.py\" init   (then review ~/.rsi-kit/spec/rules.draft.json and rename it to rules.json)"
fi
