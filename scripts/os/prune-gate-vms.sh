#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Retention for the VM disks a failed gate leaves behind for diagnosis.
#
#   prune-gate-vms.sh [--apply] [--keep N]
#
# gate.sh keeps $LUMA_OS_ROOT/vm/gate-<build>-<run> (about 13 GB of qcow2) and
# its SSH key whenever a gate fails. The old rule removed them only after two
# days, so two failed nightlies in a row filled the release volume on
# 2026-09-21 and the third could not start. The nightly needs 42 GiB free and
# the volume has about 50, so one kept set fits and two do not.
#
# Keeps the newest N finished sets (default 1) and every set that is still
# running; removes the rest. A set is running when a libvirt domain named for
# its run is in any state but "shut off", or when the gate lock is held and it
# is the newest set (gate.sh creates its directory, then takes the lock, then
# defines its domains, so during setup the lock is the only evidence). Only
# directories named gate-<build>-<YYYYMMDDTHHMMSSZ> are considered; anything
# else under vm/ is reported and left alone. Default is a dry run.
#
# Environment (for the test harness): LUMA_OS_ROOT, LUMA_OS_GATE_KEY_ROOT
# (default /root/.ssh), LUMA_OS_VIRSH (default virsh).

set -euo pipefail

apply=0
keep=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift ;;
    --keep) keep=${2:?}; shift 2 ;;
    *) printf 'usage: %s [--apply] [--keep N]\n' "$0" >&2; exit 2 ;;
  esac
done
case "$keep" in '' | *[!0-9]*) printf 'error: --keep needs a number\n' >&2; exit 2 ;; esac

root=${LUMA_OS_ROOT:-/mnt/luma-secondary/luma-build/os-release/fs}
key_root=${LUMA_OS_GATE_KEY_ROOT:-/root/.ssh}
virsh=${LUMA_OS_VIRSH:-virsh}
vm="$root/vm"
lock="$root/locks/gate.lock"

log() { printf '%s os-release: gate retention: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

[ -d "$vm" ] || { log "no $vm; nothing to do"; exit 0; }

# Domains that are not shut off, by the run id their names end with.
declare -A live_run=()
while read -r domain; do
  case "$domain" in luma-os-gate-*) ;; *) continue ;; esac
  state=$("$virsh" domstate "$domain" 2>/dev/null || echo unknown)
  [ "$state" = 'shut off' ] && continue
  live_run[${domain##*-}]=$domain
done < <("$virsh" list --all --name 2>/dev/null || true)

lock_held=0
if [ -e "$lock" ]; then
  exec 8<"$lock"
  if flock -n 8; then flock -u 8; else lock_held=1; fi
  exec 8<&-
fi

# Newest first, by the run id (a UTC timestamp), not by the build id: build ids
# like 20260921.10 sort before 20260921.9 as text.
sets=()
while IFS= read -r -d '' dir; do
  name=${dir##*/}
  run=${name##*-}
  if [ -L "$dir" ] || ! [[ "$name" =~ ^gate-[0-9]{8}\.[0-9]+-[0-9]{8}T[0-9]{6}Z$ ]]; then
    log "left alone (not a gate VM set): $dir"
    continue
  fi
  sets+=("$run $dir")
done < <(find "$vm" -mindepth 1 -maxdepth 1 -name 'gate-*' -print0)
mapfile -t sets < <(printf '%s\n' "${sets[@]}" | sed '/^$/d' | sort -r)

kept=0 removed=0 newest=1
for entry in "${sets[@]}"; do
  run=${entry%% *} dir=${entry#* }
  if [ -n "${live_run[$run]:-}" ]; then
    log "kept (running: ${live_run[$run]}): $dir"
  elif [ "$newest" -eq 1 ] && [ "$lock_held" -eq 1 ]; then
    log "kept (gate lock held; newest set may be setting up): $dir"
  elif [ "$kept" -lt "$keep" ]; then
    kept=$((kept + 1))
    log "kept (newest finished set $kept of $keep): $dir"
  else
    size=$(du -sh "$dir" 2>/dev/null | cut -f1)
    if [ "$apply" -eq 1 ]; then
      rm -rf -- "$dir"
      [ ! -d "$key_root/luma-os-gate-$run" ] || rm -rf -- "$key_root/luma-os-gate-$run"
      log "removed ${size:-?}: $dir"
    else
      log "would remove ${size:-?}: $dir"
    fi
    removed=$((removed + 1))
  fi
  newest=0
done
log "${#sets[@]} set(s): $removed $([ "$apply" -eq 1 ] && echo removed || echo 'would be removed')"
