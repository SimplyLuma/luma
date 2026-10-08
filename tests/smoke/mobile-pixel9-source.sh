#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

fail() {
  printf 'Pixel 9 source contract: FAIL: %s\n' "$1" >&2
  exit 1
}

case "$PIXEL9_LINUX_COMMIT:$PIXEL9_UBOOT_COMMIT:$PIXEL9_PMAPORTS_COMMIT:$PIXEL9_ANDROID_KERNEL_MANIFEST_COMMIT" in
  *[!0-9a-f:]*|'') fail 'source revisions are not immutable lowercase commit IDs' ;;
esac
[ "${#PIXEL9_LINUX_COMMIT}" -eq 40 ] || fail 'Linux commit is not full length'
[ "${#PIXEL9_UBOOT_COMMIT}" -eq 40 ] || fail 'U-Boot commit is not full length'
[ "${#PIXEL9_PMAPORTS_COMMIT}" -eq 40 ] || fail 'pmaports commit is not full length'
[ "${#PIXEL9_ANDROID_KERNEL_MANIFEST_COMMIT}" -eq 40 ] || fail 'Android manifest commit is not full length'
[ "$PIXEL9_PMAPORTS_BRANCH" = main ] || fail 'pmaports provenance branch is not main'

for pinned_file in \
  "$PIXEL9_UBOOT_RAMBOOT_PATCH:$PIXEL9_UBOOT_RAMBOOT_PATCH_SHA256" \
  "$PIXEL9_UBOOT_LOAD_WINDOW_PATCH:$PIXEL9_UBOOT_LOAD_WINDOW_PATCH_SHA256" \
  "$PIXEL9_UBOOT_HANDOFF_OBSERVABILITY_PATCH:$PIXEL9_UBOOT_HANDOFF_OBSERVABILITY_PATCH_SHA256" \
  "$PIXEL9_UBOOT_EMBEDDED_FIT_PATCH:$PIXEL9_UBOOT_EMBEDDED_FIT_PATCH_SHA256" \
  "$PIXEL9_DIRECT_KERNEL_PATCH:$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" \
  "$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH:$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256" \
  "$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
  "config/mobile/pixel9-physical/diagnostic-init:$PIXEL9_DIAGNOSTIC_INIT_SHA256" \
  "config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_ECM_INIT:$PIXEL9_DIAGNOSTIC_ECM_INIT_SHA256" \
  "config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_INIT:$PIXEL9_DIAGNOSTIC_FEDORA_INIT_SHA256" \
  "config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK:$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256"; do
  relative=${pinned_file%%:*}
  expected=${pinned_file#*:}
  [ "$(shasum -a 256 "$repo_root/$relative" | awk '{print $1}')" = "$expected" ] || \
    fail "pinned local input differs: $relative"
done

kernel_load=$((PIXEL9_DIAGNOSTIC_KERNEL_LOAD))
kernel_end=$((PIXEL9_DIAGNOSTIC_KERNEL_WINDOW_END))
fdt_load=$((PIXEL9_DIAGNOSTIC_FDT_LOAD))
fdt_end=$((PIXEL9_DIAGNOSTIC_FDT_WINDOW_END))
initramfs_load=$((PIXEL9_DIAGNOSTIC_INITRAMFS_LOAD))
initramfs_end=$((PIXEL9_DIAGNOSTIC_INITRAMFS_WINDOW_END))
reserved_start=$((PIXEL9_DIAGNOSTIC_FIRST_RESERVED_START))
[ "$kernel_load" -lt "$kernel_end" ] && [ "$kernel_end" -le "$fdt_load" ] && \
  [ "$fdt_load" -lt "$fdt_end" ] && [ "$fdt_end" -le "$initramfs_load" ] && \
  [ "$initramfs_load" -lt "$initramfs_end" ] && [ "$initramfs_end" -le "$reserved_start" ] || \
  fail 'diagnostic FIT windows overlap or reach reserved memory'

for selected in PIXEL9_FACTORY_IMAGE_SELECTED PIXEL9_FULL_OTA_SELECTED; do
  [ "${!selected}" = true ] || fail "$selected is not pinned"
done

[ "$PIXEL9_OBSERVED_BUILD_ID" = CP41.260814.003.B1 ] || fail 'unexpected observed build ID'
[ "$PIXEL9_OBSERVED_SECURITY_PATCH" = 2026-08-05 ] || fail 'unexpected security patch'
[ "$PIXEL9_RECOVERY_REQUIRED_BASEBAND" = g5400c-260728-260806-B-16025591 ] || \
  fail 'unexpected recovery baseband boundary'
[ "$PIXEL9_OBSERVED_SLOT_SUFFIX" = _a ] || fail 'unexpected observed slot'
[ "$PIXEL9_OBSERVED_VIRTUAL_AB" = true ] || fail 'virtual A/B was not recorded'
[ "$PIXEL9_OBSERVED_SLOT_A_SUCCESSFUL" = true ] || fail 'slot A success is not recorded'
[ "$PIXEL9_OBSERVED_SLOT_A_UNBOOTABLE" = false ] || fail 'slot A is not recorded bootable'
[ "$PIXEL9_OBSERVED_SLOT_B_SUCCESSFUL" = true ] || fail 'slot B success is not recorded'
[ "$PIXEL9_OBSERVED_SLOT_B_UNBOOTABLE" = false ] || fail 'slot B is not recorded bootable'
[ "$PIXEL9_INACTIVE_SLOT_REPAIR_COMPLETED" = true ] || fail 'inactive-slot repair completion is not recorded'
[ -n "$PIXEL9_INACTIVE_SLOT_REPAIR_AUTHORIZATION_CONSUMED" ] || \
  fail 'inactive-slot repair authorization consumption is not recorded'

observed_build_lower=$(printf '%s' "$PIXEL9_OBSERVED_BUILD_ID" | tr '[:upper:]' '[:lower:]')
case "$PIXEL9_FACTORY_IMAGE_FILENAME" in
  tokay_beta-"$observed_build_lower"-factory-*.zip) ;;
  *) fail 'factory filename does not match the observed build' ;;
esac
case "$PIXEL9_FULL_OTA_FILENAME" in
  tokay_beta-ota-"$observed_build_lower"-*.zip) ;;
  *) fail 'full OTA filename does not match the observed build' ;;
esac
for digest in PIXEL9_FACTORY_IMAGE_SHA256 PIXEL9_FULL_OTA_SHA256; do
  digest_value=${!digest}
  case "$digest_value" in
    *[!0-9a-f]*|'') fail "$digest is not a lowercase SHA-256 digest" ;;
  esac
  [ "${#digest_value}" -eq 64 ] || fail "$digest is not full length"
done
[ "$PIXEL9_FACTORY_IMAGE_BYTES" -eq 4345344029 ] || fail 'unexpected factory image size'
[ "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" -eq 67108864 ] || fail 'unexpected stock boot image size'
[ "$PIXEL9_STOCK_BOOT_IMAGE_SHA256" = \
  1d7888cb9f9eaa1057165f5447f5df21f61a168e72082a1f27d0e74421aed353 ] || \
  fail 'unexpected stock boot image digest'
[ "$PIXEL9_STOCK_KERNEL_IMAGE_BYTES" -eq 42441216 ] ||
  fail 'unexpected uncompressed stock kernel size'
[ "$PIXEL9_STOCK_KERNEL_IMAGE_SHA256" = \
  492f6242c6471e2b4674f8291fb96c0222fccbf175d410f1678bbdfc2b8e5f81 ] ||
  fail 'unexpected uncompressed stock kernel digest'
[ "$PIXEL9_FULL_OTA_BYTES" -eq 3409376868 ] || fail 'unexpected full OTA size'

