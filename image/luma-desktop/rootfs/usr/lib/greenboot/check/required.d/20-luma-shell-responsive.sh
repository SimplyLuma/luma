#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Luma health check (ADR-030 section 6): GNOME Shell answers on D-Bus in the
# graphical session found by the display-manager check (the person's session,
# or the greeter's).
set -euo pipefail
# greenboot 0.16 restarts the computer whenever a required check fails, update
# or not, so this check enforces only on the trial boots of a newly finalized
# deployment (luma-update's helper, docs/os/luma-update.md) and reports without
# failing on every other boot.
. /usr/lib/luma-update/luma-greenboot-common.sh
# A machine its owner has set to boot without a graphical session (for
# example multi-user.target) is not unhealthy for lacking one.
if [ "$(systemctl get-default)" != graphical.target ]; then
  printf 'Luma: default target is %s; no graphical session is expected\n' "$(systemctl get-default)"
  exit 0
fi
timeout=${LUMA_HEALTH_SHELL_TIMEOUT:-180}
deadline=$((SECONDS + timeout))
last=
while [ "$SECONDS" -lt "$deadline" ]; do
  if session=$(/usr/libexec/luma-os/graphical-session); then
    read -r id class type user uid <<<"$session"
    bus="/run/user/$uid/bus"
    if [ -S "$bus" ]; then
      if version=$(timeout 15 setpriv --reuid="$uid" --regid="$(id -g "$user")" --clear-groups \
          env DBUS_SESSION_BUS_ADDRESS="unix:path=$bus" \
          busctl --user --timeout=10 get-property org.gnome.Shell /org/gnome/Shell \
          org.gnome.Shell ShellVersion 2>&1); then
        printf 'Luma: GNOME Shell %s answers in the %s session of %s\n' \
          "$(sed 's/^s //' <<<"$version")" "$class" "$user"
        exit 0
      fi
      last=$version
    else
      last="no session bus at $bus"
    fi
  else
    last='no graphical session'
  fi
  sleep 3
done
luma_check_failed "Luma: GNOME Shell did not answer on D-Bus within ${timeout}s: $last"
