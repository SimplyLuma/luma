#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install a hash-pinned FP6 WCD9378 module bundle as a recoverable depmod
# overlay. This script never loads/unloads a module, restarts a service,
# reboots, accesses the bootloader, or writes a partition. The matching kernel
# and DTB must subsequently be exercised with `fastboot boot` only.

set -euo pipefail
umask 022

expected_kernel=7.1.2
install_root=/usr/lib/modules/$expected_kernel/updates/luma-fp6-microphone
state_root=/var/lib/luma/fp6-microphone

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die 'run as root'
[ "$(uname -r)" = "$expected_kernel" ] || die 'unexpected running kernel'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device is not the exact Fairphone 6 target'
for tool in awk cmp depmod grep install mkdir modinfo mv readelf readlink \
  sha256sum tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done

bundle=${1:?usage: install-fp6-microphone-modules.sh BUNDLE_DIR}
manifest=$bundle/manifest.env
[ -f "$manifest" ] || die 'bundle manifest is missing'

# The builder emits only simple data records. Reject shell syntax before
# sourcing the file so the artifact manifest cannot become executable input.
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"

[ "${LUMA_FP6_MICROPHONE_BUILD_VERSION:-}" = 1 ] ||
  die 'unsupported microphone bundle version'
[ "${KERNEL_RELEASE:-}" = "$expected_kernel" ] || die 'bundle kernel differs'
[ "${RAM_BOOT_ONLY:-}" = true ] || die 'bundle is not marked RAM-boot-only'
[ "${PHONE_ACCESSED:-}" = false ] || die 'bundle provenance flag differs'
[ "${MODULES_INSTALLED:-}" = false ] || die 'bundle provenance flag differs'
[ "${PARTITION_WRITTEN:-}" = false ] || die 'bundle provenance flag differs'

modules=(regmap-sdw snd-soc-wcd-common snd-soc-wcd9378 snd-soc-wcd9378-sdw)
hashes=(
  "$REGMAP_SDW_KO_SHA256"
  "$SND_SOC_WCD_COMMON_KO_SHA256"
  "$SND_SOC_WCD9378_KO_SHA256"
  "$SND_SOC_WCD9378_SDW_KO_SHA256"
)

for index in "${!modules[@]}"; do
  module=$bundle/${modules[$index]}.ko
  [ -f "$module" ] || die "missing module: ${modules[$index]}"
  [ "$(sha256sum "$module" | awk '{print $1}')" = "${hashes[$index]}" ] ||
    die "module checksum differs: ${modules[$index]}"
  [ "$(modinfo -F vermagic "$module" | awk '{print $1}')" = "$expected_kernel" ] ||
    die "module vermagic differs: ${modules[$index]}"
  ! readelf -SW "$module" | grep -Eq '[.]BTF([.]base)?' ||
    die "module retains incompatible split BTF: ${modules[$index]}"
done

if [ -f "$install_root/manifest.env" ] &&
  cmp -s "$manifest" "$install_root/manifest.env"; then
  printf 'The verified FP6 microphone module bundle is already installed.\n'
  exit 0
fi

bundle_id=${SND_SOC_WCD9378_SDW_KO_SHA256:0:16}
mkdir -p "$state_root/backups" "$state_root/failed"
backup_root=$state_root/backups/module-overlay-before-$bundle_id
failed_root=$state_root/failed/module-overlay-$bundle_id
[ ! -e "$backup_root" ] || die "backup already exists: $backup_root"
[ ! -e "$failed_root" ] || die "failed-install record already exists: $failed_root"

had_previous=false
if [ -e "$install_root" ]; then
  mv "$install_root" "$backup_root"
  had_previous=true
fi

rollback_install=true
cleanup_install() {
  if [ "$rollback_install" = true ]; then
    if [ -e "$install_root" ]; then
      mv "$install_root" "$failed_root" 2>/dev/null || true
    fi
    if [ "$had_previous" = true ] && [ -e "$backup_root" ]; then
      mv "$backup_root" "$install_root" 2>/dev/null || true
    fi
    depmod "$expected_kernel" 2>/dev/null || true
  fi
}
trap cleanup_install EXIT

mkdir -p "$install_root"
for module in "${modules[@]}"; do
  install -m 0644 "$bundle/$module.ko" "$install_root/$module.ko"
done
install -m 0644 "$manifest" "$install_root/manifest.env"
depmod "$expected_kernel"

for module in "${modules[@]}"; do
  selected=$(modinfo -n "$module")
  [ "$(readlink -f "$selected")" = "$(readlink -f "$install_root/$module.ko")" ] ||
    die "modprobe did not select the Luma overlay for $module: $selected"
done

rollback_install=false
trap - EXIT
printf 'Installed the verified FP6 microphone modules without loading them.\n'
if [ "$had_previous" = true ]; then
  printf 'Previous overlay backup: %s\n' "$backup_root"
fi
printf 'modules_loaded=false\nservices_restarted=false\nrebooted=false\npartitions_written=false\n'
