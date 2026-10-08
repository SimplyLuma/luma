#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install the exact NFC modules as a recoverable depmod overlay. This script
# does not load a module, poll NFC, restart a service, reboot, or access the
# bootloader; the matching DTB is exercised separately with fastboot boot.

set -euo pipefail
umask 022

expected_kernel=7.1.2
install_root=/usr/lib/modules/$expected_kernel/updates/luma-fp6-nfc
state_root=/var/lib/luma/fp6-nfc
bundle=${1:?usage: install-fp6-nfc-modules.sh BUNDLE_DIR}
manifest=$bundle/manifest.env

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }
[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(uname -r)" = "$expected_kernel" ] || die 'unexpected running kernel'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device is not the exact Fairphone 6 target'
for tool in cmp cut depmod find grep install mkdir modinfo mv readelf readlink sha256sum tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done
[ -f "$manifest" ] || die 'bundle manifest is missing'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "${LUMA_FP6_NFC_BUILD_VERSION:-}" = 1 ] || die 'bundle version differs'
[ "${UPSTREAM_COMMIT:-}" = af49850e65bdf5dc6f4a14fb9e8e5c5814522d33 ] || die 'upstream differs'
[ "${KERNEL_RELEASE:-}" = "$expected_kernel" ] || die 'bundle kernel differs'
[ "${DTB_SHA256:-}" = 4428113f5f454f1ae56afe497000ca1330cd594629d4c4589028c19ecb3c895c ] || die 'DTB differs'
[ "${KERNEL_RELINKED:-}" = false ] && [ "${NFC_OVERLAY_ONLY:-}" = true ] || die 'kernel/DTB scope differs'
[ "${CALIBRATION_BLOBS_INCLUDED:-}" = false ] || die 'calibration provenance differs'
[ "${READER_SCOPE_ONLY:-}" = true ] && [ "${RAM_BOOT_ONLY:-}" = true ] || die 'scope differs'
[ "${PHONE_ACCESSED:-}" = false ] && [ "${PARTITION_WRITTEN:-}" = false ] || die 'bundle provenance differs'

files=(nfc nci s3fwrn5 s3fwrn5_i2c)
hashes=(
  8beb3b1ee22a7af643cd763e768a21a0f53ee437c94244eceeb0ee33977e5ee0
  e97b561491ed1c4b9963a739cac0553e88b218346b42673e6ea85563b23b14e3
  ba972f65e232fca5a12a43a21ba9496b9e417af4156743aa2ea84af457d6551d
  df905d9ad4240b708a11c029af98344fa7d7ea23284ef0eb21f1655c6e67746a
)
for index in "${!files[@]}"; do
  module=$bundle/modules/${files[$index]}.ko
  [ -f "$module" ] || die "missing module: ${files[$index]}"
  [ "$(hash "$module")" = "${hashes[$index]}" ] || die "module differs: ${files[$index]}"
  [ "$(modinfo -F vermagic "$module" | cut -d ' ' -f 1)" = "$expected_kernel" ] ||
    die "module vermagic differs: ${files[$index]}"
  readelf -h "$module" | grep -Fq 'Machine:                           AArch64' ||
    die "module architecture differs: ${files[$index]}"
done
if find "$bundle" -type f \( -name hwreg.bin -o -name swreg.bin \) -print | grep -q .; then
  die 'unreviewed calibration blob is present'
fi
if [ -f "$install_root/manifest.env" ] && cmp -s "$manifest" "$install_root/manifest.env"; then
  printf 'The verified FP6 NFC module bundle is already installed.\n'
  exit 0
fi

bundle_id=${S3FWRN5_I2C_KO_SHA256:0:16}
mkdir -p "$state_root/backups" "$state_root/failed"
backup_root=$state_root/backups/module-overlay-before-$bundle_id
failed_root=$state_root/failed/module-overlay-$bundle_id
[ ! -e "$backup_root" ] || die "backup already exists: $backup_root"
[ ! -e "$failed_root" ] || die "failed-install record already exists: $failed_root"
had_previous=false
if [ -e "$install_root" ]; then mv "$install_root" "$backup_root"; had_previous=true; fi
rollback_install=true
cleanup_install() {
  if [ "$rollback_install" = true ]; then
    if [ -e "$install_root" ]; then mv "$install_root" "$failed_root" 2>/dev/null || true; fi
    if [ "$had_previous" = true ] && [ -e "$backup_root" ]; then mv "$backup_root" "$install_root" 2>/dev/null || true; fi
    depmod "$expected_kernel" 2>/dev/null || true
  fi
}
trap cleanup_install EXIT
mkdir -p "$install_root/modules"
for file in "${files[@]}"; do install -m 0644 "$bundle/modules/$file.ko" "$install_root/modules/$file.ko"; done
install -m 0644 "$manifest" "$install_root/manifest.env"
depmod "$expected_kernel"
for file in "${files[@]}"; do
  selected=$(modinfo -n "$file")
  [ "$(readlink -f "$selected")" = "$(readlink -f "$install_root/modules/$file.ko")" ] ||
    die "modprobe did not select the Luma overlay for $file: $selected"
done
rollback_install=false
trap - EXIT
printf 'Installed the verified FP6 NFC modules without loading them.\n'
printf 'modules_loaded=false\nservices_restarted=false\nnfc_polled=false\nrebooted=false\npartitions_written=false\n'
