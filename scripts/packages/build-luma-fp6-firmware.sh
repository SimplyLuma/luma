#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build luma-fp6-firmware from the Fairphone 6 firmware set (a tar of
# usr/lib/firmware holding exactly devices/fairphone/fp6/firmware.manifest;
# extracted from the postmarketOS fairphone-fp6 edge image 20261002-1221).
# Firmware is not kept in Git.
#
#   build-luma-fp6-firmware.sh FIRMWARE_TAR

set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
tar_file=$(realpath -e "${1:?usage: build-luma-fp6-firmware.sh FIRMWARE_TAR}")
manifest=$repo_root/devices/fairphone/fp6/firmware.manifest
expected=$(sed -n 's/^manifest_sha256 = "\(.*\)"/\1/p' "$repo_root/devices/fairphone/fp6/device.toml")
echo "$expected  $manifest" | sha256sum -c --quiet - || { echo "error: firmware.manifest does not match device.toml" >&2; exit 1; }
[ "$(tar -tf "$tar_file" | grep -vc '/$')" = 281 ] || { echo "error: the tar is not the 267-file set (+14 links)" >&2; exit 1; }

export LUMA_RPM_BUILDER_ARCHITECTURE=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$LUMA_RPM_BUILDER_ARCHITECTURE" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) echo "error: unsupported build architecture" >&2; exit 1 ;;
esac
output_dir="$repo_root/build/packages/luma-fp6-firmware"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
install -m 0644 "$tar_file" "$rpmbuild_dir/SOURCES/fp6-firmware.tar"
install -m 0644 "$manifest" "$rpmbuild_dir/SOURCES/firmware.manifest"
install -m 0644 "$repo_root/packaging/rpm/luma-fp6-firmware.spec" "$rpmbuild_dir/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" "$builder_container" "
  set -euo pipefail
  dnf5 -q -y install rpm-build
  rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-fp6-firmware.spec
"
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
printf 'FP6 firmware package: %s\n' "$output_dir"
