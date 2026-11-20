#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Make the luma-streamy source archive: the app plus the lumaframe GStreamer
# plugin with every crate vendored at the versions and checksums in its
# Cargo.lock, so the RPM builds offline. Needs cargo and network access for
# the download; run it in the RPM builder.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s OUTPUT.tar.gz\n' "$0" >&2
  exit 2
fi
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output=$(realpath -m "$1")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

source_dir="$work/luma-streamy"
mkdir -p "$source_dir"
(cd "$repo_root/src/luma-streamy" && tar --exclude=./gst-lumaframe/target --exclude=./gst-lumaframe/vendor \
  --exclude=__pycache__ -cf - .) | tar -C "$source_dir" -xf -
install -m 0644 "$repo_root/LICENSE.md" "$source_dir/LICENSE.md"
crate="$source_dir/gst-lumaframe"
[ -f "$crate/Cargo.lock" ] || { printf 'error: Cargo.lock is missing; the archive must pin every crate\n' >&2; exit 1; }
mkdir -p "$crate/.cargo"
(cd "$crate" && cargo vendor --locked --versioned-dirs vendor > .cargo/config.toml)
# A vendor step that fetched nothing is a failure, not an empty archive.
crates=$(find "$crate/vendor" -mindepth 1 -maxdepth 1 -type d | wc -l)
locked=$(grep -c '^source = "registry' "$crate/Cargo.lock")
if [ "$crates" -lt "$locked" ] || [ "$crates" -eq 0 ]; then
  printf 'error: vendored %s crates but Cargo.lock names %s from the registry\n' "$crates" "$locked" >&2
  exit 1
fi
grep -q 'vendored-sources' "$crate/.cargo/config.toml" || { printf 'error: cargo vendor wrote no source replacement\n' >&2; exit 1; }
source_date_epoch=${SOURCE_DATE_EPOCH:-0}
tar --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner \
  -C "$work" -cf - luma-streamy | gzip -n >"$output"
printf 'vendored %s crates\n' "$crates"
sha256sum "$output"
