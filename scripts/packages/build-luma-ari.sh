#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Ari build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the architecture-independent Ari RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Ari build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
builder_container=${LUMA_ARI_RPM_BUILD_CONTAINER:-$builder_container}

# Ari is built against the same pinned AppKit as the rest of the desktop.
# The platform is the release config/desktop/inputs.env pins for this
# architecture; it used to be a literal .62 here, which failed every build
# once the platform moved on.
case "$architecture" in
  x86_64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA ;;
  aarch64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA ;;
esac
platform_rpm="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS/$platform_nevra.rpm"
[ -f "$platform_rpm" ] || {
  printf 'error: build %s before Ari: %s\n' "$platform_nevra" "$platform_rpm" >&2
  exit 1
}
platform_rpm_name=$(basename "$platform_rpm")

output_dir="$repo_root/build/packages/luma-ari"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
# The tarball mirrors the repository (src/luma-ari, tests/luma-ari and
# tests/fixtures): the tests find the app and their fixtures by path from the
# repository root. The spec's %autosetup -n points into src/luma-ari.
source_dir="$work_dir/luma-ari/src/luma-ari"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
cp -R "$repo_root/src/luma-ari/." "$source_dir/"
mkdir -p "$work_dir/luma-ari/tests/fixtures"
cp -R "$repo_root/tests/luma-ari" "$work_dir/luma-ari/tests/luma-ari"
cp -R "$repo_root/tests/fixtures/ari-v70.json" "$repo_root/tests/fixtures/ari-v70-data" "$work_dir/luma-ari/tests/fixtures/"
mkdir -p "$work_dir/luma-ari/tools/lumaui-conform/scenarios"
cp "$repo_root/tools/lumaui-conform/scenarios/ari.json" "$work_dir/luma-ari/tools/lumaui-conform/scenarios/"
find "$work_dir/luma-ari" -type d -name __pycache__ -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-ari | gzip -n >"$rpmbuild_dir/SOURCES/luma-ari.tar.gz"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/packaging/rpm/luma-ari.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    # The builder mounts whichever build root it was created with, which is not
    # always this repository's own, so absolute /build paths do not survive a
    # nested checkout. Everything here is resolved from the working directory
    # the runner already places us in: <build root>/packages/<pkg>.work.X/rpmbuild
    dnf5 -q -y install --allowerasing \
      ../../luma-developer-platform/$architecture/RPMS/$platform_rpm_name \
      appstream desktop-file-utils gtk4 libadwaita libappstream-glib meson ninja-build \
      python3-devel python3-gobject rpm-build systemd-rpm-macros glib2-devel \
      xorg-x11-server-Xvfb dbus-daemon libXtst
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-ari.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Ari packages: %s\n' "$output_dir"
