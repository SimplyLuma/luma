#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
python3 "$repo_root/scripts/developer/generate-home-widget-styles.py" --check
output_dir="$repo_root/build/packages/luma-home-widgets"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
stage="$work_dir/luma-home-widgets-0.1.0"
cp -a "$repo_root/src/luma-home-widgets" "$stage"
tar -czf "$rpmbuild_dir/SOURCES/luma-home-widgets-0.1.0.tar.gz" -C "$work_dir" luma-home-widgets-0.1.0
install -m 0644 "$repo_root/packaging/rpm/luma-home-widgets.spec" "$rpmbuild_dir/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
  set -euo pipefail
  dnf5 -y install gcc meson glib2-devel json-glib-devel gobject-introspection-devel gtk3-devel python3-gobject-base dbus-daemon rpm-build
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-home-widgets.spec
'
mkdir -p "$output_dir"
cp -a "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SRPMS" "$output_dir/"
