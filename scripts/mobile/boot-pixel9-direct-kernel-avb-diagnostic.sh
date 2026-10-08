#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# One-shot RAM-only boot of an exact reproducible stock-size AVB diagnostic.
# The only state-changing transport command is `fastboot boot`; no flash,
# erase, slot-selection, trust-key, or relock operation is implemented.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

candidate_dir=${1:?usage: boot-pixel9-direct-kernel-avb-diagnostic.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
recovery_dir=${2:?usage: boot-pixel9-direct-kernel-avb-diagnostic.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
evidence_dir=${3:?usage: boot-pixel9-direct-kernel-avb-diagnostic.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
candidate=$candidate_dir/avb-proof/boot.img
candidate_manifest=$candidate_dir/avb-proof/manifest.env
reproducibility=$candidate_dir/reproducibility.env
revocation=$candidate_dir/revocation.env
prior_acceptance=$candidate_dir/prior-acceptance.env
accepted_v2_acceptance=$candidate_dir/accepted-v2-acceptance.env
rejected_v3_result=$candidate_dir/rejected-v3-result.env
accepted_v4_acceptance=$candidate_dir/accepted-v4-acceptance.env
accepted_v5_acceptance=$candidate_dir/accepted-v5-acceptance.env
fedora_rpm_lock=$candidate_dir/fedora44-ram-userspace-rpms.lock
rejected_v6_result=$candidate_dir/rejected-v6-result.env
rejected_v6_revocation=$candidate_dir/rejected-v6-revocation.env
rejected_v6_proof=$candidate_dir/rejected-v6-proof.env
factory=$recovery_dir/$PIXEL9_FACTORY_IMAGE_FILENAME
ota=$recovery_dir/$PIXEL9_FULL_OTA_FILENAME
fastboot_bin=${FASTBOOT:-fastboot}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

case "${LUMA_PIXEL9_DIRECT_KERNEL_AVB_BOOT_AUTHORIZED:-0}:${LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_BOOT_AUTHORIZED:-0}:${LUMA_PIXEL9_DIRECT_KERNEL_AVB_PERSISTENT_DTB_BOOT_AUTHORIZED:-0}:${LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_BOOT_AUTHORIZED:-0}:${LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_BOOT_AUTHORIZED:-0}:${LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_BOOT_AUTHORIZED:-0}:${LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_BOOT_AUTHORIZED:-0}" in
  1:0:0:0:0:0:0) authorization_mode=acm-v1 ;;
  0:1:0:0:0:0:0) authorization_mode=acm-ecm-v2 ;;
  0:0:1:0:0:0:0) authorization_mode=acm-ecm-persistent-dtb-v3 ;;
  0:0:0:1:0:0:0) authorization_mode=acm-ecm-copy-dtb-v4 ;;
  0:0:0:0:1:0:0) authorization_mode=ufs-delay-copy-dtb-v5 ;;
  0:0:0:0:0:1:0) authorization_mode=fedora-userspace-copy-dtb-v6 ;;
  0:0:0:0:0:0:1) authorization_mode=fedora-zstd-userspace-copy-dtb-v7 ;;
  0:0:0:0:0:0:0) die 'one-shot stock-size AVB diagnostic boot authorization is absent' ;;
  *) die 'exactly one stock-size AVB diagnostic authorization mode is required' ;;
esac
authorized_sha=${LUMA_PIXEL9_AUTHORIZED_SHA256:-}
case "$authorized_sha" in
  ''|*[!0-9a-f]*) die 'exact authorized SHA-256 is absent or malformed' ;;
esac
[ "${#authorized_sha}" -eq 64 ] || die 'exact authorized SHA-256 is not 64 hexadecimal characters'
[ ! -e "$evidence_dir" ] || die "refusing to replace evidence: $evidence_dir"
for tool in awk grep head mkdir sed sha256sum tail tr wc "$fastboot_bin"; do
  command -v "$tool" >/dev/null 2>&1 || die "missing temporary-boot tool: $tool"
done
for required in "$candidate" "$candidate_manifest" "$reproducibility" "$factory" "$ota"; do
  [ -f "$required" ] || die "temporary-boot prerequisite is absent: $required"
