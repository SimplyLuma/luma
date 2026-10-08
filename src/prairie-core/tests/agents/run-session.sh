#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Run one ADR-033 agent session suite as the session user of a systemd container.
#   run-session.sh <suite> <evidence-dir> [suite options]   suite: messages | phone | calendar | clock | connect | charlie
# Starts the stand-ins the suites need in the user session: the notification
# server and screen lock, and an unlocked Secret Service.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SUITE=$1
EVIDENCE=$2
shift 2
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}
mkdir -p "$EVIDENCE"
systemctl --user reset-failed >/dev/null 2>&1 || true
systemctl --user stop luma-test-notifications >/dev/null 2>&1 || true
if true; then
  systemd-run --user --quiet --unit=luma-test-notifications \
    --setenv=LUMA_TEST_NOTIFICATIONS="$EVIDENCE/notifications.jsonl" python3 "$HERE/notification_stub.py"
fi
if ! systemctl --user is-active -q luma-test-keyring; then
  systemd-run --user --quiet --unit=luma-test-keyring sh -c 'printf luma-test | exec gnome-keyring-daemon --foreground --unlock --components=secrets'
fi
if ! systemctl --user is-active -q luma-test-display; then
  # A display for windows the agents open (Calendar from a reminder, Phone from a call).
  systemd-run --user --quiet --unit=luma-test-display Xvfb :90 -screen 0 1280x800x24 -nolisten tcp
fi
systemctl --user set-environment DISPLAY=:90 GSK_RENDERER=cairo
sleep 2
exec python3 "$HERE/${SUITE}_agent_session.py" --evidence "$EVIDENCE" "$@"
