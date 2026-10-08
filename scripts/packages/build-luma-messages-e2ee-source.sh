#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Make the luma-messages-e2ee source archive: the crate plus every dependency
# vendored at the versions and checksums in Cargo.lock, so the RPM builds
# offline. Needs cargo and network access for the download; run it in the RPM
# builder.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s OUTPUT.tar.gz\n' "$0" >&2
  exit 2
fi
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output=$(realpath -m "$1")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

source_dir="$work/luma-messages-e2ee"
mkdir -p "$source_dir"
(cd "$repo_root/src/luma-messages-e2ee" && tar --exclude=./target --exclude=./vendor -cf - .) | tar -C "$source_dir" -xf -
[ -f "$source_dir/Cargo.lock" ] || { printf 'error: Cargo.lock is missing; the archive must pin every crate\n' >&2; exit 1; }
mkdir -p "$source_dir/.cargo"
(cd "$source_dir" && cargo vendor --locked --versioned-dirs vendor > .cargo/config.toml)
# A vendor step that fetched nothing is a failure, not an empty archive.
crates=$(find "$source_dir/vendor" -mindepth 1 -maxdepth 1 -type d | wc -l)
locked=$(grep -c '^source = "registry' "$source_dir/Cargo.lock")
if [ "$crates" -lt "$locked" ] || [ "$crates" -eq 0 ]; then
  printf 'error: vendored %s crates but Cargo.lock names %s from the registry\n' "$crates" "$locked" >&2
  exit 1
fi
grep -q 'vendored-sources' "$source_dir/.cargo/config.toml" || { printf 'error: cargo vendor wrote no source replacement\n' >&2; exit 1; }
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
  -C "$work" -czf "$output" luma-messages-e2ee
printf 'vendored %s crates\n' "$crates"
sha256sum "$output"
