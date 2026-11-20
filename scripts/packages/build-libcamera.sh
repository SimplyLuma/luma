#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build Luma's libcamera: Fedora 44's package (sources from
# its source RPM, pinned by digest) with packaging/rpm's spec and the patches
# in patches/libcamera-fedora. Native builds only (meson, no cross).

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
spec="$repo_root/packaging/rpm/libcamera.spec"
version=$(sed -n 's/^Version:[[:space:]]*//p' "$spec")
fedora_srpm=libcamera-$version-2.fc44.src.rpm
fedora_srpm_url=https://kojipkgs.fedoraproject.org/packages/libcamera/$version/2.fc44/src/$fedora_srpm
fedora_srpm_sha256=7d82bfc0ee14188b79ae052fddbce8e4bceba3ab1425313301e34db6c351be2d

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
[ "$architecture" = "$(uname -m)" ] || { printf 'error: build libcamera natively\n' >&2; exit 1; }
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

output_dir="$repo_root/build/packages/libcamera"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/fedora"
curl -fsSL --retry 3 -o "$work_dir/$fedora_srpm" "$fedora_srpm_url"
echo "$fedora_srpm_sha256  $work_dir/$fedora_srpm" | sha256sum -c --quiet - || {
  printf 'error: %s differs from the recorded source RPM\n' "$fedora_srpm" >&2; exit 1; }
(cd "$work_dir/fedora" && rpm2cpio "../$fedora_srpm" | cpio -idm --quiet)
for file in "$work_dir"/fedora/*; do
  case "$(basename "$file")" in *.spec) ;; *) install -m 0644 "$file" "$rpmbuild_dir/SOURCES/" ;; esac
done
install -m 0644 "$repo_root"/patches/libcamera-fedora/*.patch "$rpmbuild_dir/SOURCES/"
install -m 0644 "$spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing rpm-build dnf5-plugins
    dnf5 -q -y builddep --allowerasing SPECS/libcamera.spec
    rpmbuild -ba --define \"_topdir \$PWD\" --define 'dist .fc44' SPECS/libcamera.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/$architecture" "$output_dir/SRPMS"
for rpm in "$rpmbuild_dir"/RPMS/"$architecture"/*.rpm; do
  case "$rpm" in *-debuginfo-*|*-debugsource-*) continue ;; esac
  install -m 0644 "$rpm" "$output_dir/RPMS/$architecture/"
done
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'libcamera packages: %s\n' "$output_dir"
