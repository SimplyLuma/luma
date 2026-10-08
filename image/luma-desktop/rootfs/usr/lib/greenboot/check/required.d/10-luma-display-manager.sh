#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Luma health check (ADR-030 section 6): the display manager reached the
# greeter (the login screen or first-boot setup) or a person's graphical
# session, and that session stayed up.
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
timeout=${LUMA_HEALTH_DISPLAY_TIMEOUT:-300}
# A session whose Shell fails at startup still registers with logind for a
# moment before GDM tears it down and tries again, so a session only counts
# once it has stayed up this long.
settle=${LUMA_HEALTH_DISPLAY_SETTLE:-30}
deadline=$((SECONDS + timeout))
dm_unit=$(systemctl show -p Id --value display-manager.service 2>/dev/null || true)

dm_gave_up() {
  # GDM stops starting the greeter after repeated session failures.
  [ -n "$dm_unit" ] &&
    journalctl -b -u "$dm_unit" --no-pager -o cat 2>/dev/null |
      grep -Fq 'maximum number of display failures reached'
}

candidate=
since=0
while [ "$SECONDS" -lt "$deadline" ]; do
  if dm_gave_up; then
    journalctl -b -u "$dm_unit" --no-pager -o cat 2>/dev/null | tail -n 20 >&2 || true
    luma_check_failed 'Luma: the display manager gave up after repeated greeter or session failures'
  fi
  if systemctl is-failed --quiet display-manager.service; then
    break
  fi
  if systemctl is-active --quiet display-manager.service &&
     session=$(/usr/libexec/luma-os/graphical-session); then
    read -r id class type user _ <<<"$session"
    if [ "$id" != "$candidate" ]; then
      candidate=$id
      since=$SECONDS
    elif [ $((SECONDS - since)) -ge "$settle" ]; then
      printf 'Luma: display manager reached a %s session that stayed up %ss (%s, %s, session %s)\n' \
        "$class" "$settle" "$type" "$user" "$id"
      exit 0
    fi
  else
    candidate=
  fi
  sleep 3
done
systemctl status display-manager.service --no-pager --lines=20 >&2 || true
luma_check_failed "Luma: the display manager did not keep a greeter or session up for ${settle}s within ${timeout}s"
