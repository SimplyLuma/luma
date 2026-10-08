#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run an OS pipeline command in a transient systemd unit with the shared build
# host's resource caps (the same budget /srv/luma-build/bin/luma-build-run
# gives Luma package builds), so an image build or VM gate can never starve
# the other work on the machine.
#
#   run-capped.sh [--wait] [--name NAME] [--log FILE] COMMAND [ARG...]
#
# Without --wait the unit starts in the background and its name is printed;
# follow it with `journalctl -u NAME -f` or the log file.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

wait_flag=()
name="luma-os-$(date -u +%Y%m%dT%H%M%SZ)"
log=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --wait) wait_flag=(--wait); shift ;;
    --name) name=${2:?}; shift 2 ;;
    --log) log=${2:?}; shift 2 ;;
    --) shift; break ;;
    -*) printf 'usage: %s [--wait] [--name NAME] [--log FILE] COMMAND [ARG...]\n' "$0" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ "$#" -gt 0 ] || { printf 'usage: %s [--wait] [--name NAME] [--log FILE] COMMAND [ARG...]\n' "$0" >&2; exit 2; }
luma_os_require_root
luma_os_check_host

# Leave the machine at least 8 GiB, as the shared runner does.
available=$(awk '/^MemAvailable:/ { print int($2 / 1048576) }' /proc/meminfo)
[ "${available:-0}" -ge 12 ] || luma_os_die "only ${available} GiB of memory is available; refusing to start"

output=(--property=StandardOutput=journal --property=StandardError=journal)
command=("$@")
if [ -n "$log" ]; then
  # systemd itself may not write to the unlabeled pipeline volume under
  # SELinux; the unit's own shell opens the log instead.
  install -d -m 0755 "$(dirname "$log")"
  command=(/usr/bin/bash -c 'log=$1; shift; exec >>"$log" 2>&1; exec "$@"' luma-os-log "$log" "$@")
fi

# The caps live on luma-os.slice, which also holds every container the
# pipeline starts (lib/common.sh), so scripts and containers share one budget.
luma_os_ensure_slice
exec systemd-run --system --collect "${wait_flag[@]}" \
  --unit="$name" \
  --slice="$LUMA_OS_SLICE" \
  --description="Luma OS release pipeline: $*" \
  --property=Nice=10 \
  --property=KillMode=control-group \
  "${output[@]}" \
  --setenv=LUMA_OS_ROOT="$LUMA_OS_ROOT" \
  --setenv=TMPDIR="$LUMA_OS_ROOT/tmp" \
  --setenv=PATH=/usr/local/bin:/usr/bin:/usr/sbin \
  --working-directory=/ \
  -- "${command[@]}"