history="$repo_root/config/mobile/pixel9-physical/recovery-history/cp31.260623.012.env"
rg -Fq 'PIXEL9_HISTORICAL_DOWNGRADE_AUTHORIZED=false' "$history" || \
  fail 'the superseded QPR1 set lacks a closed downgrade gate'

for gate in \
  PIXEL9_GOOGLE_DOWNLOAD_TERMS_ACCEPTED \
  PIXEL9_FACTORY_IMAGE_DOWNLOADED PIXEL9_FACTORY_IMAGE_VERIFIED \
  PIXEL9_FULL_OTA_DOWNLOADED PIXEL9_FULL_OTA_VERIFIED \
  PIXEL9_INACTIVE_SLOT_REPAIR_AUTHORIZED \
  PIXEL9_UNLOCK_AUTHORIZED PIXEL9_BOOT_AUTHORIZED \
  PIXEL9_STOCK_BOOT_CONTROL_AUTHORIZED PIXEL9_FLASH_AUTHORIZED \
  PIXEL9_RELOCK_AUTHORIZED; do
  [ "${!gate}" = false ] || fail "$gate is not closed"
done

rg -Fq 'status = "disabled";' "$repo_root/docs/research/pixel9-linux-hardware-readiness.md" ||
  fail 'the native modem-disabled boundary is undocumented'
rg -Fq 'one shared Fedora userspace' "$repo_root/docs/build/pixel9-physical-bringup.md" ||
  fail 'the convergence contract is undocumented'

if rg -n '(\$adb_bin|\$fastboot_bin).*[[:space:]](flash|flashing|erase|wipe|reboot|boot|format|set_active)([[:space:]";]|$)' \
  "$repo_root/scripts/mobile/inventory-pixel9.sh"; then
  fail 'the read-only inventory contains a mutating transport command'
fi

if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' \
  "$repo_root/scripts/mobile/fetch-pixel9-recovery.sh"; then
  fail 'the recovery fetcher contains a phone transport command'
fi

rg -Fq "printf 'AUDIT_ONLY=true" \
  "$repo_root/scripts/mobile/fetch-pixel9-source-audit.sh" || \
  fail 'the sparse source fetch is not labeled audit-only'
rg -Fq "printf 'BUILD_READY=false" \
  "$repo_root/scripts/mobile/fetch-pixel9-source-audit.sh" || \
  fail 'the sparse source fetch does not close the build-ready gate'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' \
  "$repo_root/scripts/mobile/fetch-pixel9-source-audit.sh"; then
  fail 'the sparse source fetch contains a phone transport command'
fi

rg -Fq "printf 'AUDIT_ONLY=false" \
  "$repo_root/scripts/mobile/fetch-pixel9-sources.sh" || \
  fail 'the full source fetch is not distinguished from the sparse audit'
rg -Fq "printf 'BUILD_READY=true" \
  "$repo_root/scripts/mobile/fetch-pixel9-sources.sh" || \
  fail 'the full source fetch does not open only its build-ready gate'

kernel_proof="$repo_root/scripts/mobile/build-pixel9-kernel-proof.sh"
for boundary in \
  'SCOPE=compile-only-unmodified-upstream' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'AVB_SIGNED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$kernel_proof" || \
    fail "the kernel proof lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$kernel_proof"; then
  fail 'the compile-only kernel proof contains a phone transport command'
fi

reproducibility_proof="$repo_root/scripts/mobile/verify-pixel9-kernel-proof.sh"
for boundary in \
  'COMPARISON=byte-for-byte' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$reproducibility_proof" ||
    fail "the kernel reproducibility proof lacks boundary: $boundary"
done

stock_boot_audit="$repo_root/scripts/mobile/analyze-pixel9-stock-boot.sh"
for boundary in \
  'SCOPE=offline-stock-layout-read-only' \
  'FACTORY_FLASH_SCRIPTS_EXECUTED=false' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'AVB_SIGNED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$stock_boot_audit" ||
    fail "the stock boot audit lacks boundary: $boundary"
done
rg -Fq 'STOCK_KERNEL_ENTRY_CONTRACT_VERIFIED=true' "$stock_boot_audit" ||
  fail 'the stock audit does not verify its PE/COFF entry contract'
rg -Fq -- '--require-stock-contract' "$stock_boot_audit" ||
  fail 'the stock audit does not fail closed on its kernel entry contract'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$stock_boot_audit"; then
  fail 'the offline stock boot audit contains a phone transport command'
fi

uboot_proof="$repo_root/scripts/mobile/build-pixel9-uboot-proof.sh"
for boundary in \
  'SCOPE=compile-only-unmodified-upstream' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'U_BOOT_FASTBOOT_EXPOSED=false' \
  'U_BOOT_SHELL_EXPOSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$uboot_proof" ||
    fail "the U-Boot proof lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$uboot_proof"; then
  fail 'the compile-only U-Boot proof contains a phone transport command'
fi

uboot_reproducibility="$repo_root/scripts/mobile/verify-pixel9-uboot-proof.sh"
for boundary in \
  'COMPARISON=byte-for-byte' \
  'PHONE_ACCESSED=false' \
  'U_BOOT_FASTBOOT_EXPOSED=false' \
  'U_BOOT_SHELL_EXPOSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$uboot_reproducibility" ||
    fail "the U-Boot reproducibility proof lacks boundary: $boundary"
done

module_proof="$repo_root/scripts/mobile/build-pixel9-module-proof.sh"
for boundary in \
  'SCOPE=compile-and-stage-unmodified-upstream-modules' \
  'PHONE_ACCESSED=false' \
  'MODULES_BUILT=true' \
  'INITRAMFS_ASSEMBLED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$module_proof" ||
    fail "the module proof lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$module_proof"; then
  fail 'the module proof contains a phone transport command'
fi

diagnostic_fit="$repo_root/scripts/mobile/build-pixel9-diagnostic-fit.sh"
for boundary in \
  'SCOPE=offline-volatile-storage-unmounted-diagnostic' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'MODULES_INCLUDED=false' \
  'INTERACTIVE_SHELL=false' \
  'AUTOMATIC_REBOOT_SECONDS=180' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$diagnostic_fit" ||
    fail "the diagnostic FIT lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$diagnostic_fit"; then
  fail 'the offline diagnostic FIT builder contains a phone transport command'
fi

diagnostic_init="$repo_root/config/mobile/pixel9-physical/diagnostic-init"
rg -Fq 'PERSISTENT_FILESYSTEMS_MOUNTED=false' "$diagnostic_init" || \
  fail 'diagnostic init does not report its storage-unmounted boundary'
rg -Fq 'INTERACTIVE_SHELL_EXPOSED=false' "$diagnostic_init" || \
  fail 'diagnostic init does not report its no-shell boundary'
if rg -n 'mount .* (/data|/metadata|/mnt|/storage)|modprobe|insmod|mdev -s|telnetd|sshd' \
  "$diagnostic_init"; then
  fail 'diagnostic init contains a persistent mount, module loader, or shell service'
fi

diagnostic_ecm_init="$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_ECM_INIT"
for boundary in \
  'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=2' \
  'PERSISTENT_FILESYSTEMS_MOUNTED=false' \
  'INTERACTIVE_SHELL_EXPOSED=false' \
  'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
  'luma-pixel9-volatile-v2' \
  'Pixel 9 volatile diagnostic v2' \
  "device_address=192.168.77.1" \
  "host_address=192.168.77.2" \
  "echo '02:4c:55:4d:41:01'" \
  "echo '02:4c:55:4d:41:02'" \
  'functions/acm.usb0' \
  'functions/ecm.usb0' \
  'udhcpd -f /run/udhcpd.conf' \
  'httpd -f -p "$device_address:80"' \
  '>/dev/ttyGS0' \
  'automatic reboot in 180 seconds'; do
  rg -Fq "$boundary" "$diagnostic_ecm_init" ||
    fail "ACM+ECM diagnostic init lacks: $boundary"
