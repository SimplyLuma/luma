#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Package two byte-identical copy-before-unflatten AVB proofs. Bind the new
# bytes both to the physically accepted ECM predecessor and to the rejected
# persistent-.rodata result. Offline only; no phone transport exists here.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

avb_a=${1:?usage: prepare-pixel9-direct-kernel-avb-copy-dtb-candidate.sh AVB_A AVB_B ACCEPTED_ECM_CANDIDATE REJECTED_PERSISTENT_DTB_CANDIDATE OUTPUT_DIR}
avb_b=${2:?usage: prepare-pixel9-direct-kernel-avb-copy-dtb-candidate.sh AVB_A AVB_B ACCEPTED_ECM_CANDIDATE REJECTED_PERSISTENT_DTB_CANDIDATE OUTPUT_DIR}
accepted_candidate=${3:?usage: prepare-pixel9-direct-kernel-avb-copy-dtb-candidate.sh AVB_A AVB_B ACCEPTED_ECM_CANDIDATE REJECTED_PERSISTENT_DTB_CANDIDATE OUTPUT_DIR}
rejected_candidate=${4:?usage: prepare-pixel9-direct-kernel-avb-copy-dtb-candidate.sh AVB_A AVB_B ACCEPTED_ECM_CANDIDATE REJECTED_PERSISTENT_DTB_CANDIDATE OUTPUT_DIR}
output_dir=${5:?usage: prepare-pixel9-direct-kernel-avb-copy-dtb-candidate.sh AVB_A AVB_B ACCEPTED_ECM_CANDIDATE REJECTED_PERSISTENT_DTB_CANDIDATE OUTPUT_DIR}
accepted_acceptance=$accepted_candidate/acceptance.env
accepted_reproducibility=$accepted_candidate/reproducibility.env
accepted_manifest=$accepted_candidate/avb-proof/manifest.env
accepted_partition=$accepted_candidate/avb-proof/boot.img
rejected_acceptance=$rejected_candidate/acceptance.env
rejected_reproducibility=$rejected_candidate/reproducibility.env
rejected_partition=$rejected_candidate/avb-proof/boot.img

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the copy-DTB candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
  esac
fi
for tool in awk cmp grep install mkdir sed sha256sum stat tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing copy-DTB candidate tool: $tool"
done
for required in \
  "$accepted_acceptance" "$accepted_reproducibility" "$accepted_manifest" "$accepted_partition" \
  "$rejected_acceptance" "$rejected_reproducibility" "$rejected_partition"; do
  [ -f "$required" ] || die "candidate provenance input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

for avb in "$avb_a" "$avb_b"; do
  for required in boot.img manifest.env avb.info.txt avb.verify.txt; do
    [ -f "$avb/$required" ] || die "copy-DTB AVB proof is incomplete: $avb/$required"
  done
  for boundary in \
    'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1' \
    'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
    'DIAGNOSTIC_VARIANT=acm-ecm-copy-dtb-v4' \
    'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
    "LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" \
    "DIRECT_KERNEL_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" \
    'PERSISTENT_DTB_PATCH_SHA256=none' \
    "COPY_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" \
    'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata' \
    "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256" \
    'STOCK_PARTITION_SIZE_MATCH=true' \
    'AVB_FOOTER_PRESENT=true' \
    'AVB_SIGNATURE_VERIFIED_OFFLINE=true' \
    'PUBLIC_TEST_KEY=true' \
    'PRODUCTION_TRUST_ROOT=false' \
    'CUSTOM_KEY_INSTALLED=false' \
    'PHONE_ACCESSED=false' \
    'BOOT_AUTHORIZED=false' \
    'FLASH_AUTHORIZED=false'; do
    grep -Fqx "$boundary" "$avb/manifest.env" || die "copy-DTB AVB proof lacks: $boundary"
  done
done

for relative in boot.img manifest.env avb.info.txt avb.verify.txt; do
  cmp "$avb_a/$relative" "$avb_b/$relative" ||
    die "copy-DTB AVB reproducibility differs: $relative"
done

candidate_bytes=$(stat -c %s "$avb_a/boot.img")
candidate_sha=$(sha256sum "$avb_a/boot.img" | awk '{print $1}')
inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$avb_a/manifest.env")
tokay_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$avb_a/manifest.env")
initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$avb_a/manifest.env")
accepted_bytes=$(stat -c %s "$accepted_partition")
accepted_sha=$(sha256sum "$accepted_partition" | awk '{print $1}')
accepted_inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$accepted_reproducibility")
accepted_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$accepted_manifest")
accepted_initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$accepted_manifest")
rejected_sha=$(sha256sum "$rejected_partition" | awk '{print $1}')
rejected_result_sha=$(sha256sum "$rejected_acceptance" | awk '{print $1}')
for value in \
  "$inner_sha" "$tokay_dtb_sha" "$initramfs_sha" \
  "$accepted_inner_sha" "$accepted_dtb_sha" "$accepted_initramfs_sha" \
  "$rejected_sha" "$rejected_result_sha"; do
  [ -n "$value" ] || die 'copy-DTB provenance value is absent'
