#!/usr/bin/env bash
# Tiny Shenzhen in tmux: the viewer and a shell in the pipeline.
#   ./dev.sh            the Shenzhen viewer (tinysz)
#   ./dev.sh tinysz-full
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="tinyshenzen"
WORLD="${1:-tinysz}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  exec tmux attach -t "$SESSION"
fi

tmux new-session -d -s "$SESSION" -n dev -c "$ROOT/tinyworlds-frontend"
tmux send-keys -t "$SESSION:dev.0" "NEXT_PUBLIC_WORLD=$WORLD npm run dev" C-m
tmux split-window -v -t "$SESSION:dev" -c "$ROOT/tinyworlds-pipeline"
tmux send-keys -t "$SESSION:dev.1" "# npm run all -- --city $WORLD   (fetch → scene → render → index)" C-m
tmux select-pane -t "$SESSION:dev.0"
exec tmux attach -t "$SESSION"
