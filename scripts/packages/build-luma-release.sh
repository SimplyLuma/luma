#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-release (ADR-040) as a noarch RPM in the Fedora builder.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

builder_container=$FEDORA_RPM_BUILD_CONTAINER
output_dir="$repo_root/build/packages/luma-release"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
COPYFILE_DISABLE=1 tar -C "$repo_root" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - src/luma-release scripts/os/lib/release_identity.py config/os/release.env |
  gzip -n >"$rpmbuild_dir/SOURCES/luma-release.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/luma-release.spec" "$rpmbuild_dir/SPECS/"

bash "$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install rpm-build python3
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-release.spec
    rpm -qp --provides RPMS/noarch/luma-release-*.rpm | grep -qx 'system-release(releasever) = 44'
    rpm -qp --provides RPMS/noarch/luma-release-*.rpm | grep -qx 'system-release(44)'
    rpm -qpl RPMS/noarch/luma-release-*.rpm | grep -qx /usr/lib/os-release
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
