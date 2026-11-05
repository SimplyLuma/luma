#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build kernel-modules-luma-fp6 from the Fairphone 6 kernel build in an OSTree
# commit (the Alpha 1 image: luma/1/aarch64/fairphone-fp6/nightly 949c41c0,
# whose vmlinuz and initramfs are the boot image's). The package holds
# /usr/lib/modules/<release> without vmlinuz, initramfs.img and aboot.img.
#
#   build-kernel-modules-luma-fp6.sh OSTREE_REPO COMMIT

set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ostree_repo=$(realpath -e "${1:?usage: build-kernel-modules-luma-fp6.sh OSTREE_REPO COMMIT}")
commit=${2:?usage: build-kernel-modules-luma-fp6.sh OSTREE_REPO COMMIT}
kver=$(sed -n 's/^release = "\(.*\)"/\1/p' "$repo_root/devices/fairphone/fp6/device.toml" | head -n 1)
grep -q "^%global kver $kver\$" "$repo_root/packaging/rpm/kernel-modules-luma-fp6.spec" ||
  { echo "error: the spec's kver is not device.toml's kernel release $kver" >&2; exit 1; }

output_dir="$repo_root/build/packages/kernel-modules-luma-fp6"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/tree/usr/lib/modules"
ostree --repo="$ostree_repo" checkout -U --subpath="/usr/lib/modules/$kver" "$commit" "$work_dir/tree/usr/lib/modules/$kver"
rm -f "$work_dir/tree/usr/lib/modules/$kver"/{vmlinuz,initramfs.img,aboot.img}
rm -rf "$work_dir/tree/usr/lib/modules/$kver/dtb"
tar -C "$work_dir/tree" --sort=name --owner=0 --group=0 --numeric-owner -cf "$rpmbuild_dir/SOURCES/fp6-kernel-modules.tar" usr
install -m 0644 "$repo_root/packaging/rpm/kernel-modules-luma-fp6.spec" "$rpmbuild_dir/SPECS/"
# Nothing is compiled: the aarch64 package is assembled on any host.
rpmbuild -ba --target aarch64 --define "_topdir $rpmbuild_dir" "$rpmbuild_dir/SPECS/kernel-modules-luma-fp6.spec"
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/aarch64" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/aarch64/*.rpm "$output_dir/RPMS/aarch64/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
printf 'FP6 kernel modules package: %s\n' "$output_dir"
