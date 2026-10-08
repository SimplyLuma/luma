#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  printf 'usage: %s --repo PATH --webroot PATH\n' "$0" >&2
  exit 2
}

source_repo=
webroot=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) source_repo=${2:-}; shift 2 ;;
    --webroot) webroot=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$source_repo" ] && [ -n "$webroot" ] || usage

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/update/recent.env"
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

for tool in flock ostree readlink realpath rsync; do
  command -v "$tool" >/dev/null 2>&1 || fail "required activation tool is missing: $tool"
done
[ -f "$source_repo/config" ] || fail "publication repository is invalid: $source_repo"
[ -s "$source_repo/summary" ] && [ -s "$source_repo/summary.sig" ] ||
  fail 'publication repository has no signed summary'
[ -s "$source_repo/luma/luma-recent.gpg" ] || fail 'publication repository has no public key'
commit=$(ostree rev-parse --repo="$source_repo" "$LUMA_UPDATE_REF")
[[ "$commit" =~ ^[0-9a-f]{64}$ ]] || fail 'Recent does not resolve to a commit'
[ -s "$source_repo/luma/releases/$commit.json" ] || fail 'release manifest is missing'
[ -s "$source_repo/luma/releases/$commit.json.asc" ] || fail 'release signature is missing'
ostree fsck --repo="$source_repo" --quiet

mkdir -p "$webroot/generations"
exec 9>"$webroot/.luma-activate.lock"
flock -n 9 || fail 'another webroot activation is active'
destination="$webroot/generations/$commit"
partial="$webroot/generations/.$commit.partial"
[ ! -e "$partial" ] || fail "stale partial generation requires review: $partial"

if [ ! -d "$destination" ]; then
  link_dest=()
  if [ -L "$webroot/current" ]; then
    previous=$(realpath "$webroot/current")
    case "$previous" in
      "$webroot"/generations/*) link_dest=("--link-dest=$previous") ;;
      *) fail 'current points outside the managed generations directory' ;;
    esac
  fi
  mkdir "$partial"
  cleanup() { rm -rf -- "$partial"; }
  trap cleanup EXIT INT TERM
  rsync -aH --delete "${link_dest[@]}" "$source_repo/" "$partial/"
  ostree fsck --repo="$partial" --quiet
  test "$(ostree rev-parse --repo="$partial" "$LUMA_UPDATE_REF")" = "$commit" ||
    fail 'copied generation resolves an unexpected commit'
  mv "$partial" "$destination"
  trap - EXIT INT TERM
fi
# Update archives contain public, signed distribution material. Keep secrets
# elsewhere and make every served generation traversable/readable without
# granting nginx access to the mutable staging or build configuration trees.
chmod -R a+rX "$destination"

next_link="$webroot/.current.$commit"
ln -s "generations/$commit" "$next_link"
mv -Tf "$next_link" "$webroot/current"
printf 'active_generation=%s\nactive_path=%s\n' "$commit" "$webroot/current"