done
[ ! -e "$revocation" ] || die 'candidate has a revocation record'

candidate_bytes=$(wc -c <"$candidate" | tr -d '[:space:]')
candidate_sha=$(sha256sum "$candidate" | awk '{print $1}')
[ "$authorized_sha" = "$candidate_sha" ] ||
  die 'authorized SHA-256 does not match the exact candidate bytes'
[ "$candidate_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] ||
  die 'candidate does not equal the exact stock boot partition size'
[ "$(tail -c 64 "$candidate" | head -c 4)" = AVBf ] || die 'candidate AVB footer is absent'
candidate_kind=$(sed -n 's/^CANDIDATE_KIND=//p' "$reproducibility")
case "$authorization_mode:$candidate_kind" in
  acm-v1:direct-kernel-embedded-dtb-initramfs-stock-size-avb) ;;
  acm-ecm-v2:direct-kernel-embedded-dtb-initramfs-stock-size-avb-acm-ecm-v2) ;;
  acm-ecm-persistent-dtb-v3:direct-kernel-embedded-persistent-dtb-initramfs-stock-size-avb-acm-ecm-v3) ;;
  acm-ecm-copy-dtb-v4:direct-kernel-embedded-copied-dtb-initramfs-stock-size-avb-acm-ecm-v4) ;;
  ufs-delay-copy-dtb-v5:direct-kernel-embedded-copied-dtb-delayed-ufs-initramfs-stock-size-avb-ecm-v5) ;;
  fedora-userspace-copy-dtb-v6:direct-kernel-embedded-copied-dtb-fedora44-userspace-stock-size-avb-ecm-v6) ;;
  fedora-zstd-userspace-copy-dtb-v7:direct-kernel-embedded-copied-dtb-fedora44-zstd-userspace-stock-size-avb-ecm-v7) ;;
  *) die 'candidate kind differs from the explicitly authorized AVB diagnostic mode' ;;
esac
grep -Fqx 'COMPARISON=byte-for-byte' "$reproducibility" ||
  die 'candidate lacks byte-for-byte reproducibility proof'
for boundary in \
  'AVB_PARTITION_IDENTICAL=true' \
  'AVB_MANIFEST_IDENTICAL=true' \
  'AVB_INFO_IDENTICAL=true' \
  'AVB_VERIFY_REPORT_IDENTICAL=true'; do
  grep -Fqx "$boundary" "$reproducibility" || die "candidate reproducibility lacks: $boundary"