done
if rg -n 'mount .* (/data|/metadata|/mnt|/storage)|modprobe|insmod|mdev -s|telnetd|sshd|cgi-bin' \
  "$diagnostic_ecm_init"; then
  fail 'ACM+ECM diagnostic init contains persistent storage, module loading, or a command service'
fi
if rg -n 'ttyGS0[^\n]*<|<[^\n]*ttyGS0' "$diagnostic_ecm_init"; then
  fail 'ACM+ECM diagnostic init reads from its write-only serial report endpoint'
fi

diagnostic_ecm_builder="$repo_root/scripts/mobile/build-pixel9-diagnostic-ecm-initramfs.sh"
for boundary in \
  'SCOPE=offline-no-shell-acm-ecm-http-initramfs' \
  'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'INTERACTIVE_SHELL=false' \
  'COMMAND_ENDPOINT=false' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$diagnostic_ecm_builder" ||
    fail "ACM+ECM initramfs builder lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$diagnostic_ecm_builder"; then
  fail 'the offline ACM+ECM initramfs builder contains a phone transport command'
fi

diagnostic_ufs_init="$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_UFS_INIT"
for boundary in \
  'snapshot_delay=15' \
  'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=3' \
  'SNAPSHOT_DELAY_SECONDS=$snapshot_delay' \
  'UFS_PLATFORM_DEVICES_BEGIN' \
  'UFS_DEFERRED_PROBES_BEGIN' \
  'UFS_KERNEL_LOG_BEGIN' \
  'REPORT_TRANSPORTS=usb-ecm-http' \
  'luma-pixel9-ufs-v3' \
  'PERSISTENT_FILESYSTEMS_MOUNTED=false' \
  'INTERACTIVE_SHELL_EXPOSED=false'; do
  rg -Fq "$boundary" "$diagnostic_ufs_init" ||
    fail "delayed-UFS diagnostic init lacks: $boundary"
done
if rg -n 'mount .* (/data|/metadata|/mnt|/storage)|modprobe|insmod|mdev -s|telnetd|sshd|cgi-bin' \
  "$diagnostic_ufs_init"; then
  fail 'delayed-UFS diagnostic init contains persistent storage, module loading, or a command service'
fi

diagnostic_ufs_builder="$repo_root/scripts/mobile/build-pixel9-diagnostic-ufs-initramfs.sh"
for boundary in \
  'SCOPE=offline-no-shell-delayed-ufs-ecm-http-initramfs' \
  'REPORT_VERSION=3' \
  'SNAPSHOT_DELAY_SECONDS=15' \
  'REPORT_TRANSPORTS=usb-ecm-http' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'INTERACTIVE_SHELL=false' \
  'COMMAND_ENDPOINT=false' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$diagnostic_ufs_builder" ||
    fail "delayed-UFS initramfs builder lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$diagnostic_ufs_builder"; then
  fail 'the offline delayed-UFS initramfs builder contains a phone transport command'
fi

diagnostic_fedora_init="$repo_root/config/mobile/pixel9-physical/$PIXEL9_DIAGNOSTIC_FEDORA_INIT"
for boundary in \
  'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=4' \
  'FEDORA_BASH_EXECUTION_TOKEN=LUMA_PIXEL9_FEDORA44_AARCH64_OK' \
  'REPORT_TRANSPORTS=usb-ecm-http' \
  'luma-pixel9-fedora-v4' \
  'PERSISTENT_FILESYSTEMS_MOUNTED=false' \
  'PERSISTENT_BLOCK_DEVICES_WRITTEN=false' \
  'INTERACTIVE_SHELL_EXPOSED=false' \
  'COMMAND_ENDPOINT_EXPOSED=false'; do
  rg -Fq "$boundary" "$diagnostic_fedora_init" ||
    fail "Fedora-userspace diagnostic init lacks: $boundary"
done
if rg -n 'mount .* (/data|/metadata|/mnt|/storage)|modprobe|insmod|mdev -s|telnetd|sshd|cgi-bin|getty' \
  "$diagnostic_fedora_init"; then
  fail 'Fedora-userspace diagnostic init contains persistent storage, module loading, or a command service'
fi

diagnostic_fedora_builder="$repo_root/scripts/mobile/build-pixel9-diagnostic-fedora-initramfs.sh"
for boundary in \
  'SCOPE=offline-no-shell-fedora44-aarch64-execution-proof-initramfs' \
  'FEDORA_BASH_EXECUTED_ON_BUILD_HOST=true' \
  'FEDORA_GLIBC_EXECUTED_ON_BUILD_HOST=true' \
  'REPORT_VERSION=4' \
  'REPORT_TRANSPORTS=usb-ecm-http' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'PERSISTENT_BLOCK_DEVICE_WRITES=false' \
  'INTERACTIVE_SHELL=false' \
  'COMMAND_ENDPOINT=false' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$diagnostic_fedora_builder" ||
    fail "Fedora-userspace initramfs builder lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$diagnostic_fedora_builder"; then
  fail 'the offline Fedora-userspace initramfs builder contains a phone transport command'
fi

direct_kernel="$repo_root/scripts/mobile/build-pixel9-direct-kernel-proof.sh"
for boundary in \
  'SCOPE=offline-embedded-dtb-initramfs-entry-proof' \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_SUBSYSTEM=10' \
  'TOKAY_DTB_EMBEDDED_ONCE=true' \
  'INITRAMFS_EMBEDDED_ONCE=true' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'INTERACTIVE_SHELL=false' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_kernel" ||
    fail "the direct-kernel proof lacks boundary: $boundary"
done
rg -Fq -- '--require-stock-contract' "$direct_kernel" ||
  fail 'the direct-kernel proof does not fail closed on the entry contract'
rg -Fq 'cpio -i --quiet --to-stdout init' "$direct_kernel" ||
  fail 'the direct-kernel proof does not inspect the archived diagnostic init'
rg -Fq 'BUILD_PATHS_NORMALIZED=true' "$direct_kernel" ||
  fail 'the direct-kernel proof does not record normalized build paths'
rg -Fq 'COMPAT_VDSO_PATHS_NORMALIZED=true' "$direct_kernel" ||
  fail 'the direct-kernel proof does not record normalized compat-vDSO paths'
rg -Fq -- '-ffile-prefix-map=' "$direct_kernel" ||
  fail 'the direct-kernel proof does not normalize build paths before linking'
rg -Fq 'fedora-zstd-userspace-copy-dtb-v7)' "$direct_kernel" ||
  fail 'the direct-kernel proof lacks the compressed-Fedora variant'
rg -Fq -- '--enable INITRAMFS_COMPRESSION_ZSTD' "$direct_kernel" ||
  fail 'the compressed-Fedora proof does not enable in-kernel Zstandard initramfs storage'
rg -Fq 'embedded-initramfs.data' "$direct_kernel" ||
  fail 'the compressed-Fedora proof does not preserve its embedded payload'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$direct_kernel"; then
  fail 'the direct-kernel proof contains a phone transport command'
fi

