#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Remove a candidate that did not pass its gate from the candidate repository,
# so no signed-but-unhealthy commit lingers anywhere. The build directory, its
# logs and gate evidence stay (scripts/os/prune.sh ages them out).
#
#   discard-candidate.sh --build-id YYYYMMDD.N

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

case "${1:-}" in
  --build-id) build_id=${2:?} ;;
  *) printf 'usage: %s --build-id ID\n' "$0" >&2; exit 2 ;;
esac
luma_os_require_root
build_dir="$LUMA_OS_ROOT/builds/$build_id"
[ -s "$build_dir/export.env" ] || { luma_os_log "no candidate for $build_id"; exit 0; }
luma_os_load_env "$build_dir/export.env"
repo="$LUMA_OS_ROOT/ostree/candidate-repo"
channel_repo=$(luma_os_channel_repo "$(sed -n 's/^LUMA_OS_CHANNEL=//p' "$build_dir/build.env")")
if [ -f "$channel_repo/config" ] && ostree show --repo="$channel_repo" "$LUMA_EXPORT_CANDIDATE" >/dev/null 2>&1 &&
   [ -s "$build_dir/publish.env" ]; then
  luma_os_die "$LUMA_EXPORT_CANDIDATE is published; it cannot be discarded"
fi
exec 8>"$LUMA_OS_ROOT/locks/candidate-repo.lock"
flock -w 3600 8 || luma_os_die 'candidate repository is locked'
if [ "$(ostree rev-parse --repo="$repo" "$LUMA_EXPORT_REF" 2>/dev/null || true)" = "$LUMA_EXPORT_CANDIDATE" ]; then
  if [ -n "$LUMA_EXPORT_PARENT" ] && ostree show --repo="$repo" "$LUMA_EXPORT_PARENT" >/dev/null 2>&1; then
    ostree refs --repo="$repo" --create="$LUMA_EXPORT_REF" --force "$LUMA_EXPORT_PARENT"
  else
    ostree refs --repo="$repo" --delete "$LUMA_EXPORT_REF"
  fi
fi
ostree prune --repo="$repo" --delete-commit="$LUMA_EXPORT_CANDIDATE" >/dev/null 2>&1 || true
ostree prune --repo="$repo" --refs-only >/dev/null
printf 'LUMA_DISCARDED_UTC=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$build_dir/export.env"
luma_os_log "discarded candidate $LUMA_EXPORT_CANDIDATE of $build_id"
