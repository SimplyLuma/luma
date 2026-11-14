#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build kernel-modules-luma-fp6 from a Fairphone 6 kernel build: the
# modules_install tree of scripts/kernel/build-fp6.sh (--tree), or the modules
# in an OSTree commit (the Alpha 1 image: luma/1/aarch64/fairphone-fp6/nightly
# 949c41c0). The package holds /usr/lib/modules/<release> without vmlinuz,
# initramfs.img and aboot.img. Its kernel must be the one in the boot image.
# It also ships that kernel's Android GPU admission entry: with --tree, the
# android-gpu-admission.sha256 build-fp6.sh wrote beside DIR; an OSTree commit
# has none, so its package admits no kernel.
#
# The package also carries the boot image of the same kernel (--boot-image,
# scripts/mobile/build-fp6-boot-image.sh --kernel) and its declaration for
# luma-device-boot (scripts/devices/device-boot-manifest.py, from
# device.toml [boot_update]) in /usr/lib/luma/device-boot/fairphone-fp6/, so
# an update that brings new modules also brings the kernel that loads them.
# With --tree, the boot image's kernel must be the Image.gz of manifest.env
# beside DIR (IMAGE_SHA256), whose KERNEL_NOTES_SHA256 the declaration keeps.
#
#   build-kernel-modules-luma-fp6.sh --boot-image FILE --tree OUTPUT_DIR/modules
#   build-kernel-modules-luma-fp6.sh --boot-image FILE OSTREE_REPO COMMIT

set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
usage='usage: build-kernel-modules-luma-fp6.sh --boot-image FILE (--tree DIR | OSTREE_REPO COMMIT)'
tree="" ostree_repo="" commit="" boot_image=""
if [ "${1:-}" = --boot-image ]; then
  boot_image=$(realpath -e "${2:?$usage}")
  shift 2
fi
[ -n "$boot_image" ] || { echo "$usage" >&2; exit 2; }
if [ "${1:-}" = --tree ]; then
  tree=$(realpath -e "${2:?$usage}")
else
  ostree_repo=$(realpath -e "${1:?$usage}")
  commit=${2:?$usage}
fi
kver=$(sed -n 's/^release = "\(.*\)"/\1/p' "$repo_root/devices/fairphone/fp6/device.toml" | head -n 1)
grep -q "^%global kver $kver\$" "$repo_root/packaging/rpm/kernel-modules-luma-fp6.spec" ||
  { echo "error: the spec's kver is not device.toml's kernel release $kver" >&2; exit 1; }

output_dir="$repo_root/build/packages/kernel-modules-luma-fp6"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/tree/usr/lib/modules"
if [ -n "$tree" ]; then
  [ -f "$tree/lib/modules/$kver/modules.dep" ] || { echo "error: $tree has no lib/modules/$kver" >&2; exit 1; }
  cp -a "$tree/lib/modules/$kver" "$work_dir/tree/usr/lib/modules/$kver"
  rm -f "$work_dir/tree/usr/lib/modules/$kver"/{build,source}
else
  ostree --repo="$ostree_repo" checkout -U --subpath="/usr/lib/modules/$kver" "$commit" "$work_dir/tree/usr/lib/modules/$kver"
fi
rm -f "$work_dir/tree/usr/lib/modules/$kver"/{vmlinuz,initramfs.img,aboot.img}
admission_dir="$work_dir/tree/usr/share/luma/android/hardware/fairphone-fp6-kernel-notes.d"
mkdir -p "$admission_dir"
if [ -n "$tree" ]; then
  admission="$(dirname "$tree")/android-gpu-admission.sha256"
  [ -f "$admission" ] || { echo "error: $admission is missing (build the kernel with scripts/kernel/build-fp6.sh)" >&2; exit 1; }
  grep -Fq "for $kver," "$admission" || { echo "error: $admission is not for $kver" >&2; exit 1; }
  install -m 0644 "$admission" "$admission_dir/$kver.sha256"
else
  printf '# Fairphone 6 kernel %s from an OSTree commit: no Android GPU admission.\n' "$kver" \
    >"$admission_dir/$kver.sha256"
fi
rm -rf "$work_dir/tree/usr/lib/modules/$kver/dtb"
kernel_sha="" notes_sha=""
if [ -n "$tree" ]; then
  manifest="$(dirname "$tree")/manifest.env"
  [ -f "$manifest" ] || { echo "error: $manifest is missing (build the kernel with scripts/kernel/build-fp6.sh)" >&2; exit 1; }
  kernel_sha=$(sed -n 's/^IMAGE_SHA256=//p' "$manifest")
  notes_sha=$(sed -n 's/^KERNEL_NOTES_SHA256=//p' "$manifest")
  [ -n "$kernel_sha" ] || { echo "error: $manifest has no IMAGE_SHA256" >&2; exit 1; }
fi
python3 "$repo_root/scripts/devices/device-boot-manifest.py" --device "$repo_root/devices/fairphone/fp6" \
  --boot-image "$boot_image" --kernel-sha256 "$kernel_sha" --kernel-notes-sha256 "$notes_sha" \
  --output "$work_dir/tree/usr/lib/luma/device-boot/fairphone-fp6"
tar -C "$work_dir/tree" --sort=name --owner=0 --group=0 --numeric-owner -cf "$rpmbuild_dir/SOURCES/fp6-kernel-modules.tar" usr
install -m 0644 "$repo_root/packaging/rpm/kernel-modules-luma-fp6.spec" "$rpmbuild_dir/SPECS/"
# Nothing is compiled: the aarch64 package is assembled on any host.
rpmbuild -ba --target aarch64 --define "_topdir $rpmbuild_dir" "$rpmbuild_dir/SPECS/kernel-modules-luma-fp6.spec"
rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/aarch64" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/aarch64/*.rpm "$output_dir/RPMS/aarch64/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
printf 'FP6 kernel modules package: %s\n' "$output_dir"
