#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Accept two byte-identical ACM+ECM AVB proofs and bind the successor to the
# physically accepted native-kernel predecessor. Offline packaging only; no
# phone transport exists here.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

avb_a=${1:?usage: prepare-pixel9-direct-kernel-avb-ecm-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
avb_b=${2:?usage: prepare-pixel9-direct-kernel-avb-ecm-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
prior_candidate=${3:?usage: prepare-pixel9-direct-kernel-avb-ecm-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
output_dir=${4:?usage: prepare-pixel9-direct-kernel-avb-ecm-candidate.sh AVB_A AVB_B PRIOR_CANDIDATE OUTPUT_DIR}
prior_acceptance=$prior_candidate/acceptance.env
prior_reproducibility=$prior_candidate/reproducibility.env
prior_partition=$prior_candidate/avb-proof/boot.img

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the ACM+ECM AVB candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
  esac
fi
for tool in awk cmp grep install mkdir sed sha256sum stat tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing ACM+ECM candidate tool: $tool"
done
for required in "$prior_acceptance" "$prior_reproducibility" "$prior_partition"; do
  [ -f "$required" ] || die "accepted predecessor input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

for avb in "$avb_a" "$avb_b"; do
  for required in boot.img manifest.env avb.info.txt avb.verify.txt; do
    [ -f "$avb/$required" ] || die "AVB proof is incomplete: $avb/$required"
  done
  for boundary in \
    'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1' \
    'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
    'DIAGNOSTIC_VARIANT=acm-ecm-v2' \
    'REPORT_TRANSPORTS=usb-acm,usb-ecm-http' \
    "LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" \
    "DIRECT_KERNEL_PATCH_SHA256=$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" \
    "INITRAMFS_SHA256=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256" \
    'STOCK_PARTITION_SIZE_MATCH=true' \
    'AVB_FOOTER_PRESENT=true' \
    'AVB_ALGORITHM=SHA256_RSA4096' \
    'AVB_HASH_ALGORITHM=sha256' \
    'AVB_SIGNATURE_VERIFIED_OFFLINE=true' \
    'PUBLIC_TEST_KEY=true' \
    'PRODUCTION_TRUST_ROOT=false' \
    'GOOGLE_PRODUCTION_SIGNATURE_COPIED=false' \
    'ANDROID_IDENTITY_PROPERTIES_COPIED=false' \
    'CUSTOM_KEY_INSTALLED=false' \
    'PHONE_ACCESSED=false' \
    'BOOT_AUTHORIZED=false' \
    'FLASH_AUTHORIZED=false'; do
    grep -Fqx "$boundary" "$avb/manifest.env" || die "ACM+ECM AVB proof lacks: $boundary"
  done
done

for relative in boot.img manifest.env avb.info.txt avb.verify.txt; do
  cmp "$avb_a/$relative" "$avb_b/$relative" ||
    die "ACM+ECM AVB reproducibility differs: $relative"
done

candidate_bytes=$(stat -c %s "$avb_a/boot.img")
candidate_sha=$(sha256sum "$avb_a/boot.img" | awk '{print $1}')
inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$avb_a/manifest.env")
tokay_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$avb_a/manifest.env")
prior_bytes=$(stat -c %s "$prior_partition")
prior_sha=$(sha256sum "$prior_partition" | awk '{print $1}')
prior_inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$prior_reproducibility")
[ -n "$inner_sha" ] || die 'ACM+ECM inner wrapper digest is absent'
[ -n "$tokay_dtb_sha" ] || die 'ACM+ECM Tokay DTB digest is absent'
[ -n "$prior_inner_sha" ] || die 'accepted predecessor inner-wrapper digest is absent'
[ "$inner_sha" != "$prior_inner_sha" ] || die 'ACM+ECM inner wrapper did not change from the predecessor'
[ "$candidate_sha" != "$prior_sha" ] || die 'ACM+ECM partition did not change from the predecessor'
grep -Fqx "PARTITION_BYTES=$candidate_bytes" "$avb_a/manifest.env" ||
  die 'ACM+ECM candidate byte size differs from its manifest'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$avb_a/manifest.env" ||
  die 'ACM+ECM candidate digest differs from its manifest'
[ "$prior_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] ||
  die 'accepted predecessor size differs from the stock partition size'
grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ACCEPTANCE_VERSION=1' "$prior_acceptance" ||
  die 'accepted predecessor record version differs'
grep -Fqx 'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs-stock-size-avb' "$prior_acceptance" ||
  die 'accepted predecessor kind differs'
grep -Fqx "CANDIDATE_SHA256=$prior_sha" "$prior_acceptance" ||
  die 'accepted predecessor bytes differ from the acceptance record'
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
  grep -Fqx "$boundary" "$prior_acceptance" || die "accepted predecessor lacks: $boundary"
done

mkdir -p "$output_dir/avb-proof"
for artifact in boot.img manifest.env avb.info.txt avb.verify.txt; do
  install -m 0644 "$avb_a/$artifact" "$output_dir/avb-proof/$artifact"
done
install -m 0644 "$prior_acceptance" "$output_dir/prior-acceptance.env"
install -m 0644 "$prior_reproducibility" "$output_dir/prior-reproducibility.env"

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_ECM_CANDIDATE_VERSION=1\n'
  printf 'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs-stock-size-avb-acm-ecm-v2\n'
  printf 'DIAGNOSTIC_VARIANT=acm-ecm-v2\n'
  printf 'REPORT_TRANSPORTS=usb-acm,usb-ecm-http\n'
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
  printf 'INITRAMFS_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256"
  printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 ACM+ECM stock-size AVB diagnostic candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact candidate SHA-256: %s\n' "$candidate_sha"