direct_record="$repo_root/docs/changes/2026-09-01-pixel9-direct-kernel-proof-001.md"
for evidence in \
  '7631bd033baabe54ff7ba6eb79213e47a7f8e8f7f9ba92e8d6d4c34382ca8c38' \
  '70e367cdb18096388d1948fbaee9f44a75e28d8b9e06d9291d05e285edd04b2d' \
  'cbe2e17a3fe7fa54d3928d740eba972b7954947b674ebca880f189506e547275' \
  'FIXED_PATH_REPRODUCIBLE'; do
  case "$evidence" in
    FIXED_PATH_REPRODUCIBLE)
      rg -Fq 'compare byte-for-byte' "$direct_record" ||
        fail 'the direct-kernel record lacks fixed-path reproducibility evidence'
      ;;
    *)
      rg -Fq "$evidence" "$direct_record" ||
        fail "the direct-kernel record lacks evidence: $evidence"
      ;;
  esac
done

ramboot="$repo_root/scripts/mobile/build-pixel9-uboot-ramboot.sh"
for boundary in \
  'SCOPE=offline-read-only-ramboot-candidate' \
  'PERSISTENT_STORAGE_INITIALIZED=false' \
  'USB_UPDATE_INTERFACE_COMPILED=false' \
  'INTERACTIVE_INPUT_COMPILED=false' \
  'ARBITRARY_MEMORY_COMMANDS_COMPILED=false' \
  'FIT_BOUNDS_REQUIRED=true' \
  'FIT_SIZE_COMPILED=true' \
  'PRIOR_STAGE_INITRD_END_REQUIRED=false' \
  'FIT_DIGEST_REQUIRED=true' \
  'FIT_SOURCE=embedded-rodata' \
  'FIT_EMBEDDED_ONCE=true' \
  'ANDROID_BOOT_RAMDISK_REQUIRED=false' \
  'FAILURE_HOLD_SECONDS=30' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$ramboot" ||
    fail "the read-only ramboot builder lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$ramboot"; then
  fail 'the offline ramboot builder contains a phone transport command'
fi

android_wrapper="$repo_root/scripts/mobile/build-pixel9-android-ramboot-wrapper.sh"
for boundary in \
  'SCOPE=offline-v4-downloaded-boot-wrapper' \
  'HEADER_VERSION=4' \
  'GKI_BOOT_SIGNATURE_INCLUDED=false' \
  'ROUNDTRIP_KERNEL_IDENTICAL=true' \
  'ROUNDTRIP_RAMDISK_IDENTICAL=true' \
  'PHONE_ACCESSED=false' \
  'BOOTLOADER_UNLOCKED=false' \
  'AVB_SIGNED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$android_wrapper" ||
    fail "the Android v4 wrapper lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$android_wrapper"; then
  fail 'the offline Android wrapper contains a phone transport command'
fi

embedded_wrapper="$repo_root/scripts/mobile/build-pixel9-embedded-ramboot-wrapper.sh"
for boundary in \
  'SCOPE=offline-v4-kernel-only-embedded-fit-wrapper' \
  'LZ4_FORMAT=legacy' \
  'HEADER_VERSION=4' \
  'ANDROID_CMDLINE_EMPTY=true' \
  'ANDROID_OS_VERSION_EMPTY=true' \
  'ANDROID_OS_PATCH_LEVEL_EMPTY=true' \
  'ANDROID_BOOT_RAMDISK_EMPTY=true' \
  'GKI_BOOT_SIGNATURE_INCLUDED=false' \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_SUBSYSTEM=10' \
  'FIT_LINKED_IN_KERNEL=true' \
  'LZ4_ROUNDTRIP_IDENTICAL=true' \
  'ROUNDTRIP_KERNEL_IDENTICAL=true' \
  'ROUNDTRIP_RAMDISK_IDENTICAL=true' \
  'PHONE_ACCESSED=false' \
  'BOOTLOADER_UNLOCKED=false' \
  'AVB_SIGNED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$embedded_wrapper" ||
    fail "the embedded-FIT Android v4 wrapper lacks boundary: $boundary"
done
rg -Fq -- '--require-stock-contract' "$embedded_wrapper" ||
  fail 'the embedded-FIT wrapper does not fail closed on the entry contract'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$embedded_wrapper"; then
  fail 'the offline embedded-FIT wrapper contains a phone transport command'
fi

direct_wrapper="$repo_root/scripts/mobile/build-pixel9-direct-kernel-wrapper.sh"
for boundary in \
  'SCOPE=offline-v4-kernel-only-direct-kernel-wrapper' \
  'LZ4_FORMAT=legacy' \
  'HEADER_VERSION=4' \
  'ANDROID_CMDLINE_EMPTY=true' \
  'ANDROID_OS_VERSION_EMPTY=true' \
  'ANDROID_OS_PATCH_LEVEL_EMPTY=true' \
  'ANDROID_BOOT_RAMDISK_EMPTY=true' \
  'GKI_BOOT_SIGNATURE_INCLUDED=false' \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_SUBSYSTEM=10' \
  'TOKAY_DTB_LINKED_IN_KERNEL=true' \
  'INITRAMFS_LINKED_IN_KERNEL=true' \
  'LZ4_ROUNDTRIP_IDENTICAL=true' \
  'ROUNDTRIP_KERNEL_IDENTICAL=true' \
  'ROUNDTRIP_RAMDISK_IDENTICAL=true' \
  'PHONE_ACCESSED=false' \
  'BOOTLOADER_UNLOCKED=false' \
  'AVB_SIGNED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_wrapper" ||
    fail "the direct-kernel Android v4 wrapper lacks boundary: $boundary"
done
rg -Fq -- '--require-stock-contract' "$direct_wrapper" ||
  fail 'the direct-kernel wrapper lacks an independent entry-contract gate'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$direct_wrapper"; then
  fail 'the offline direct-kernel wrapper contains a phone transport command'
fi

direct_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-candidate.sh"
for boundary in \
  'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs' \
  'COMPARISON=byte-for-byte' \
  'KERNEL_IMAGE_IDENTICAL=true' \
  'TOKAY_DTB_IDENTICAL=true' \
  'INITRAMFS_IDENTICAL=true' \
  'COMPRESSED_KERNEL_IDENTICAL=true' \
  'WRAPPER_IDENTICAL=true' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_candidate" ||
    fail "the direct-kernel candidate packager lacks boundary: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$direct_candidate"; then
  fail 'the offline direct-kernel candidate packager contains a phone transport command'
fi

direct_boot="$repo_root/scripts/mobile/boot-pixel9-direct-kernel-diagnostic.sh"
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_BOOT_AUTHORIZED:-0' "$direct_boot" ||
  fail 'the direct-kernel boot helper lacks one-shot authorization'
rg -Fq 'LUMA_PIXEL9_AUTHORIZED_SHA256' "$direct_boot" ||
  fail 'the direct-kernel boot helper lacks exact-digest authorization'
rg -Fq '"$fastboot_bin" boot "$candidate"' "$direct_boot" ||
  fail 'the direct-kernel temporary-boot transaction is absent'
for boundary in \
  'AUTHORIZED_SHA256=%s' \
  'BOOT_AUTHORIZATION_CONSUMED=true' \
  'PARTITIONS_FLASHED=false' \
  'FLASH_AUTHORIZED=false' \
  'RELOCK_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_boot" ||
    fail "the direct-kernel boot helper lacks boundary: $boundary"
done
if rg -n '"\$fastboot_bin"[[:space:]]+(flash|update|erase|format|set_active|flashing)([[:space:]";]|$)' "$direct_boot"; then
  fail 'the direct-kernel boot helper contains a persistent mutation'
fi
direct_boot_test_root=$(mktemp -d)
if "$direct_boot" "$direct_boot_test_root/candidate" \
    "$direct_boot_test_root/recovery" "$direct_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the direct-kernel boot helper ran without one-shot authorization'
fi
[ ! -e "$direct_boot_test_root/evidence" ] ||
  fail 'the direct-kernel boot helper wrote evidence before authorization'