done
[ "$candidate_sha" != "$accepted_sha" ] || die 'copy-DTB partition did not change from accepted v2'
[ "$candidate_sha" != "$rejected_sha" ] || die 'copy-DTB partition equals rejected v3'
[ "$tokay_dtb_sha" = "$accepted_dtb_sha" ] || die 'Tokay DTB bytes changed from accepted v2'
[ "$initramfs_sha" = "$accepted_initramfs_sha" ] || die 'diagnostic initramfs changed from accepted v2'
[ "$candidate_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'candidate size differs from stock'
[ "$accepted_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'accepted predecessor size differs from stock'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$avb_a/manifest.env" || die 'candidate digest differs from manifest'

grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_ACCEPTANCE_VERSION=1' "$accepted_acceptance" ||
  die 'accepted ECM predecessor version differs'
grep -Fqx "CANDIDATE_SHA256=$accepted_sha" "$accepted_acceptance" ||
  die 'accepted ECM predecessor bytes differ from acceptance'
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
  grep -Fqx "$boundary" "$accepted_acceptance" || die "accepted ECM predecessor lacks: $boundary"
done

grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_PERSISTENT_DTB_ACCEPTANCE_VERSION=1' "$rejected_acceptance" ||
  die 'rejected persistent-DTB result version differs'
grep -Fqx "CANDIDATE_SHA256=$rejected_sha" "$rejected_acceptance" ||
  die 'rejected persistent-DTB bytes differ from result'
for boundary in \
  'FASTBOOT_RAM_BOOT_COMMAND_ACCEPTED=true' \
  'PHYSICAL_DIAGNOSTIC_ACCEPTED=false' \
  'NATIVE_KERNEL_BOOT_CONFIRMED=false' \
  'DIAGNOSTIC_USB_PRODUCT_OBSERVED=false' \
  'RETURN_TO_STOCK_ANDROID_CONFIRMED=true' \
  'BOOT_AUTHORIZATION_CONSUMED=true' \
  'RETRY_AUTHORIZED=false' \
  'PARTITIONS_FLASHED=false' \
  'CUSTOM_KEY_INSTALLED=false' \
  'FLASH_AUTHORIZED=false'; do
  grep -Fqx "$boundary" "$rejected_acceptance" || die "rejected persistent-DTB result lacks: $boundary"
done

mkdir -p "$output_dir/avb-proof"
for artifact in boot.img manifest.env avb.info.txt avb.verify.txt; do
  install -m 0644 "$avb_a/$artifact" "$output_dir/avb-proof/$artifact"
done
install -m 0644 "$accepted_acceptance" "$output_dir/accepted-v2-acceptance.env"
install -m 0644 "$accepted_reproducibility" "$output_dir/accepted-v2-reproducibility.env"
install -m 0644 "$rejected_acceptance" "$output_dir/rejected-v3-result.env"
install -m 0644 "$rejected_reproducibility" "$output_dir/rejected-v3-reproducibility.env"

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_COPY_DTB_CANDIDATE_VERSION=1\n'
  printf 'CANDIDATE_KIND=direct-kernel-embedded-copied-dtb-initramfs-stock-size-avb-acm-ecm-v4\n'
  printf 'DIAGNOSTIC_VARIANT=acm-ecm-copy-dtb-v4\n'
  printf 'REPORT_TRANSPORTS=usb-acm,usb-ecm-http\n'
  printf 'EMBEDDED_DTB_LIFETIME=copied-from-init-rodata\n'
  printf 'COMPARISON=byte-for-byte\n'
  printf 'AVB_PARTITION_IDENTICAL=true\n'
  printf 'AVB_MANIFEST_IDENTICAL=true\n'
  printf 'AVB_INFO_IDENTICAL=true\n'
  printf 'AVB_VERIFY_REPORT_IDENTICAL=true\n'
  printf 'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true\n'
  printf 'PRIOR_CANDIDATE_SHA256=%s\n' "$accepted_sha"
  printf 'PRIOR_INNER_WRAPPER_SHA256=%s\n' "$accepted_inner_sha"
  printf 'REJECTED_PERSISTENT_DTB_RESULT_BOUND=true\n'
  printf 'REJECTED_PERSISTENT_DTB_CANDIDATE_SHA256=%s\n' "$rejected_sha"
  printf 'REJECTED_PERSISTENT_DTB_RESULT_SHA256=%s\n' "$rejected_result_sha"
  printf 'INNER_WRAPPER_SHA256=%s\n' "$inner_sha"
  printf 'TOKAY_DTB_SHA256=%s\n' "$tokay_dtb_sha"
  printf 'INITRAMFS_SHA256=%s\n' "$initramfs_sha"
  printf 'PERSISTENT_DTB_PATCH_SHA256=none\n'
  printf 'COPY_DTB_PATCH_SHA256=%s\n' "$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256"
  printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 copy-DTB AVB diagnostic candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact candidate SHA-256: %s\n' "$candidate_sha"
