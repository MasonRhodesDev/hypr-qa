#!/bin/bash
# Host side, given the vm wrapper: start Steam so its client bootstrap (a few hundred MB from
# Valve's CDN) is in the snapshot, wait for the login window, stop it. Twice: the first launch
# after the install re-extracts the client package (and restores its stock files), a second one
# settles it, so a launch in a scenario is an ordinary one and not a client update in disguise.
set -euo pipefail
VM="$1"

launch_once() {
    "$VM" session hyprctl dispatch 'hl.dsp.exec_cmd("steam")' | grep -qx ok
    echo "   waiting for the Steam login window (bootstrap download on the first launch; up to 20 min)"
    local up=0
    for _ in $(seq 1 240); do
        if "$VM" session hyprctl -j clients | python3 -c '
import json, sys
sys.exit(0 if any(c["class"] == "steam" for c in json.load(sys.stdin)) else 1)'; then
            echo "   steam window is up"; up=1; break
        fi
        sleep 5
    done
    [ "$up" = 1 ] || { "$VM" ssh 'tail -n 20 ~/.local/share/Steam/logs/bootstrap_log.txt 2>&1' >&2; return 1; }
    sleep 10
    # -x: pkill -f would match this ssh command line too.
    "$VM" ssh 'pkill -x steam; pkill -x steamwebhelper; sleep 4; pgrep -x steam || true'
}

launch_once
launch_once