avb_fetch="$repo_root/scripts/mobile/fetch-pixel9-avb-test-key.sh"
for boundary in \
  'PUBLIC_TEST_KEY=true' \
  'PRODUCTION_TRUST_ROOT=false' \
  'CUSTOM_KEY_INSTALLED=false' \
  'PHONE_ACCESSED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$avb_fetch" || fail "the AVB test-input fetch lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$avb_fetch"; then
  fail 'the AVB test-input fetch contains a phone transport command'
fi

direct_avb="$repo_root/scripts/mobile/build-pixel9-direct-kernel-avb-wrapper.sh"
for boundary in \
  'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
  'STOCK_PARTITION_SIZE_MATCH=true' \
  'AVB_FOOTER_PRESENT=true' \
  'AVB_ALGORITHM=SHA256_RSA4096' \
  'AVB_SIGNATURE_VERIFIED_OFFLINE=true' \
  'PUBLIC_TEST_KEY=true' \
  'PRODUCTION_TRUST_ROOT=false' \
  'GOOGLE_PRODUCTION_SIGNATURE_COPIED=false' \
  'ANDROID_IDENTITY_PROPERTIES_COPIED=false' \
  'CUSTOM_KEY_INSTALLED=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_avb" || fail "the direct-kernel AVB wrapper lacks: $boundary"
done
rg -Fq -- '--partition_size "$PIXEL9_STOCK_BOOT_IMAGE_BYTES"' "$direct_avb" ||
  fail 'the direct-kernel AVB wrapper does not preserve the stock partition size'
rg -Fq -- '--algorithm SHA256_RSA4096' "$direct_avb" ||
  fail 'the direct-kernel AVB wrapper does not use the audited stock algorithm shape'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$direct_avb"; then
  fail 'the offline direct-kernel AVB wrapper contains a phone transport command'
fi

direct_avb_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-candidate.sh"
for boundary in \
  'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs-stock-size-avb' \
  'COMPARISON=byte-for-byte' \
  'AVB_PARTITION_IDENTICAL=true' \
  'AVB_MANIFEST_IDENTICAL=true' \
  'AVB_INFO_IDENTICAL=true' \
  'AVB_VERIFY_REPORT_IDENTICAL=true' \
  'PRIOR_UNPADDED_CANDIDATE_REVOKED=true' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_avb_candidate" ||
    fail "the direct-kernel AVB candidate packager lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$direct_avb_candidate"; then
  fail 'the offline direct-kernel AVB candidate packager contains a phone transport command'
fi

direct_avb_ecm_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-ecm-candidate.sh"
for boundary in \
  'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs-stock-size-avb-acm-ecm-v2' \
  'DIAGNOSTIC_VARIANT=acm-ecm-v2' \
  'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
  'COMPARISON=byte-for-byte' \
  'AVB_PARTITION_IDENTICAL=true' \
  'AVB_MANIFEST_IDENTICAL=true' \
  'AVB_INFO_IDENTICAL=true' \
  'AVB_VERIFY_REPORT_IDENTICAL=true' \
  'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true' \
  'PHYSICAL_RAM_BOOT_ACCEPTED=true' \
  'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
  'PARTITIONS_FLASHED=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_avb_ecm_candidate" ||
    fail "the ACM+ECM AVB candidate packager lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$direct_avb_ecm_candidate"; then
  fail 'the offline ACM+ECM AVB candidate packager contains a phone transport command'
fi

persistent_dtb_patch="$repo_root/$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH"
rg -Fq $'\t.section ".rodata", "a"' "$persistent_dtb_patch" ||
  fail 'the persistent-DTB patch does not move the blob to read-only lifetime storage'
rg -Fq 'POISON_FREE_INITMEM (0xcc)' "$persistent_dtb_patch" ||
  fail 'the persistent-DTB patch does not record its observed poison mechanism'

persistent_dtb_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-persistent-dtb-candidate.sh"
for boundary in \
  'CANDIDATE_KIND=direct-kernel-embedded-persistent-dtb-initramfs-stock-size-avb-acm-ecm-v3' \
  'DIAGNOSTIC_VARIANT=acm-ecm-persistent-dtb-v3' \
  'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
  'EMBEDDED_DTB_LIFETIME=persistent-rodata' \
  'COMPARISON=byte-for-byte' \
  'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true' \
  'USB_ECM_HTTP_REPORT_RECEIVED=true' \
  'DT_PROPERTY_INTEGRITY_CONFIRMED=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$persistent_dtb_candidate" ||
    fail "the persistent-DTB candidate packager lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$persistent_dtb_candidate"; then
  fail 'the offline persistent-DTB candidate packager contains a phone transport command'
fi

copy_dtb_patch="$repo_root/$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH"
rg -Fq 'unflatten_and_copy_device_tree();' "$copy_dtb_patch" ||
  fail 'the copy-DTB patch does not use Linux built-in-FDT copy support'
rg -Fq 'CONFIG_LUMA_TOKAY_EMBEDDED_DIAGNOSTIC' "$copy_dtb_patch" ||
  fail 'the copy-DTB patch is not gated to the explicit Tokay diagnostic'
rg -Fq 'Keep the physically accepted init-section placement' "$copy_dtb_patch" ||
  fail 'the copy-DTB patch does not preserve the accepted section boundary'

copy_dtb_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-copy-dtb-candidate.sh"
for boundary in \
  'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-initramfs-stock-size-avb-acm-ecm-v4' \
  'DIAGNOSTIC_VARIANT=acm-ecm-copy-dtb-v4' \
  'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
  'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
  'COMPARISON=byte-for-byte' \
  'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true' \
  'REJECTED_PERSISTENT_DTB_RESULT_BOUND=true' \
  'PHYSICAL_DIAGNOSTIC_ACCEPTED=false' \
  'NATIVE_KERNEL_BOOT_CONFIRMED=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$copy_dtb_candidate" ||
    fail "the copy-DTB candidate packager lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$copy_dtb_candidate"; then
  fail 'the offline copy-DTB candidate packager contains a phone transport command'
fi

ufs_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-ufs-candidate.sh"
for boundary in \
  'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-delayed-ufs-initramfs-stock-size-avb-ecm-v5' \
  'DIAGNOSTIC_VARIANT=ufs-delay-copy-dtb-v5' \
  'REPORT_VERSION=3' \
  'SNAPSHOT_DELAY_SECONDS=15' \
  'REPORT_TRANSPORTS=usb-ecm-http' \
  'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
  'COMPARISON=byte-for-byte' \
  'PRIOR_COPY_DTB_PHYSICAL_ACCEPTANCE_BOUND=true' \
  'PHYSICAL_DIAGNOSTIC_ACCEPTED=true' \
  'DT_PROPERTY_INTEGRITY_CONFIRMED=true' \
  'REAL_UFS_BLOCK_DEVICE_OBSERVED=false' \
  'UFS_FUNCTIONAL_ACCEPTED=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$ufs_candidate" ||
    fail "the delayed-UFS candidate packager lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$ufs_candidate"; then
  fail 'the offline delayed-UFS candidate packager contains a phone transport command'
fi

