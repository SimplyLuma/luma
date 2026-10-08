#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in git gzip rpmbuild sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required native IMS build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || {
  printf 'error: build the FP6 IMS RPM on the Fedora AArch64 builder\n' >&2
  exit 1
}

source_dir="$repo_root/src/imsd"
[ -f "$source_dir/LUMA-PROVENANCE.md" ] || {
  printf 'error: canonical IMS source or provenance is missing\n' >&2
  exit 1
}

output_dir="$repo_root/build/packages/luma-imsd/aarch64"
work_dir=$(mktemp -d "$repo_root/build/packages/luma-imsd.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
archive_root="$work_dir/luma-imsd-$LUMA_IMSD_VERSION"
install -d -m 0755 "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} \
  "$archive_root"
cp -R "$source_dir/." "$archive_root/"
find "$archive_root" -type d -name build -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - "luma-imsd-$LUMA_IMSD_VERSION" | \
  gzip -n >"$rpmbuild_dir/SOURCES/luma-imsd-$LUMA_IMSD_VERSION.tar.gz"

install -m 0644 "$repo_root/scripts/mobile/luma-fp6-imsd.service" \
  "$rpmbuild_dir/SOURCES/"
install -m 0755 "$repo_root/scripts/mobile/luma-fp6-ims-pdn-up.sh" \
  "$rpmbuild_dir/SOURCES/"
install -m 0755 "$repo_root/scripts/mobile/luma-fp6-ims-state-guard" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/scripts/mobile/net.catcrafts.IMS1-luma.conf" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-imsd.spec" \
  "$rpmbuild_dir/SPECS/"

rpmbuild -ba --define "_topdir $rpmbuild_dir" \
  --define "use_source_date_epoch_as_buildtime 1" \
  "$rpmbuild_dir/SPECS/luma-imsd.spec"

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS" "$output_dir/SRPMS"
find "$rpmbuild_dir/RPMS" -type f -name '*.rpm' \
  -exec install -m 0644 {} "$output_dir/RPMS/" \;
find "$rpmbuild_dir/SRPMS" -type f -name '*.rpm' \
  -exec install -m 0644 {} "$output_dir/SRPMS/" \;
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)
printf 'Luma native IMS packages: %s\n' "$output_dir"
