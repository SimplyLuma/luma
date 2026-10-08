#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Retention for what the OS pipeline itself creates on the build host.
#
#   prune.sh [--apply] [--keep-builds N] [--keep-images N] [--keep-nightlies N]
#            [--keep-failed-gates N] [--trim]
#
# Default is a dry run that lists what would go. It only ever touches:
#   * $LUMA_OS_ROOT/builds/<YYYYMMDD.N> beyond the newest N (default 5), never
#     one whose commit is a channel head;
#   * localhost/luma-os/desktop:<build> images of pruned builds and of all but
#     the newest N kept builds (default 1; an exported build needs its image
#     only to be exported again), and dangling
#     layers, in the pipeline's own container storage (never shared storage,
#     never `podman system prune`);
#   * gate VMs named luma-os-gate-* that are shut off, and the disks failed
#     gates kept under $LUMA_OS_ROOT/vm beyond the newest N finished sets
#     (default 1; never a running gate; scripts/os/prune-gate-vms.sh);
#   * candidate commits older than 3 days that were never published;
#   * nightly history in the preview repository beyond the newest N releases
#     (default 14); beta and stable history is never pruned automatically;
#   * pipeline logs older than 30 days;
# and trims the volume so freed blocks return to the shared disk.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

apply=0
keep_builds=5
keep_images=1
keep_nightlies=5
keep_failed_gates=1
trim=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift ;;
    --keep-builds) keep_builds=${2:?}; shift 2 ;;
    --keep-images) keep_images=${2:?}; shift 2 ;;
    --keep-nightlies) keep_nightlies=${2:?}; shift 2 ;;
    --keep-failed-gates) keep_failed_gates=${2:?}; shift 2 ;;
    --trim) trim=1; shift ;;
    *) printf 'usage: %s [--apply] [--keep-builds N] [--keep-images N] [--keep-nightlies N] [--keep-failed-gates N] [--trim]\n' "$0" >&2; exit 2 ;;
  esac
done
luma_os_require_root
do_or_say() {
  if [ "$apply" -eq 1 ]; then "$@"; else printf 'would run:'; printf ' %q' "$@"; printf '\n'; fi
}

heads=()
for channel in $LUMA_OS_CHANNELS; do
  repo=$(luma_os_channel_repo "$channel")
  [ -f "$repo/config" ] || continue
  head=$(ostree rev-parse --repo="$repo" "$(luma_os_channel_ref "$channel")" 2>/dev/null || true)
  [ -n "$head" ] && heads+=("$head")
done

# Builds.
mapfile -t builds < <(find "$LUMA_OS_ROOT/builds" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
  -regex '.*/[0-9]{8}\.[0-9]+' -printf '%f\n' 2>/dev/null | sort -t. -k1,1n -k2,2n)
count=${#builds[@]}
for ((i = 0; i < count - keep_builds; i++)); do
  id=${builds[$i]}
  commit=$(sed -n 's/^LUMA_EXPORT_CANDIDATE=//p' "$LUMA_OS_ROOT/builds/$id/export.env" 2>/dev/null || true)
  if [ -n "$commit" ] && printf '%s\n' "${heads[@]}" | grep -Fxq "$commit"; then
    continue
  fi
  if luma_os_podman image exists "$LUMA_OS_IMAGE_NAME:$id"; then
    do_or_say luma_os_podman rmi "$LUMA_OS_IMAGE_NAME:$id"
  fi
  do_or_say rm -rf -- "$LUMA_OS_ROOT/builds/$id"
done
# A desktop image is 11 GB. Once exported it is needed only to export that
# build again (--resume-build), so only the newest keep_images keep theirs;
# five kept images filled the volume before the 20260918.1 gate.
for ((i = 0; i < count - keep_images; i++)); do
  id=${builds[$i]}
  [ -d "$LUMA_OS_ROOT/builds/$id" ] || continue
  if luma_os_podman image exists "$LUMA_OS_IMAGE_NAME:$id"; then
    do_or_say luma_os_podman rmi "$LUMA_OS_IMAGE_NAME:$id"
  fi
done
# OCI layouts are only needed until the export has run.
for id in "${builds[@]}"; do
  if [ -s "$LUMA_OS_ROOT/builds/$id/export.env" ] && [ -d "$LUMA_OS_ROOT/builds/$id/oci" ]; then
    do_or_say rm -rf -- "$LUMA_OS_ROOT/builds/$id/oci"
  fi
done
mapfile -t dangling < <(luma_os_podman images --quiet --filter dangling=true 2>/dev/null | sort -u)
for image in "${dangling[@]}"; do
  do_or_say luma_os_podman rmi "$image"
done

# Gate VMs.
while read -r domain; do
  case "$domain" in luma-os-gate-*) ;; *) continue ;; esac
  [ "$(virsh domstate "$domain" 2>/dev/null)" = 'shut off' ] || continue
  do_or_say virsh undefine "$domain" --nvram
done < <(virsh list --all --name 2>/dev/null)
# Disks a failed gate kept for diagnosis: the newest finished set stays, a
# running gate is never touched, the rest go (prune-gate-vms.sh says why).
if [ "$apply" -eq 1 ]; then
  LUMA_OS_ROOT=$LUMA_OS_ROOT "$(dirname -- "$0")/prune-gate-vms.sh" --apply --keep "$keep_failed_gates"
else
  LUMA_OS_ROOT=$LUMA_OS_ROOT "$(dirname -- "$0")/prune-gate-vms.sh" --keep "$keep_failed_gates"