fedora_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-fedora-candidate.sh"
for boundary in \
  'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_CANDIDATE_VERSION=1' \
  'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-fedora44-userspace-stock-size-avb-ecm-v6' \
  'DIAGNOSTIC_VARIANT=fedora-userspace-copy-dtb-v6' \
  'REPORT_VERSION=4' \
  'REPORT_TRANSPORTS=usb-ecm-http' \
  'FEDORA_RELEASE=44' \
  'FEDORA_ARCH=aarch64' \
  'FEDORA_BASH_EXECUTION_REQUIRED=true' \
  'FEDORA_GLIBC_EXECUTION_REQUIRED=true' \
  'COMPARISON=byte-for-byte' \
  'PRIOR_UFS_PHYSICAL_ACCEPTANCE_BOUND=true' \
  'REAL_UFS_BLOCK_DEVICE_OBSERVED=true' \
  'UFS_FUNCTIONAL_ACCEPTED=true' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'PERSISTENT_BLOCK_DEVICE_WRITES=false' \
  'INTERACTIVE_SHELL=false' \
  'COMMAND_ENDPOINT=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$fedora_candidate" ||
    fail "the Fedora-userspace candidate packager lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$fedora_candidate"; then
  fail 'the offline Fedora-userspace candidate packager contains a phone transport command'
fi

fedora_zstd_candidate="$repo_root/scripts/mobile/prepare-pixel9-direct-kernel-avb-fedora-zstd-candidate.sh"
for boundary in \
  'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_CANDIDATE_VERSION=1' \
  'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-fedora44-zstd-userspace-stock-size-avb-ecm-v7' \
  'DIAGNOSTIC_VARIANT=fedora-zstd-userspace-copy-dtb-v7' \
  'INITRAMFS_KERNEL_COMPRESSION=zstd' \
  'RAW_INITRAMFS_IDENTICAL_TO_REJECTED_V6=true' \
  'RAW_KERNEL_SMALLER_THAN_STOCK=true' \
  'RAW_KERNEL_SMALLER_THAN_REJECTED_V6=true' \
  'KERNEL_SIZE_HYPOTHESIS=reduce-raw-image-below-stock-by-zstd-compressing-identical-initramfs' \
  'KERNEL_PROOF_IDENTICAL=true' \
  'EMBEDDED_INITRAMFS_ROUNDTRIP_IDENTICAL=true' \
  'PRIOR_UFS_PHYSICAL_ACCEPTANCE_BOUND=true' \
  'REJECTED_V6_RESULT_BOUND=true' \
  'REJECTED_V6_REVOCATION_BOUND=true' \
  'REJECTED_V6_PROOF_BOUND=true' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'PERSISTENT_BLOCK_DEVICE_WRITES=false' \
  'INTERACTIVE_SHELL=false' \
  'COMMAND_ENDPOINT=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$fedora_zstd_candidate" ||
    fail "the Fedora/Zstandard candidate packager lacks: $boundary"
done
rg -Fq 'zstd -q -d -c' "$fedora_zstd_candidate" ||
  fail 'the Fedora/Zstandard candidate packager lacks a decompression round-trip gate'
if rg -n '(^|[[:space:]])(adb|fastboot)([[:space:]]|$)' "$fedora_zstd_candidate"; then
  fail 'the offline Fedora/Zstandard candidate packager contains a phone transport command'
fi
fedora_record="$repo_root/docs/changes/2026-09-01-pixel9-fedora-userspace-001.md"
for evidence in \
  'c7060a435891dc7213b58771f84cad552bc98a7e14fb4dd9fb0f6ad4955ff107' \
  '430272399b2c45e7bacdded43adad23aa8f4b09b60e080e79b11cf006350f164' \
  '34,122,240' \
  '42,441,216' \
  'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_BOOT_AUTHORIZED=1' \
  'It remains unauthorized'; do
  rg -Fq "$evidence" "$fedora_record" ||
    fail "the Fedora/Zstandard record lacks: $evidence"
done

direct_avb_boot="$repo_root/scripts/mobile/boot-pixel9-direct-kernel-avb-diagnostic.sh"
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the stock-size AVB boot helper lacks one-shot authorization'
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the ACM+ECM stock-size AVB boot helper lacks distinct one-shot authorization'
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_PERSISTENT_DTB_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the persistent-DTB AVB boot helper lacks distinct one-shot authorization'
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the copy-DTB AVB boot helper lacks distinct one-shot authorization'
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the delayed-UFS AVB boot helper lacks distinct one-shot authorization'
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the Fedora-userspace AVB boot helper lacks distinct one-shot authorization'
rg -Fq 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_BOOT_AUTHORIZED:-0' "$direct_avb_boot" ||
  fail 'the Fedora/Zstandard AVB boot helper lacks distinct one-shot authorization'
rg -Fq 'LUMA_PIXEL9_AUTHORIZED_SHA256' "$direct_avb_boot" ||
  fail 'the stock-size AVB boot helper lacks exact-digest authorization'
rg -Fq '"$fastboot_bin" boot "$candidate"' "$direct_avb_boot" ||
  fail 'the stock-size AVB temporary-boot transaction is absent'
for boundary in \
  'BOOT_AUTHORIZATION_CONSUMED=true' \
  'PARTITIONS_FLASHED=false' \
  'CUSTOM_KEY_INSTALLED=false' \
  'FLASH_AUTHORIZED=false' \
  'RELOCK_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$direct_avb_boot" ||
    fail "the stock-size AVB boot helper lacks boundary: $boundary"
done
if rg -n '"\$fastboot_bin"[[:space:]]+(flash|update|erase|format|set_active|flashing)([[:space:]";]|$)' \
  "$direct_avb_boot"; then
  fail 'the stock-size AVB boot helper contains a persistent mutation'
fi
inert_fastboot="$repo_root/tests/fixtures/pixel9-fastboot-inert.sh"
for boundary in \
  'RAM_BOOT_REQUEST_REACHED=true' \
  'TRANSPORT_OPERATION_ISSUED=false' \
  'PARTITIONS_FLASHED=false' \
  'SLOTS_CHANGED=false' \
  'CUSTOM_KEY_INSTALLED=false' \
  'FLASH_AUTHORIZED=false' \
  'exit 86'; do
  rg -Fq "$boundary" "$inert_fastboot" ||
    fail "the Pixel 9 inert transport fixture lacks: $boundary"
done
if rg -n '(^|[[:space:]])(adb|fastboot)[[:space:]]+(boot|flash|update|erase|format|set_active|flashing)([[:space:]";]|$)' \
  "$inert_fastboot"; then
  fail 'the inert Pixel 9 transport fixture contains a real device operation'
fi
direct_avb_boot_test_root=$(mktemp -d)
if "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the stock-size AVB boot helper ran without one-shot authorization'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the stock-size AVB boot helper wrote evidence before authorization'
if LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_BOOT_AUTHORIZED=1 \
    LUMA_PIXEL9_AUTHORIZED_SHA256=a506914286c7cc34658673cecd9e4f301cab00ec25d1c70dc5f1a01c29a55ec8 \
    "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the ACM+ECM boot helper accepted absent prerequisites'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the ACM+ECM boot helper wrote evidence before prerequisite validation'
if LUMA_PIXEL9_DIRECT_KERNEL_AVB_PERSISTENT_DTB_BOOT_AUTHORIZED=1 \
    LUMA_PIXEL9_AUTHORIZED_SHA256=11fa667047127efc426cd273ac8aa39f9b04f1025ca35b9f0f2f965893b71164 \
    "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the persistent-DTB boot helper accepted absent prerequisites'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the persistent-DTB boot helper wrote evidence before prerequisite validation'
if LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_BOOT_AUTHORIZED=1 \
    LUMA_PIXEL9_AUTHORIZED_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
    "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the copy-DTB boot helper accepted absent prerequisites'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the copy-DTB boot helper wrote evidence before prerequisite validation'
if LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_BOOT_AUTHORIZED=1 \
    LUMA_PIXEL9_AUTHORIZED_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
    "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the delayed-UFS boot helper accepted absent prerequisites'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the delayed-UFS boot helper wrote evidence before prerequisite validation'
if LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_BOOT_AUTHORIZED=1 \
    LUMA_PIXEL9_AUTHORIZED_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
    "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the Fedora-userspace boot helper accepted absent prerequisites'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the Fedora-userspace boot helper wrote evidence before prerequisite validation'
if LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_BOOT_AUTHORIZED=1 \
    LUMA_PIXEL9_AUTHORIZED_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
    "$direct_avb_boot" "$direct_avb_boot_test_root/candidate" \
    "$direct_avb_boot_test_root/recovery" "$direct_avb_boot_test_root/evidence" \
    >/dev/null 2>&1; then
  fail 'the Fedora/Zstandard boot helper accepted absent prerequisites'
fi
[ ! -e "$direct_avb_boot_test_root/evidence" ] ||
  fail 'the Fedora/Zstandard boot helper wrote evidence before prerequisite validation'

diagnostic_observer="$repo_root/scripts/mobile/observe-pixel9-diagnostic.sh"
for boundary in \
  'SCOPE=host-only-read-only-diagnostic-observation' \
  'http://192.168.77.1/luma-pixel9-report.txt' \
  'LUMA_PIXEL9_OBSERVER_REPORT_VERSION:-2' \
  'LUMA_PIXEL9_OBSERVER_USB_SERIAL:-luma-pixel9-volatile-v2' \
  '2|3|4)' \
  'LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=$report_version' \
  'REPORT_END=true' \
  'USB_ECM_HTTP_REPORT_RECEIVED=%s' \
  'PHONE_MUTATION_COMMANDS_ISSUED=false' \
  'BOOT_COMMAND_ISSUED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$diagnostic_observer" ||
    fail "the pre-armed diagnostic observer lacks: $boundary"
done
if rg -n '"\$(fastboot_bin|adb_bin)"[[:space:]]+(boot|flash|update|erase|format|set_active|flashing|shell|reboot|push|install)([[:space:]";]|$)' \
  "$diagnostic_observer"; then
  fail 'the passive diagnostic observer contains a phone mutation command'
fi
observer_test_root=$direct_avb_boot_test_root/observer
if FASTBOOT=/usr/bin/true ADB=/usr/bin/true CURL=/usr/bin/true \
    "$diagnostic_observer" \
    40544d2b269332463b4b56acd11d9ce4f22e16476e70a0ca94831030271e6b5e \
    "$observer_test_root" >/dev/null 2>&1; then
  fail 'the passive diagnostic observer armed without exactly one fastboot device'
fi
[ ! -e "$observer_test_root" ] ||
  fail 'the passive diagnostic observer wrote evidence before transport validation'

direct_avb_record="$repo_root/docs/changes/2026-09-01-pixel9-direct-kernel-avb-001.md"
for evidence in \
  '86ee814e17ebe5a3aa3b65eb9c2843382d70f61b0122d7a2c5c6e2017432a84e' \
  'NATIVE_KERNEL_BOOT_CONFIRMED' \
  'Pixel 9 volatile diagnostic' \
  'USB_ACM_SERIAL_NODE_OBSERVED=false' \
  'PARTITIONS_FLASHED=false' \
  'RETRY_AUTHORIZED=false'; do
  rg -Fq "$evidence" "$direct_avb_record" ||
    fail "the direct-kernel AVB acceptance record lacks: $evidence"
done

ecm_record="$repo_root/docs/changes/2026-09-01-pixel9-ecm-report-001.md"
for evidence in \
  '9c78b9857348fb8920e40e2f7c5b206973af86c5ca4a0acca7c4877c0647743f' \
  '3bd9edc908c5f16753df631092bde184860a35cb12c24d2170c572602f5b9de6' \
  '7bfc51c1a5dc6ad991f82c3086b3d84a7e85b7f07febba650bf541db545d7801' \
  'a1ae3cb6a2e32b42a66008e90e981fccd550713229975d2541805c4c483d6f7c' \
  'a506914286c7cc34658673cecd9e4f301cab00ec25d1c70dc5f1a01c29a55ec8' \
  'a898f7094123df5cfd071921d76522be5c1a57a51769459f3e71910247575a17' \
  'ef5c315d6c6793ead5259b93a7e4dad02cabc7223e9393e9ced25cf6f8241b85' \
  'Physical-device operation:** one exact-digest `fastboot boot`' \
  'POISON_FREE_INITMEM' \
  'retry remains false'; do
  rg -Fq "$evidence" "$ecm_record" ||
    fail "the ACM+ECM offline record lacks: $evidence"
done

persistent_dtb_record="$repo_root/docs/changes/2026-09-01-pixel9-persistent-dtb-001.md"
for evidence in \
  '143e8c16fd4c1d4eadd04a3b1527808e6085f3aafebb1ffae379c9ce4641534e' \
  'b8793df48a77220b2b4c3a641240797ac26a2d3eca95f73a8e71bbd1757db216' \
  '14e8fecaa0a15b22156bbc1e4f6f286eb870fc17b6b5c99d6110f6bd4d148c14' \
  '11fa667047127efc426cd273ac8aa39f9b04f1025ca35b9f0f2f965893b71164' \
  'ba86ed9c2bef4520502eb9b3fce96893ac96f7dff1756bd0be29633c9364b77e' \
  'Physical-device operation:** one exact-digest `fastboot boot`' \
  'PHYSICAL_DIAGNOSTIC_ACCEPTED=false' \
  'authorization is consumed and retry is false'; do
  rg -Fq "$evidence" "$persistent_dtb_record" ||
    fail "the persistent-DTB offline record lacks: $evidence"
done

copy_dtb_record="$repo_root/docs/changes/2026-09-01-pixel9-copy-dtb-001.md"
for evidence in \
  '5104b86851b08b72475e104e05bafcacda94f3483ffe0fb195a3db71ae5249bf' \
  '07cff49e9dd48239b194815d3e276e6ca8a32a32e8d4205f68c3b2487e23fc8b' \
  'b2682e97373d860d05fe5009aaa40bf26fd7cb02f27e8b0d42c526bf9173de49' \
  '40544d2b269332463b4b56acd11d9ce4f22e16476e70a0ca94831030271e6b5e' \
  '63b2ffafcd29e13545b8d4f636e76e980c198297b419aa8fa4bf63aac93c498d' \
  'ba4b005151a344529592ed53e8478ab038f7f066f7fee1157c90bcc165cdec85' \
  'fe370b7e1ef674ea6b68b089947185fdfcc70e33baff08c1d355f31cc0420020' \
  'Physical-device operation:** one exact-digest `fastboot boot`' \
  'PHYSICAL_NATIVE_BOOT_ACCEPTED=true' \
  'PHYSICAL_DIAGNOSTIC_ACCEPTED=true' \
  'USB_ECM_HTTP_REPORT_RECEIVED=true' \
  'DT_PROPERTY_INTEGRITY_CONFIRMED=true' \
  'UFS_FUNCTIONAL_ACCEPTED=false' \
  'DRM_FUNCTIONAL_ACCEPTED=false' \
  'authorizations are consumed and retry is false'; do
  rg -Fq "$evidence" "$copy_dtb_record" ||
    fail "the copy-DTB offline record lacks: $evidence"
done

