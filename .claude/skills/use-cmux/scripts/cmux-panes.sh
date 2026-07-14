#!/usr/bin/env bash
#
# cmux-panes.sh — launch one command per split pane in the current cmux
# workspace so every parallel task/agent is visible.
#
# Usage:
#   cmux-panes.sh "cmd for pane 1" "cmd for pane 2" "cmd for pane 3" ...
#
# Each argument is run in its own new split pane. The first goes to the right,
# the rest stack downward. No-ops safely if cmux is not present.
#
# Reference: https://cmux.com/docs/api

set -uo pipefail

SOCK="${CMUX_SOCKET_PATH:-/tmp/cmux.sock}"

# --- detection -------------------------------------------------------------
if ! command -v cmux >/dev/null 2>&1; then
  echo "cmux CLI not found — running nothing. Install cmux or run commands directly." >&2
  exit 127
fi
if [ ! -S "$SOCK" ] || ! cmux ping >/dev/null 2>&1; then
  echo "cmux socket not reachable at $SOCK — is the cmux app running?" >&2
  exit 1
fi
if [ "$#" -eq 0 ]; then
  echo "usage: $0 \"cmd1\" \"cmd2\" ..." >&2
  exit 2
fi

# --- helper: id of the newest surface in the focused pane ------------------
newest_surface() {
  if command -v jq >/dev/null 2>&1; then
    cmux list-pane-surfaces --json | jq -r '.surfaces[-1].surface'
  else
    # Fallback: grab the last "surface" value without jq.
    cmux list-pane-surfaces --json \
      | grep -o '"surface"[^,]*' | tail -n1 | sed 's/.*: *"//; s/"//'
  fi
}

# --- fan out ---------------------------------------------------------------
total="$#"
idx=0
for cmd in "$@"; do
  if [ "$idx" -eq 0 ]; then dir="right"; else dir="down"; fi
  cmux new-split "$dir" >/dev/null
  sid="$(newest_surface)"
  if [ -z "$sid" ]; then
    echo "warning: could not resolve surface id for pane $((idx+1))" >&2
    continue
  fi
  cmux send --surface "$sid" "$cmd" >/dev/null
  cmux send-key --surface "$sid" enter >/dev/null
  echo "pane $((idx+1))/$total -> surface $sid : $cmd"
  idx=$((idx+1))
done

# --- sidebar summary -------------------------------------------------------
cmux set-status agents "$total running" --icon hammer --color "#2563eb" --priority 80 >/dev/null 2>&1 || true
cmux log --level info --source cmux-panes -- "Launched $total pane(s)" >/dev/null 2>&1 || true

echo "Launched $total pane(s). Watch them in the cmux sidebar."