fi

# Media from failed nightlies. A medium that failed verification is never
# published or served, yet its ISO (6 GB) stayed on the release volume: two
# failures in a row (20260925.1, 20260926.1) left 12 GB behind and the next
# run's space check refused. Drop only the ISO of a medium whose result.json
# says failed and unpublished; its result, logs and verification evidence stay.
while read -r result; do
  dir=${result%/result.json}
  python3 - "$result" <<'PY' || continue
import json, sys
r = json.load(open(sys.argv[1]))
sys.exit(0 if r.get("state") == "failed" and r.get("published") is False else 1)
PY
  for iso in "$dir"/*.iso; do
    [ -f "$iso" ] && do_or_say rm -f -- "$iso"
  done
done < <(find "$LUMA_OS_ROOT/media" -mindepth 3 -maxdepth 3 -path '*/media/*/*/result.json' 2>/dev/null)

# Repositories.
if [ -f "$LUMA_OS_ROOT/ostree/candidate-repo/config" ]; then
  do_or_say ostree prune --repo="$LUMA_OS_ROOT/ostree/candidate-repo" --refs-only --keep-younger-than='3 days ago'
fi
if [ -f "$LUMA_OS_ROOT/ostree/import/config" ]; then
  do_or_say ostree prune --repo="$LUMA_OS_ROOT/ostree/import" --refs-only
fi
preview=$(luma_os_channel_repo nightly)
if [ -f "$preview/config" ]; then
  nightly_ref=$(luma_os_channel_ref nightly)
  history=0
  walk=$(ostree rev-parse --repo="$preview" "$nightly_ref" 2>/dev/null || true)
  while [ -n "$walk" ]; do
    history=$((history + 1))
    walk=$(ostree rev-parse --repo="$preview" "$walk^" 2>/dev/null || true)
  done
  if [ "$history" -gt "$keep_nightlies" ]; then
    # --only-branch limits depth pruning to nightly; beta keeps all history,
    # and any object a beta commit still uses is kept.
    do_or_say ostree prune --repo="$preview" --refs-only \
      --only-branch="$nightly_ref" --depth=$((keep_nightlies - 1))
  fi
fi

# Static deltas the delta contract no longer wants (release.env: deltas from
# the previous LUMA_OS_DELTA_PREDECESSORS heads, a from-empty delta only for the
# head when LUMA_OS_EMPTY_DELTA=1), and deltas to or from commits retention
# removed. A summary lists the deltas, so any change re-signs it.
for channel_repo in "$(luma_os_channel_repo nightly)" "$(luma_os_channel_repo stable)"; do
  [ -f "$channel_repo/config" ] || continue
  mapfile -t wanted < <(
    for ref in $(ostree refs --repo="$channel_repo"); do
      commit=$(ostree rev-parse --repo="$channel_repo" "$ref")
      [ "$LUMA_OS_EMPTY_DELTA" = 1 ] && printf '%s\n' "$commit"
      # Every commit keeps the delta from its own parent(s), so a device a few
      # releases behind still updates release by release.
      while [ -n "$commit" ]; do
        parent=$(ostree rev-parse --repo="$channel_repo" "$commit^" 2>/dev/null || true)
        n=0 base=$parent
        while [ -n "$base" ] && [ "$n" -lt "$LUMA_OS_DELTA_PREDECESSORS" ]; do
          ostree ls --repo="$channel_repo" "$base" / >/dev/null 2>&1 && printf '%s-%s\n' "$base" "$commit"
          n=$((n + 1))
          base=$(ostree rev-parse --repo="$channel_repo" "$base^" 2>/dev/null || true)
        done
        ostree ls --repo="$channel_repo" "$parent" / >/dev/null 2>&1 || break
        commit=$parent
      done
    done | sort -u)
  changed=0
  while read -r delta; do
    [ -n "$delta" ] || continue
    printf '%s\n' "${wanted[@]}" | grep -Fxq "$delta" && continue
    do_or_say ostree static-delta delete --repo="$channel_repo" "$delta"
    changed=1
  done < <(ostree static-delta list --repo="$channel_repo" 2>/dev/null)
  if [ "$changed" -eq 1 ] && [ "$apply" -eq 1 ]; then
    luma_os_gpg_unlock
    ostree summary --repo="$channel_repo" --update \
      --gpg-sign="$(luma_os_gpg_fingerprint)" --gpg-homedir="$(luma_os_gpg_home)" \
      --add-metadata="org.projectluma.generated-utc='$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
    luma_os_gpg_lock
    luma_os_log "static deltas pruned and summary re-signed: $channel_repo"
  fi
done

# Logs.
while read -r file; do
  do_or_say rm -f -- "$file"
done < <(find "$LUMA_OS_ROOT/logs" -type f -mtime +30 2>/dev/null)
# Freed blocks are trimmed only with --trim: the volume is a sparse image on a
# disk other work shares, and blocks handed back may be taken by others. The
# nightly trims before and after every run, so the shared disk is not filled
# by blocks the volume no longer uses, and its preflight refuses to start when
# the disk cannot hold the run (luma_os_check_space, with a host reserve).
if [ "$apply" -eq 1 ] && [ "$trim" -eq 1 ] && mountpoint -q "$LUMA_OS_ROOT"; then
  fstrim "$LUMA_OS_ROOT" >/dev/null 2>&1 || true
fi
[ "$apply" -eq 1 ] && luma_os_log 'prune complete' || true
