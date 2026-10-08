#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-logos (ADR-040) as a noarch RPM in the Fedora builder.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

builder_container=$FEDORA_RPM_BUILD_CONTAINER
output_dir="$repo_root/build/packages/luma-logos"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/luma-logos"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
cp -R "$repo_root/src/luma-logos/." "$source_dir/"
cp "$repo_root/website/public/brand/luma-wordmark.svg" "$source_dir/"
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-logos | gzip -n >"$rpmbuild_dir/SOURCES/luma-logos.tar.gz"
install -m 0644 "$repo_root/packaging/rpm/luma-logos.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install python3 librsvg2-tools rpm-build
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-logos.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
