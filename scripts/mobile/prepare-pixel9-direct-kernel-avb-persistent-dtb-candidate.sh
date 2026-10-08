#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Package two byte-identical persistent-DTB AVB proofs and bind them to the
# physically accepted ECM report predecessor. Offline only; no phone transport.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

avb_a=${1:?usage: prepare-pixel9-direct-kernel-avb-persistent-dtb-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
avb_b=${2:?usage: prepare-pixel9-direct-kernel-avb-persistent-dtb-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
prior_candidate=${3:?usage: prepare-pixel9-direct-kernel-avb-persistent-dtb-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
output_dir=${4:?usage: prepare-pixel9-direct-kernel-avb-persistent-dtb-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
prior_acceptance=$prior_candidate/acceptance.env
prior_reproducibility=$prior_candidate/reproducibility.env
prior_manifest=$prior_candidate/avb-proof/manifest.env
prior_partition=$prior_candidate/avb-proof/boot.img

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the persistent-DTB candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
  esac
fi
for tool in awk cmp grep install mkdir sed sha256sum stat tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing persistent-DTB candidate tool: $tool"
done
for required in "$prior_acceptance" "$prior_reproducibility" "$prior_manifest" "$prior_partition"; do
  [ -f "$required" ] || die "accepted ECM predecessor input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

for avb in "$avb_a" "$avb_b"; do
  for required in boot.img manifest.env avb.info.txt avb.verify.txt; do
    [ -f "$avb/$required" ] || die "persistent-DTB AVB proof is incomplete: $avb/$required"
  done
  for boundary in \
    'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1' \
    'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
    'DIAGNOSTIC_VARIANT=acm-ecm-persistent-dtb-v3' \
    'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
    "LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" \
    "DIRECT_KERNEL_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" \
    "PERSISTENT_DTB_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256" \
    'EMBEDDED_DTB_LIFETIME=persistent-rodata' \
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
    grep -Fqx "$boundary" "$avb/manifest.env" || die "persistent-DTB AVB proof lacks: $boundary"
  done
done

for relative in boot.img manifest.env avb.info.txt avb.verify.txt; do
  cmp "$avb_a/$relative" "$avb_b/$relative" ||
    die "persistent-DTB AVB reproducibility differs: $relative"
done

candidate_bytes=$(stat -c %s "$avb_a/boot.img")
candidate_sha=$(sha256sum "$avb_a/boot.img" | awk '{print $1}')
inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$avb_a/manifest.env")
tokay_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$avb_a/manifest.env")
initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$avb_a/manifest.env")
prior_bytes=$(stat -c %s "$prior_partition")
prior_sha=$(sha256sum "$prior_partition" | awk '{print $1}')
prior_inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$prior_reproducibility")
prior_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$prior_manifest")
prior_initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$prior_manifest")
for value in "$inner_sha" "$tokay_dtb_sha" "$initramfs_sha" "$prior_inner_sha" "$prior_dtb_sha" "$prior_initramfs_sha"; do
  [ -n "$value" ] || die 'persistent-DTB provenance value is absent'
done
[ "$inner_sha" != "$prior_inner_sha" ] || die 'persistent-DTB inner wrapper did not change'
[ "$candidate_sha" != "$prior_sha" ] || die 'persistent-DTB partition did not change'
[ "$tokay_dtb_sha" = "$prior_dtb_sha" ] || die 'Tokay DTB bytes changed from the accepted predecessor'
[ "$initramfs_sha" = "$prior_initramfs_sha" ] || die 'diagnostic initramfs changed from the accepted predecessor'
[ "$candidate_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'candidate size differs from stock'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$avb_a/manifest.env" || die 'candidate digest differs from manifest'
[ "$prior_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || die 'accepted predecessor size differs from stock'
grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_ACCEPTANCE_VERSION=1' "$prior_acceptance" ||
  die 'ECM predecessor acceptance version differs'
grep -Fqx 'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs-stock-size-avb-acm-ecm-v2' "$prior_acceptance" ||
  die 'ECM predecessor kind differs'
grep -Fqx "CANDIDATE_SHA256=$prior_sha" "$prior_acceptance" ||
  die 'ECM predecessor bytes differ from acceptance'
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
  grep -Fqx "$boundary" "$prior_acceptance" || die "ECM predecessor acceptance lacks: $boundary"
done

mkdir -p "$output_dir/avb-proof"
for artifact in boot.img manifest.env avb.info.txt avb.verify.txt; do
  install -m 0644 "$avb_a/$artifact" "$output_dir/avb-proof/$artifact"
done
install -m 0644 "$prior_acceptance" "$output_dir/prior-acceptance.env"
install -m 0644 "$prior_reproducibility" "$output_dir/prior-reproducibility.env"

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_PERSISTENT_DTB_CANDIDATE_VERSION=1\n'
  printf 'CANDIDATE_KIND=direct-kernel-embedded-persistent-dtb-initramfs-stock-size-avb-acm-ecm-v3\n'
  printf 'DIAGNOSTIC_VARIANT=acm-ecm-persistent-dtb-v3\n'
  printf 'REPORT_TRANSPORTS=usb-acm,usb-ecm-http\n'
  printf 'EMBEDDED_DTB_LIFETIME=persistent-rodata\n'
  printf 'COMPARISON=byte-for-byte\n'
  printf 'AVB_PARTITION_IDENTICAL=true\n'
  printf 'AVB_MANIFEST_IDENTICAL=true\n'
  printf 'AVB_INFO_IDENTICAL=true\n'
  printf 'AVB_VERIFY_REPORT_IDENTICAL=true\n'
  printf 'PRIOR_PHYSICAL_ACCEPTANCE_BOUND=true\n'
  printf 'PRIOR_CANDIDATE_SHA256=%s\n' "$prior_sha"
  printf 'PRIOR_INNER_WRAPPER_SHA256=%s\n' "$prior_inner_sha"
  printf 'INNER_WRAPPER_SHA256=%s\n' "$inner_sha"
  printf 'TOKAY_DTB_SHA256=%s\n' "$tokay_dtb_sha"
  printf 'INITRAMFS_SHA256=%s\n' "$initramfs_sha"
  printf 'PERSISTENT_DTB_PATCH_SHA256=%s\n' "$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256"
  printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 persistent-DTB AVB diagnostic candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact candidate SHA-256: %s\n' "$candidate_sha"
