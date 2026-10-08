#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Switch the locally served copy of the published OS content to a new,
# complete generation (ADR-015's immutable-generation rule, kept).
#
#   stage-webroot.sh [--keep N]
#
# A generation is $LUMA_OS_ROOT/webroot/generations/<UTC time>/ laid out like
# the object storage prefixes (config/os/release.env):
#   os/repo           the public repository (stable)
#   os/preview-repo   the preview repository (nightly, beta)
#   os/graph          signed update graphs
#   os/keys           public keys
# Files are hard links to the staging repositories. Every channel ref
# in the generation is checked against the staging repositories before
# `current` is switched with one rename, so a reader never sees a partial copy.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

keep=3
case "${1:-}" in
  --keep) keep=${2:?} ;;
  '') ;;
  *) printf 'usage: %s [--keep N]\n' "$0" >&2; exit 2 ;;
esac
luma_os_require_tools cp ostree
webroot="$LUMA_OS_ROOT/webroot"
generations="$webroot/generations"
install -d -m 0755 "$webroot" "$generations"
exec 5>"$LUMA_OS_ROOT/locks/webroot.lock"
flock -w 600 5 || luma_os_die 'webroot is locked'

previous=
[ -L "$webroot/current" ] && previous=$(readlink -f "$webroot/current")
generation="$generations/$(date -u +%Y%m%dT%H%M%SZ)"
[ ! -e "$generation" ] || luma_os_die "generation exists: $generation"
staging="$generation.partial"
# A generation interrupted earlier is never served; remove it.
find "$generations" -mindepth 1 -maxdepth 1 -type d -name '*.partial' -exec rm -rf -- {} +
install -d -m 0755 "$staging/os"

# Hard links from the staging repositories (same filesystem). OSTree writes
# objects once and replaces mutable files (summary, refs) by rename, so a
# generation keeps exactly the files it was made from while staging moves on.
# (rsync is not used: under SELinux the service context it transitions to may
# not read the unlabeled pipeline volume.)
copy() {
  local source=$1 name=$2
  [ -d "$source" ] || return 0
  cp -al "$source" "$staging/os/$name"
  rm -rf "$staging/os/$name/tmp" "$staging/os/$name/.lock" "$staging/os/$name"/.luma-* 2>/dev/null || true
}
copy "$LUMA_OS_ROOT/publish/public-repo" repo
copy "$LUMA_OS_ROOT/publish/preview-repo" preview-repo
copy "$LUMA_OS_ROOT/publish/graph" graph
install -d -m 0755 "$staging/os/keys"
install -m 0644 "$LUMA_OS_KEYS/luma-os-release.gpg" "$LUMA_OS_KEYS/luma-os-release.asc" "$staging/os/keys/"
install -m 0644 "$luma_os_repo_root/config/os/keys/luma-update-graph.pub" "$staging/os/keys/"

for pair in "public-repo:repo" "preview-repo:preview-repo"; do
  source="$LUMA_OS_ROOT/publish/${pair%%:*}"
  target="$staging/os/${pair#*:}"
  [ -f "$source/config" ] || continue
  while read -r ref; do
    [ -n "$ref" ] || continue
    [ "$(ostree rev-parse --repo="$target" "$ref")" = "$(ostree rev-parse --repo="$source" "$ref")" ] ||
      luma_os_die "generation copy of $ref differs from staging"
  done < <(ostree refs --repo="$source" | grep "^$LUMA_OS_REF_PREFIX/")
  cmp -s "$source/summary" "$target/summary" && cmp -s "$source/summary.sig" "$target/summary.sig" ||
    luma_os_die "generation summary of ${pair#*:} differs from staging"
done

mv "$staging" "$generation"
ln -sfn "generations/$(basename "$generation")" "$webroot/.current.new"
mv -Tf "$webroot/.current.new" "$webroot/current"
luma_os_log "webroot now serves $(basename "$generation")"

mapfile -t old < <(find "$generations" -mindepth 1 -maxdepth 1 -type d ! -name '*.partial' | LC_ALL=C sort | head -n -"$keep")
for dir in "${old[@]}"; do
  [ "$(readlink -f "$webroot/current")" = "$dir" ] || rm -rf -- "$dir"
done
