#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/cage-source.env"

for tool in curl rpmbuild sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Luma Cage build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build Luma Cage on the Fedora AArch64 builder\n' >&2
  exit 1
}

output_dir="$repo_root/build/packages/cage/aarch64"
work_dir=$(mktemp -d "$repo_root/build/packages/cage.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
topdir="$work_dir/rpmbuild"
install -d -m 0755 "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

curl -fsSL "$LUMA_CAGE_URL" -o "$topdir/SOURCES/cage-$LUMA_CAGE_VERSION.tar.gz"
actual=$(sha256sum "$topdir/SOURCES/cage-$LUMA_CAGE_VERSION.tar.gz" | cut -d ' ' -f 1)
[ "$actual" = "$LUMA_CAGE_SHA256" ] || {
  printf 'error: Cage source hash mismatch: %s\n' "$actual" >&2
  exit 1
}
install -m 0644 "$repo_root/patches/cage/0001-luma-preserve-authenticated-scanout.patch" "$topdir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-cage.spec" "$topdir/SPECS/"

rpmbuild -ba --define "_topdir $topdir" \
  --define "use_source_date_epoch_as_buildtime 1" "$topdir/SPECS/luma-cage.spec"
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS" "$output_dir/SRPMS"
find "$topdir/RPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$topdir/SRPMS" -type f -name '*.rpm' -exec install -m 0644 {} "$output_dir/SRPMS/" \;
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
