#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the Streamy RPM (the app and its lumaframe GStreamer plugin) for the
# native architecture in a Fedora builder container.
#   scripts/packages/build-luma-streamy.sh
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER
    platform_nevra=$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA ;;
  aarch64)
    builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64
    platform_nevra=$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA ;;
  *) printf 'error: unsupported Streamy build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture
spec="$repo_root/packaging/rpm/luma-streamy.spec"
# %check imports and opens the app, which needs the pinned Luma Developer Platform.
platform_rpm="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS/$platform_nevra.rpm"
[ -f "$platform_rpm" ] || {
  printf 'error: build or admit %s before Streamy: %s\n' "$platform_nevra" "$platform_rpm" >&2
  exit 1
}
output_dir="$repo_root/build/packages/luma-streamy"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
chmod 0755 "$work_dir"
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,platform}
mkdir -p "$rpmbuild_dir/source-repo/src" "$rpmbuild_dir/source-repo/scripts/packages"
cp -R "$repo_root/src/luma-streamy" "$rpmbuild_dir/source-repo/src/"
rm -rf "$rpmbuild_dir/source-repo/src/luma-streamy/gst-lumaframe/target"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/source-repo/"
install -m 0755 "$repo_root/scripts/packages/build-luma-streamy-source.sh" \
  "$rpmbuild_dir/source-repo/scripts/packages/"
install -m 0644 "$spec" "$rpmbuild_dir/SPECS/"
cp "$platform_rpm" "$rpmbuild_dir/platform/"
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" "$builder_container" "
  set -euo pipefail
  dnf5 -q -y install --allowerasing platform/*.rpm rpm-build >/dev/null
  # Everything else the spec declares, so this list cannot fall behind it.
  rpmspec -q --buildrequires SPECS/luma-streamy.spec | xargs -r -d '\n' dnf5 -q -y install
  SOURCE_DATE_EPOCH=$source_date_epoch source-repo/scripts/packages/build-luma-streamy-source.sh \"\$PWD/SOURCES/luma-streamy.tar.gz\"
  rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-streamy.spec
"
version=$(awk '/^Version:/ {print $2; exit}' "$spec")
release=$(awk '/^Release:/ {sub(/%\{\?dist\}/, "", $2); print $2; exit}' "$spec")
expected="$version-$release.fc44.$architecture"
rpm="$rpmbuild_dir/RPMS/$architecture/luma-streamy-$expected.rpm"
srpm="$rpmbuild_dir/SRPMS/luma-streamy-$version-$release.fc44.src.rpm"
test -f "$rpm" && test -f "$srpm" || { printf 'error: expected Streamy artifacts are missing\n' >&2; exit 1; }
test "$(rpm -qp --qf '%{VERSION}-%{RELEASE}.%{ARCH}' "$rpm")" = "$expected" || {
  printf 'error: Streamy RPM header differs from its spec\n' >&2; exit 1;
}
# The plugin is the app's renderer: an RPM without it is not Streamy.
files=$(rpm -qlp "$rpm")
grep -qx '/usr/lib64/gstreamer-1.0/libgstlumavideofx.so' <<<"$files" || {
  printf 'error: the Streamy RPM does not ship the lumaframe plugin\n' >&2; exit 1;
}
rm -rf "$output_dir/RPMS" "$output_dir/SRPMS" "$output_dir/SHA256SUMS"
install -d "$output_dir/RPMS/$architecture" "$output_dir/SRPMS"
install -m 0644 "$rpm" "$output_dir/RPMS/$architecture/"
install -m 0644 "$srpm" "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Streamy packages: %s\n' "$output_dir"
