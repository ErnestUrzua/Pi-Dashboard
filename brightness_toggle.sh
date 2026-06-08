#!/bin/bash
# Toggle the brightness overlay (or launch it if not running)
PID_FILE=/tmp/brightness_overlay.pid

if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
    kill -USR1 "$(cat $PID_FILE)"
else
    WAYLAND_DISPLAY=wayland-0 python3 /home/pi/projects/brightness_overlay.py &
fi
