#!/bin/bash
# Host side, given the vm wrapper: start Steam once so its client bootstrap
# (a few hundred MB from Valve's CDN) is in the snapshot, wait for the login
# window, then stop it.
set -euo pipefail
VM="$1"
"$VM" session hyprctl dispatch 'hl.dsp.exec_cmd("steam")' | grep -qx ok
echo "   waiting for the Steam login window (bootstrap download; up to 20 min)"
for _ in $(seq 1 240); do
    if "$VM" session hyprctl -j clients | python3 -c '
import json, sys
sys.exit(0 if any(c["class"] == "steam" for c in json.load(sys.stdin)) else 1)'; then
        echo "   steam window is up"; break
    fi
    sleep 5
done
"$VM" session hyprctl -j clients | grep -q '"class": "steam"' || { "$VM" ssh 'ls -la ~/.local/share/Steam 2>&1 | head; tail -n 20 ~/.local/share/Steam/logs/bootstrap_log.txt 2>&1' >&2; exit 1; }
sleep 5
"$VM" ssh 'pkill -x steam; pkill -x steamwebhelper; sleep 3; pgrep -a steam || true'