done
case "$authorization_mode" in
  acm-v1)
    grep -Fqx 'PRIOR_UNPADDED_CANDIDATE_REVOKED=true' "$reproducibility" ||
      die 'v1 candidate does not bind its revoked predecessor'
    ;;
  acm-ecm-v2)
    for boundary in \
      'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_CANDIDATE_VERSION=1' \
      'DIAGNOSTIC_VARIANT=acm-ecm-v2' \
      'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
      'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true' \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$reproducibility" ||
        die "ACM+ECM candidate reproducibility lacks: $boundary"
    done
    [ -f "$prior_acceptance" ] || die 'ACM+ECM predecessor acceptance is absent'
    prior_sha=$(sed -n 's/^PRIOR_CANDIDATE_SHA256=//p' "$reproducibility")
    [ -n "$prior_sha" ] || die 'ACM+ECM predecessor digest is absent'
    grep -Fqx "CANDIDATE_SHA256=$prior_sha" "$prior_acceptance" ||
      die 'ACM+ECM predecessor digest differs from its acceptance record'
    for boundary in \
      'PHYSICAL_RAM_BOOT_ACCEPTED=true' \
      'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
      'DIAGNOSTIC_INITRAMFS_EXECUTION_CONFIRMED=true' \
      'BOUNDED_AUTO_REBOOT_TO_STOCK_CONFIRMED=true' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$prior_acceptance" ||
        die "ACM+ECM predecessor acceptance lacks: $boundary"
    done
    for boundary in \
      'DIAGNOSTIC_VARIANT=acm-ecm-v2' \
      'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$candidate_manifest" ||
        die "ACM+ECM candidate manifest lacks: $boundary"
    done
    ;;
  acm-ecm-persistent-dtb-v3)
    for boundary in \
      'LUMA_PIXEL9_DIRECT_KERNEL_AVB_PERSISTENT_DTB_CANDIDATE_VERSION=1' \
      'DIAGNOSTIC_VARIANT=acm-ecm-persistent-dtb-v3' \
      'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=persistent-rodata' \
      "PERSISTENT_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256" \
      'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true' \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$reproducibility" ||
        die "persistent-DTB candidate reproducibility lacks: $boundary"
    done
    [ -f "$prior_acceptance" ] || die 'persistent-DTB predecessor acceptance is absent'
    prior_sha=$(sed -n 's/^PRIOR_CANDIDATE_SHA256=//p' "$reproducibility")
    [ -n "$prior_sha" ] || die 'persistent-DTB predecessor digest is absent'
    grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_ACCEPTANCE_VERSION=1' "$prior_acceptance" ||
      die 'persistent-DTB predecessor acceptance version differs'
    grep -Fqx "CANDIDATE_SHA256=$prior_sha" "$prior_acceptance" ||
      die 'persistent-DTB predecessor digest differs from its acceptance record'
    for boundary in \
      'PHYSICAL_RAM_BOOT_ACCEPTED=true' \
      'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
      'USB_ECM_HTTP_REPORT_RECEIVED=true' \
      'DT_PROPERTY_INTEGRITY_CONFIRMED=false' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$prior_acceptance" ||
        die "persistent-DTB predecessor acceptance lacks: $boundary"
    done
    for boundary in \
      'DIAGNOSTIC_VARIANT=acm-ecm-persistent-dtb-v3' \
      'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=persistent-rodata' \
      "PERSISTENT_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256" \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$candidate_manifest" ||
        die "persistent-DTB candidate manifest lacks: $boundary"
    done
    ;;
  acm-ecm-copy-dtb-v4)
    for boundary in \
      'LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_CANDIDATE_VERSION=1' \
      'DIAGNOSTIC_VARIANT=acm-ecm-copy-dtb-v4' \
      'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'PERSISTENT_DTB_PATCH_SHA256=none' \
      "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
      'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true' \
      'REJECTED_PERSISTENT_DTB_RESULT_BOUND=true' \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$reproducibility" ||
        die "copy-DTB candidate reproducibility lacks: $boundary"
    done
    [ -f "$accepted_v2_acceptance" ] || die 'copy-DTB accepted-v2 record is absent'
    [ -f "$rejected_v3_result" ] || die 'copy-DTB rejected-v3 result is absent'
    accepted_v2_sha=$(sed -n 's/^PRIOR_CANDIDATE_SHA256=//p' "$reproducibility")
    rejected_v3_sha=$(sed -n 's/^REJECTED_PERSISTENT_DTB_CANDIDATE_SHA256=//p' "$reproducibility")
    [ -n "$accepted_v2_sha" ] || die 'copy-DTB accepted-v2 digest is absent'
    [ -n "$rejected_v3_sha" ] || die 'copy-DTB rejected-v3 digest is absent'
    grep -Fqx "CANDIDATE_SHA256=$accepted_v2_sha" "$accepted_v2_acceptance" ||
      die 'copy-DTB accepted-v2 digest differs from its record'
    grep -Fqx "CANDIDATE_SHA256=$rejected_v3_sha" "$rejected_v3_result" ||
      die 'copy-DTB rejected-v3 digest differs from its result'
    for boundary in \
      'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
      'USB_ECM_HTTP_REPORT_RECEIVED=true' \
      'DT_PROPERTY_INTEGRITY_CONFIRMED=false' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$accepted_v2_acceptance" ||
        die "copy-DTB accepted-v2 record lacks: $boundary"
    done
    for boundary in \
      'PHYSICAL_DIAGNOSTIC_ACCEPTED=false' \
      'NATIVE_KERNEL_BOOT_CONFIRMED=false' \
      'DIAGNOSTIC_USB_PRODUCT_OBSERVED=false' \
      'RETURN_TO_STOCK_ANDROID_CONFIRMED=true' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$rejected_v3_result" ||
        die "copy-DTB rejected-v3 result lacks: $boundary"
    done
    for boundary in \
      'DIAGNOSTIC_VARIANT=acm-ecm-copy-dtb-v4' \
      'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'PERSISTENT_DTB_PATCH_SHA256=none' \
      "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$candidate_manifest" ||
        die "copy-DTB candidate manifest lacks: $boundary"
    done
    ;;
  ufs-delay-copy-dtb-v5)
    for boundary in \
      'LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_CANDIDATE_VERSION=1' \
      'DIAGNOSTIC_VARIANT=ufs-delay-copy-dtb-v5' \
      'REPORT_VERSION=3' \
      'SNAPSHOT_DELAY_SECONDS=15' \
      'REPORT_TRANSPORTS=usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'PERSISTENT_DTB_PATCH_SHA256=none' \
      "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
      'PRIOR_COPY_DTB_PHYSICAL_ACCEPTANCE_BOUND=true' \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_UFS_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$reproducibility" ||
        die "delayed-UFS candidate reproducibility lacks: $boundary"
    done
    [ -f "$accepted_v4_acceptance" ] || die 'delayed-UFS accepted-v4 record is absent'
    accepted_v4_sha=$(sed -n 's/^PRIOR_CANDIDATE_SHA256=//p' "$reproducibility")
    accepted_v4_result_sha=$(sed -n 's/^PRIOR_ACCEPTANCE_SHA256=//p' "$reproducibility")
    [ -n "$accepted_v4_sha" ] || die 'delayed-UFS accepted-v4 digest is absent'
    [ -n "$accepted_v4_result_sha" ] || die 'delayed-UFS accepted-v4 result digest is absent'
    grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_ACCEPTANCE_VERSION=2' "$accepted_v4_acceptance" ||
      die 'delayed-UFS predecessor acceptance version differs'
    grep -Fqx "CANDIDATE_SHA256=$accepted_v4_sha" "$accepted_v4_acceptance" ||
      die 'delayed-UFS predecessor digest differs from its acceptance record'
    [ "$(sha256sum "$accepted_v4_acceptance" | awk '{print $1}')" = "$accepted_v4_result_sha" ] ||
      die 'delayed-UFS predecessor acceptance digest differs'
    for boundary in \
      'PHYSICAL_DIAGNOSTIC_ACCEPTED=true' \
      'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
      'USB_ECM_HTTP_REPORT_RECEIVED=true' \
      'DT_PROPERTY_INTEGRITY_CONFIRMED=true' \
      'UFS_INVENTORY_RECEIVED=true' \
      'REAL_UFS_BLOCK_DEVICE_OBSERVED=false' \
      'UFS_FUNCTIONAL_ACCEPTED=false' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'SLOTS_CHANGED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$accepted_v4_acceptance" ||
        die "delayed-UFS predecessor acceptance lacks: $boundary"
    done
    for boundary in \
      'DIAGNOSTIC_VARIANT=ufs-delay-copy-dtb-v5' \
      'REPORT_TRANSPORTS=usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'PERSISTENT_DTB_PATCH_SHA256=none' \
      "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_UFS_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$candidate_manifest" ||
        die "delayed-UFS candidate manifest lacks: $boundary"
    done
    ;;
  fedora-userspace-copy-dtb-v6)
    for boundary in \
      'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_CANDIDATE_VERSION=1' \
      'DIAGNOSTIC_VARIANT=fedora-userspace-copy-dtb-v6' \
      'REPORT_VERSION=4' \
      'REPORT_TRANSPORTS=usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'FEDORA_RELEASE=44' \
      'FEDORA_ARCH=aarch64' \
      "FEDORA_RPM_COUNT=$PIXEL9_DIAGNOSTIC_FEDORA_RPM_COUNT" \
      "FEDORA_RPM_LOCK_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256" \
      'FEDORA_BASH_EXECUTION_REQUIRED=true' \
      'FEDORA_GLIBC_EXECUTION_REQUIRED=true' \
      'PRIOR_UFS_PHYSICAL_ACCEPTANCE_BOUND=true' \
      'PERSISTENT_FILESYSTEM_MOUNTS=false' \
      'PERSISTENT_BLOCK_DEVICE_WRITES=false' \
      'INTERACTIVE_SHELL=false' \
      'COMMAND_ENDPOINT=false' \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$reproducibility" ||
        die "Fedora-userspace candidate reproducibility lacks: $boundary"
    done
    [ -f "$accepted_v5_acceptance" ] || die 'Fedora-userspace accepted-v5 record is absent'
    [ -f "$fedora_rpm_lock" ] || die 'Fedora-userspace RPM lock is absent'
    [ "$(sha256sum "$fedora_rpm_lock" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256" ] ||
      die 'Fedora-userspace RPM lock differs from its pin'
    accepted_v5_sha=$(sed -n 's/^PRIOR_CANDIDATE_SHA256=//p' "$reproducibility")
    accepted_v5_result_sha=$(sed -n 's/^PRIOR_ACCEPTANCE_SHA256=//p' "$reproducibility")
    [ -n "$accepted_v5_sha" ] || die 'Fedora-userspace accepted-v5 digest is absent'
    [ -n "$accepted_v5_result_sha" ] || die 'Fedora-userspace accepted-v5 result digest is absent'
    grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_ACCEPTANCE_VERSION=1' "$accepted_v5_acceptance" ||
      die 'Fedora-userspace predecessor acceptance version differs'
    grep -Fqx "CANDIDATE_SHA256=$accepted_v5_sha" "$accepted_v5_acceptance" ||
      die 'Fedora-userspace predecessor digest differs from its acceptance record'
    [ "$(sha256sum "$accepted_v5_acceptance" | awk '{print $1}')" = "$accepted_v5_result_sha" ] ||
      die 'Fedora-userspace predecessor acceptance digest differs'
    for boundary in \
      'PHYSICAL_DIAGNOSTIC_ACCEPTED=true' \
      'NATIVE_KERNEL_BOOT_CONFIRMED=true' \
      'USB_ECM_HTTP_REPORT_RECEIVED=true' \
      'DT_PROPERTY_INTEGRITY_CONFIRMED=true' \
      'REAL_UFS_BLOCK_DEVICE_OBSERVED=true' \
      'UFS_FUNCTIONAL_ACCEPTED=true' \
      'PERSISTENT_FILESYSTEMS_MOUNTED=false' \
      'PERSISTENT_STORAGE_WRITES_ISSUED=false' \
      'RETURN_TO_STOCK_ANDROID_CONFIRMED=true' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'SLOTS_CHANGED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$accepted_v5_acceptance" ||
        die "Fedora-userspace predecessor acceptance lacks: $boundary"
    done
    for boundary in \
      'DIAGNOSTIC_VARIANT=fedora-userspace-copy-dtb-v6' \
      'REPORT_TRANSPORTS=usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'PERSISTENT_DTB_PATCH_SHA256=none' \
      "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256"; do
      grep -Fqx "$boundary" "$candidate_manifest" ||
        die "Fedora-userspace candidate manifest lacks: $boundary"
    done
    ;;
  fedora-zstd-userspace-copy-dtb-v7)
    for boundary in \
      'LUMA_PIXEL9_DIRECT_KERNEL_AVB_FEDORA_ZSTD_CANDIDATE_VERSION=1' \
      'DIAGNOSTIC_VARIANT=fedora-zstd-userspace-copy-dtb-v7' \
      'REPORT_VERSION=4' \
      'REPORT_TRANSPORTS=usb-ecm-http' \
      'FEDORA_RELEASE=44' \
      'FEDORA_ARCH=aarch64' \
      "FEDORA_RPM_COUNT=$PIXEL9_DIAGNOSTIC_FEDORA_RPM_COUNT" \
      "FEDORA_RPM_LOCK_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256" \
      'FEDORA_BASH_EXECUTION_REQUIRED=true' \
      'FEDORA_GLIBC_EXECUTION_REQUIRED=true' \
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
      "RAW_INITRAMFS_BYTES=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES" \
      "RAW_INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256" \
      "STOCK_KERNEL_BYTES=$PIXEL9_STOCK_KERNEL_IMAGE_BYTES" \
      "STOCK_KERNEL_SHA256=$PIXEL9_STOCK_KERNEL_IMAGE_SHA256" \
      'PERSISTENT_FILESYSTEM_MOUNTS=false' \
      'PERSISTENT_BLOCK_DEVICE_WRITES=false' \
      'INTERACTIVE_SHELL=false' \
      'COMMAND_ENDPOINT=false'; do
      grep -Fqx "$boundary" "$reproducibility" ||
        die "Fedora/Zstandard candidate reproducibility lacks: $boundary"
    done
    for required in "$accepted_v5_acceptance" "$fedora_rpm_lock" \
      "$rejected_v6_result" "$rejected_v6_revocation" "$rejected_v6_proof"; do
      [ -f "$required" ] || die "Fedora/Zstandard provenance record is absent: $required"
    done
    [ "$(sha256sum "$fedora_rpm_lock" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_FEDORA_RPM_LOCK_SHA256" ] ||
      die 'Fedora/Zstandard RPM lock differs from its pin'

    accepted_v5_sha=$(sed -n 's/^PRIOR_CANDIDATE_SHA256=//p' "$reproducibility")
    accepted_v5_result_sha=$(sed -n 's/^PRIOR_ACCEPTANCE_SHA256=//p' "$reproducibility")
    rejected_v6_sha=$(sed -n 's/^REJECTED_V6_CANDIDATE_SHA256=//p' "$reproducibility")
    rejected_v6_result_sha=$(sed -n 's/^REJECTED_V6_RESULT_SHA256=//p' "$reproducibility")
    rejected_v6_revocation_sha=$(sed -n 's/^REJECTED_V6_REVOCATION_SHA256=//p' "$reproducibility")
    rejected_v6_proof_sha=$(sed -n 's/^REJECTED_V6_PROOF_SHA256=//p' "$reproducibility")
    for value in "$accepted_v5_sha" "$accepted_v5_result_sha" "$rejected_v6_sha" \
      "$rejected_v6_result_sha" "$rejected_v6_revocation_sha" "$rejected_v6_proof_sha"; do
      [ -n "$value" ] || die 'Fedora/Zstandard provenance digest is absent'
    done
    grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_UFS_ACCEPTANCE_VERSION=1' "$accepted_v5_acceptance" ||
      die 'Fedora/Zstandard accepted-v5 version differs'
    grep -Fqx "CANDIDATE_SHA256=$accepted_v5_sha" "$accepted_v5_acceptance" ||
      die 'Fedora/Zstandard accepted-v5 digest differs'
    [ "$(sha256sum "$accepted_v5_acceptance" | awk '{print $1}')" = "$accepted_v5_result_sha" ] ||
      die 'Fedora/Zstandard accepted-v5 record digest differs'
    [ "$(sha256sum "$rejected_v6_result" | awk '{print $1}')" = "$rejected_v6_result_sha" ] ||
      die 'Fedora/Zstandard rejected-v6 result digest differs'
    [ "$(sha256sum "$rejected_v6_revocation" | awk '{print $1}')" = "$rejected_v6_revocation_sha" ] ||
      die 'Fedora/Zstandard rejected-v6 revocation digest differs'
    [ "$(sha256sum "$rejected_v6_proof" | awk '{print $1}')" = "$rejected_v6_proof_sha" ] ||
      die 'Fedora/Zstandard rejected-v6 proof digest differs'
    grep -Fqx "CANDIDATE_SHA256=$rejected_v6_sha" "$rejected_v6_result" ||
      die 'Fedora/Zstandard rejected-v6 result candidate differs'
    grep -Fqx "CANDIDATE_SHA256=$rejected_v6_sha" "$rejected_v6_revocation" ||
      die 'Fedora/Zstandard rejected-v6 revocation candidate differs'
    grep -Fqx "RESULT_SHA256=$rejected_v6_result_sha" "$rejected_v6_revocation" ||
      die 'Fedora/Zstandard rejected-v6 revocation does not bind its result'
    for boundary in \
      'PHYSICAL_DIAGNOSTIC_ACCEPTED=false' \
      'NATIVE_KERNEL_BOOT_CONFIRMED=false' \
      'FEDORA_EXECUTION_TOKEN_RECEIVED=false' \
      'DIAGNOSTIC_USB_PRODUCT_OBSERVED=false' \
      'RETURN_TO_STOCK_ANDROID_CONFIRMED=true' \
      'BOOT_AUTHORIZATION_CONSUMED=true' \
      'RETRY_AUTHORIZED=false' \
      'PARTITIONS_FLASHED=false' \
      'SLOTS_CHANGED=false' \
      'CUSTOM_KEY_INSTALLED=false' \
      'FLASH_AUTHORIZED=false'; do
      grep -Fqx "$boundary" "$rejected_v6_result" ||
        die "Fedora/Zstandard rejected-v6 result lacks: $boundary"
    done

    v7_kernel_bytes=$(sed -n 's/^V7_KERNEL_BYTES=//p' "$reproducibility")
    v7_kernel_sha=$(sed -n 's/^V7_KERNEL_SHA256=//p' "$reproducibility")
    rejected_v6_kernel_bytes=$(sed -n 's/^REJECTED_V6_KERNEL_BYTES=//p' "$reproducibility")
    rejected_v6_kernel_sha=$(sed -n 's/^REJECTED_V6_KERNEL_SHA256=//p' "$reproducibility")
    embedded_initramfs_bytes=$(sed -n 's/^EMBEDDED_INITRAMFS_BYTES=//p' "$reproducibility")
    embedded_initramfs_sha=$(sed -n 's/^EMBEDDED_INITRAMFS_SHA256=//p' "$reproducibility")
    for numeric in "$v7_kernel_bytes" "$rejected_v6_kernel_bytes" "$embedded_initramfs_bytes"; do
      case "$numeric" in ''|*[!0-9]*) die 'Fedora/Zstandard size provenance is malformed' ;; esac
    done
    [ "$v7_kernel_bytes" -lt "$PIXEL9_STOCK_KERNEL_IMAGE_BYTES" ] ||
      die 'Fedora/Zstandard v7 kernel is not smaller than stock'
    [ "$v7_kernel_bytes" -lt "$rejected_v6_kernel_bytes" ] ||
      die 'Fedora/Zstandard v7 kernel is not smaller than rejected v6'
    [ "$embedded_initramfs_bytes" -lt "$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES" ] ||
      die 'Fedora/Zstandard embedded initramfs is not smaller than raw CPIO'
    grep -Fqx "IMAGE_BYTES=$rejected_v6_kernel_bytes" "$rejected_v6_proof" ||
      die 'Fedora/Zstandard rejected-v6 kernel size differs from proof'
    grep -Fqx "IMAGE_SHA256=$rejected_v6_kernel_sha" "$rejected_v6_proof" ||
      die 'Fedora/Zstandard rejected-v6 kernel digest differs from proof'
    for boundary in \
      'DIAGNOSTIC_VARIANT=fedora-zstd-userspace-copy-dtb-v7' \
      'REPORT_TRANSPORTS=usb-ecm-http' \
      'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
      'PERSISTENT_DTB_PATCH_SHA256=none' \
      "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
      "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256" \
      'INITRAMFS_KERNEL_COMPRESSION=zstd' \
      "EMBEDDED_INITRAMFS_SHA256=$embedded_initramfs_sha" \
      "KERNEL_BYTES=$v7_kernel_bytes" \
      "KERNEL_SHA256=$v7_kernel_sha"; do
      grep -Fqx "$boundary" "$candidate_manifest" ||
        die "Fedora/Zstandard candidate manifest lacks: $boundary"
    done
    ;;
