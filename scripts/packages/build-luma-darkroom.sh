#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Darkroom build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
  printf 'error: build the architecture-independent Darkroom RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
}

platform_rpm="$repo_root/build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
if [ ! -f "$platform_rpm" ]; then
  "$repo_root/scripts/packages/build-luma-developer-platform.sh"
fi
# The window's LumaUI glyphs resolve through the Prairie icon theme on a Luma
# desktop, and %check fails on any icon that does not resolve, so the pinned
# theme is installed in the builder too.
prairie_rpm="$repo_root/build/packages/prairie-icon-theme/RPMS/noarch/$PRAIRIE_ICON_THEME_NEVRA.rpm"
if [ ! -f "$prairie_rpm" ]; then
  "$repo_root/scripts/packages/build-prairie-icon-theme.sh"
fi
[ -f "$prairie_rpm" ] || {
  printf 'error: %s is missing: %s\n' "$PRAIRIE_ICON_THEME_NEVRA" "$prairie_rpm" >&2
  exit 1
}

output_dir="$repo_root/build/packages/luma-darkroom"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
builder_rpmbuild_dir="/build/${rpmbuild_dir#"$repo_root/build/"}"
source_dir="$work_dir/luma-darkroom"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir" "$rpmbuild_dir/platform"
install -m 0644 "$platform_rpm" "$rpmbuild_dir/platform/"
install -m 0644 "$prairie_rpm" "$rpmbuild_dir/platform/"
cp -R "$repo_root/src/luma-darkroom/." "$source_dir/"
mkdir -p "$source_dir/fixtures"
install -m 0644 "$repo_root/tests/fixtures/darkroom-v70.json" "$source_dir/fixtures/"
cp -R "$repo_root/tests/fixtures/darkroom-v70" "$source_dir/fixtures/"
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-darkroom | gzip -n >"$rpmbuild_dir/SOURCES/luma-darkroom.tar.gz"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-darkroom.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" "
    set -euo pipefail
    dnf5 -q -y install \
      \$PWD/platform/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm \
      \$PWD/platform/$PRAIRIE_ICON_THEME_NEVRA.rpm \
      adwaita-icon-theme appstream dbus-daemon desktop-file-utils gtk4 libadwaita python3-devel \
      python3-gobject python3-pillow python3-numpy LibRaw rpm-build shared-mime-info xorg-x11-server-Xvfb
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-darkroom.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Darkroom packages: %s\n' "$output_dir"

