#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Release the one known postmarketOS/Fedora bring-up handoff failure before the
# graphical login starts: a deleted boot-time Plymouth process retaining the
# DRM master. Every identity check must match; all other DRM holders are left
# untouched. SIGKILL is intentionally forbidden.

set -eu

drm_device=/dev/dri/card0
[ -e "$drm_device" ] || exit 0

pids=$(fuser "$drm_device" 2>/dev/null | xargs || true)
[ -n "$pids" ] || exit 0

set -- $pids
[ "$#" -eq 1 ] || exit 0
pid=$1

[ "$(awk '/^Uid:/{print $2}' "/proc/$pid/status" 2>/dev/null || true)" = 0 ] || exit 0
[ "$(readlink "/proc/$pid/exe" 2>/dev/null || true)" = "/usr/bin/plymouthd (deleted)" ] || exit 0
[ "$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)" = \
  "plymouthd --mode=boot --attach-to-session " ] || exit 0

kill -TERM "$pid"
attempt=0
while [ "$attempt" -lt 20 ]; do
  [ ! -e "/proc/$pid" ] && ! fuser "$drm_device" >/dev/null 2>&1 && exit 0
  attempt=$((attempt + 1))
  sleep 0.25
done

printf 'luma-plymouth-release: verified PID %s did not release DRM after SIGTERM\n' \
  "$pid" >&2
exit 1