esac
grep -Fqx "CANDIDATE_BYTES=$candidate_bytes" "$reproducibility" ||
  die 'candidate byte size differs from its reproducibility record'
grep -Fqx "CANDIDATE_SHA256=$candidate_sha" "$reproducibility" ||
  die 'candidate digest differs from its reproducibility record'
grep -Fqx "PARTITION_BYTES=$candidate_bytes" "$candidate_manifest" ||
  die 'candidate byte size differs from its AVB manifest'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$candidate_manifest" ||
  die 'candidate digest differs from its AVB manifest'
for boundary in \
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
  grep -Fqx "$boundary" "$candidate_manifest" || die "candidate manifest lacks: $boundary"
done

[ "$(wc -c <"$factory" | tr -d '[:space:]')" = "$PIXEL9_FACTORY_IMAGE_BYTES" ] ||
  die 'factory recovery size differs from the pin'
[ "$(sha256sum "$factory" | awk '{print $1}')" = "$PIXEL9_FACTORY_IMAGE_SHA256" ] ||
  die 'factory recovery digest differs from the pin'
[ "$(wc -c <"$ota" | tr -d '[:space:]')" = "$PIXEL9_FULL_OTA_BYTES" ] ||
  die 'full OTA size differs from the pin'
[ "$(sha256sum "$ota" | awk '{print $1}')" = "$PIXEL9_FULL_OTA_SHA256" ] ||
  die 'full OTA digest differs from the pin'

