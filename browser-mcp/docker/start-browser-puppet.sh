#!/bin/sh
set -eu

DISPLAY_NUM="${BROWSER_PUPPET_XVFB_DISPLAY:-:99}"
SCREEN_ARGS="${BROWSER_PUPPET_XVFB_SCREEN:-0 1280x800x24}"

Xvfb "$DISPLAY_NUM" -screen $SCREEN_ARGS -nolisten tcp &
XVFB_PID=$!

cleanup() {
    kill "$XVFB_PID" 2>/dev/null || true
    wait "$XVFB_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

export DISPLAY="$DISPLAY_NUM"

display_socket="/tmp/.X11-unix/X${DISPLAY_NUM#:}"
for _ in $(seq 1 50); do
    if [ -S "$display_socket" ]; then
        break
    fi
    sleep 0.1
done

"$@" &
APP_PID=$!
wait "$APP_PID"
