#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-rpi-bluetooth-firmware (Raspberry Pi Bluetooth patch RAM) as a
# noarch RPM from RPi-Distro/bluez-firmware at the spec's pinned commit.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
spec="$repo_root/packaging/rpm/luma-rpi-bluetooth-firmware.spec"
commit=$(sed -n 's/^%global commit //p' "$spec")
tarball_sha256=5e027003671a8b129d3e44b6f13845f0a51596047d2310cb69e04a22ea16e70f

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

output_dir="$repo_root/build/packages/luma-rpi-bluetooth-firmware"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
tarball="$rpmbuild_dir/SOURCES/bluez-firmware-$commit.tar.gz"
curl -fsSL --retry 3 -o "$tarball" \
  "https://github.com/RPi-Distro/bluez-firmware/archive/$commit/bluez-firmware-$commit.tar.gz"
echo "$tarball_sha256  $tarball" | sha256sum -c --quiet - || {
  printf 'error: bluez-firmware %s differs from the recorded tarball\n' "$commit" >&2; exit 1; }
install -m 0644 "$repo_root/LICENSES/LicenseRef-Cypress-Wireless-Firmware.txt" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    dnf5 -q -y install --allowerasing rpm-build
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-rpi-bluetooth-firmware.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(cd "$output_dir" && find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS)
printf 'Raspberry Pi Bluetooth firmware: %s\n' "$output_dir"