fastboot_rows=$($fastboot_bin devices 2>/dev/null | awk 'NF {count++} END {print count+0}')
[ "$fastboot_rows" -eq 1 ] || die 'expected exactly one fastboot device'
getvar() {
  "$fastboot_bin" getvar "$1" 2>&1 | sed -n "s/.*$1: //p" | tail -1 | tr -d '\r'
}
[ "$(getvar product)" = tokay ] || die 'fastboot target is not tokay'
[ "$(getvar unlocked)" = yes ] || die 'Tokay bootloader is not unlocked'
[ "$(getvar is-userspace)" = no ] || die 'phone is in fastbootd rather than the bootloader'
[ "$(getvar secure)" = yes ] || die 'unexpected insecure bootloader state'
[ "$(getvar battery-soc-ok)" = yes ] || die 'bootloader reports insufficient battery'
[ "$(getvar slot-successful:a)" = yes ] || die 'stock slot A is not successful'
[ "$(getvar slot-unbootable:a)" = no ] || die 'stock slot A is unbootable'
[ "$(getvar slot-successful:b)" = yes ] || die 'stock slot B is not successful'
[ "$(getvar slot-unbootable:b)" = no ] || die 'stock slot B is unbootable'
current_slot=$(getvar current-slot)
case "$current_slot" in a|b) ;; *) die 'current slot is neither A nor B' ;; esac
max_download_raw=$(getvar max-download-size)
case "$max_download_raw" in
  0x*)
    max_download_digits=${max_download_raw#0x}
    case "$max_download_digits" in
      ''|*[!0-9a-fA-F]*) die 'bootloader max-download-size is malformed' ;;
    esac
    ;;
  ''|*[!0-9]*) die 'bootloader max-download-size is malformed' ;;