ufs_record="$repo_root/docs/changes/2026-09-01-pixel9-ufs-delay-001.md"
for evidence in \
  'de419d9f601545d808f8216c47a4561102898f66bafae573751a35d148b70183' \
  'd183124f29405078407223538050766766ad21c6b3b50c16701cdb3f08997b25' \
  '2519c27f9ed58b70f25cce493a0892a1cb07916bc1d85b53567fff97cd0949bb' \
  'ddff253f6bd21f4076a683b191a81a04e577a91fdb2926fe07f0ef262de69197' \
  '9788206ba2528237cfe47ac9811c71fbb5fd95e6470d0cce2b56293d25e43014' \
  'f8898f72772c48fca77cea69494ae108b95d28c4decb4dac3382104a09469d81' \
  'Physical-device operation:** one exact-digest RAM-only boot' \
  'exynos-ufshc' \
  'samsung-ufs-phy' \
  'KLUDG4UHGC-B0E1' \
  'sda` through `sdd' \
  'No persistent filesystem was mounted or written' \
  'The authorization is consumed' \
  'retry is false'; do
  rg -Fq "$evidence" "$ufs_record" ||
    fail "the delayed-UFS record lacks: $evidence"
done

unlock="$repo_root/scripts/mobile/unlock-pixel9-bootloader.sh"
rg -Fq 'LUMA_PIXEL9_UNLOCK_AUTHORIZED:-0' "$unlock" || \
  fail 'Pixel 9 unlock lacks one-shot authorization'
rg -Fq 'flashing unlock' "$unlock" || fail 'Pixel 9 unlock transaction is absent'
for boundary in \
  'CANDIDATE_BOOTED=false' \
  'PARTITIONS_FLASHED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$unlock" || fail "Pixel 9 unlock lacks boundary: $boundary"
done
if rg -n '"\$fastboot_bin"[[:space:]]+(boot|flash|update|erase|format|set_active)([[:space:]";]|$)' "$unlock"; then
  fail 'Pixel 9 unlock script contains an unauthorized boot/flash operation'
fi
unlock_test_root=$(mktemp -d)
if "$unlock" "$unlock_test_root/candidate" "$unlock_test_root/recovery" \
    "$unlock_test_root/evidence" >/dev/null 2>&1; then
  fail 'Pixel 9 unlock ran without one-shot authorization'
fi
[ ! -e "$unlock_test_root/evidence" ] || \
  fail 'Pixel 9 unlock wrote evidence before authorization'

temporary_boot="$repo_root/scripts/mobile/boot-pixel9-diagnostic.sh"
rg -Fq '"$fastboot_bin" boot "$candidate"' "$temporary_boot" || \
  fail 'Pixel 9 temporary-boot transaction is absent'
for boundary in \
  'TEMPORARY_BOOT_COMMAND_ACCEPTED=true' \
  'NATIVE_KERNEL_BOOT_CONFIRMED=false' \
  'BOOT_AUTHORIZATION_CONSUMED=true' \
  'PARTITIONS_FLASHED=false' \
  'FLASH_AUTHORIZED=false' \
  'RELOCK_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$temporary_boot" || \
    fail "Pixel 9 temporary boot lacks boundary: $boundary"
done
for entry_boundary in \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_SUBSYSTEM=10'; do
  rg -Fq "$entry_boundary" "$temporary_boot" ||
    fail "Pixel 9 temporary boot lacks entry gate: $entry_boundary"
done
if rg -n '"\$fastboot_bin"[[:space:]]+(flash|flashing|update|erase|format|set_active)([[:space:]";]|$)' \
  "$temporary_boot"; then
  fail 'Pixel 9 temporary-boot script contains a persistent mutation'
fi
boot_test_root=$(mktemp -d)
if "$temporary_boot" "$boot_test_root/candidate" "$boot_test_root/recovery" \
    "$boot_test_root/evidence" >/dev/null 2>&1; then
  fail 'Pixel 9 temporary boot ran without one-shot authorization'
fi
[ ! -e "$boot_test_root/evidence" ] || \
  fail 'Pixel 9 temporary boot wrote evidence before authorization'

stock_control="$repo_root/scripts/mobile/boot-pixel9-stock-control.sh"
rg -Fq 'LUMA_PIXEL9_STOCK_BOOT_CONTROL_AUTHORIZED:-0' "$stock_control" || \
  fail 'Pixel 9 stock control lacks one-shot authorization'
rg -Fq '"$fastboot_bin" boot "$stock_boot"' "$stock_control" || \
  fail 'Pixel 9 stock-control temporary-boot transaction is absent'
for boundary in \
  'SCOPE=offline-stock-layout-read-only' \
  'ramdisk size: 0' \
  'STOCK_OS_BOOT_CONFIRMED=false' \
  'STOCK_BOOT_CONTROL_AUTHORIZATION_CONSUMED=true' \
  'PARTITIONS_FLASHED=false' \
  'FLASH_AUTHORIZED=false' \
  'RELOCK_AUTHORIZED=false'; do
  rg -Fq "$boundary" "$stock_control" || \
    fail "Pixel 9 stock boot control lacks boundary: $boundary"
done
if rg -n '"\$fastboot_bin"[[:space:]]+(flash|flashing|update|erase|format|set_active)([[:space:]";]|$)' \
  "$stock_control"; then
  fail 'Pixel 9 stock boot control contains a persistent mutation'
fi
stock_control_test_root=$(mktemp -d)
if "$stock_control" "$stock_control_test_root/audit" \
    "$stock_control_test_root/recovery" \
    "$stock_control_test_root/evidence" >/dev/null 2>&1; then
  fail 'Pixel 9 stock boot control ran without one-shot authorization'
fi
[ ! -e "$stock_control_test_root/evidence" ] || \
  fail 'Pixel 9 stock boot control wrote evidence before authorization'
rm -rf -- "$unlock_test_root"
rm -rf -- "$boot_test_root"
rm -rf -- "$stock_control_test_root"

fetch_test_root=$(mktemp -d)
trap 'rm -rf -- "$fetch_test_root"' EXIT
if LUMA_PIXEL9_RECOVERY_DIR="$fetch_test_root/recovery" \
  "$repo_root/scripts/mobile/fetch-pixel9-recovery.sh" >/dev/null 2>&1; then
  fail 'the recovery fetcher ran without explicit terms acceptance'
fi
[ ! -e "$fetch_test_root/recovery" ] || \
  fail 'the recovery fetcher wrote output before terms acceptance'

rg -Fq "printf 'PHONE_ACCESSED=false" \
  "$repo_root/scripts/mobile/fetch-pixel9-recovery.sh" || \
  fail 'the recovery manifest does not preserve the no-phone boundary'
rg -Fq "printf 'FLASH_AUTHORIZED=false" \
  "$repo_root/scripts/mobile/fetch-pixel9-recovery.sh" || \
  fail 'the recovery manifest does not close the flash gate'

repair_test_root=$(mktemp -d)
if LUMA_PIXEL9_REPAIR_EVIDENCE_DIR="$repair_test_root/evidence" \
  "$repo_root/scripts/mobile/repair-pixel9-inactive-slot.sh" >/dev/null 2>&1; then
  fail 'the inactive-slot repair ran without one-shot authorization'
fi
[ ! -e "$repair_test_root/evidence" ] || \
  fail 'inactive-slot repair wrote evidence before authorization'
if rg -n '^[[:space:]]*(fastboot|"\$fastboot_bin")[[:space:]]+(flash|update|set_active|flashing)' \
  "$repo_root/scripts/mobile/repair-pixel9-inactive-slot.sh"; then
  fail 'inactive-slot repair contains a forbidden fastboot mutation'
fi
rg -Fq 'reboot sideload-auto-reboot' \
  "$repo_root/scripts/mobile/repair-pixel9-inactive-slot.sh" || \
  fail 'inactive-slot repair does not enter stock recovery sideload mode'
rg -Fq 'sideload "$ota_path"' \
  "$repo_root/scripts/mobile/repair-pixel9-inactive-slot.sh" || \
  fail 'inactive-slot repair does not use the verified full OTA'
rm -rf -- "$repair_test_root"

printf 'Pixel 9 source contract: PASS\n'