esac
max_download=$((max_download_raw))
[ "$candidate_bytes" -le "$max_download" ] || die 'candidate exceeds the bootloader download limit'

mkdir -p "$evidence_dir"
{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_BOOT_ATTEMPT_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'CANDIDATE_KIND=%s\n' "$candidate_kind"
  printf 'AUTHORIZATION_MODE=%s\n' "$authorization_mode"
  printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'AUTHORIZED_SHA256=%s\n' "$authorized_sha"
  printf 'CURRENT_SLOT=%s\n' "$current_slot"
  printf 'MAX_DOWNLOAD_BYTES=%s\n' "$max_download"
  printf 'BOOTLOADER_UNLOCKED=true\n'
  printf 'BOOT_AUTHORIZED=true\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$evidence_dir/preflight.env"

printf 'Requesting RAM-only Tokay boot of authorized SHA-256 %s\n' "$candidate_sha"
"$fastboot_bin" boot "$candidate"

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_BOOT_COMMAND_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'CANDIDATE_KIND=%s\n' "$candidate_kind"
  printf 'AUTHORIZATION_MODE=%s\n' "$authorization_mode"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'TEMPORARY_BOOT_COMMAND_ACCEPTED=true\n'
  printf 'NATIVE_KERNEL_BOOT_CONFIRMED=false\n'
  printf 'BOOT_AUTHORIZATION_CONSUMED=true\n'
  printf 'PARTITIONS_FLASHED=false\n'
  printf 'CUSTOM_KEY_INSTALLED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
  printf 'RELOCK_AUTHORIZED=false\n'
} >"$evidence_dir/command.env"

printf 'Tokay accepted the RAM-only stock-size AVB diagnostic request; physical output remains to be observed.\n'
