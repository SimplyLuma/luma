#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Accept two byte-identical AVB wrapper proofs and bind them to the revoked
# unpadded predecessor. Offline packaging only; no phone transport exists here.

set -euo pipefail
umask 077

avb_a=${1:?usage: prepare-pixel9-direct-kernel-avb-candidate.sh AVB_A AVB_B PRIOR_REVOCATION OUTPUT_DIR}
avb_b=${2:?usage: prepare-pixel9-direct-kernel-avb-candidate.sh AVB_A AVB_B PRIOR_REVOCATION OUTPUT_DIR}
prior_revocation=${3:?usage: prepare-pixel9-direct-kernel-avb-candidate.sh AVB_A AVB_B PRIOR_REVOCATION OUTPUT_DIR}
output_dir=${4:?usage: prepare-pixel9-direct-kernel-avb-candidate.sh AVB_A AVB_B PRIOR_REVOCATION OUTPUT_DIR}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the AVB candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
  esac
fi
for tool in awk cmp grep install mkdir sha256sum stat tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing AVB candidate tool: $tool"
done
[ -f "$prior_revocation" ] || die 'prior candidate revocation is absent'
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

for avb in "$avb_a" "$avb_b"; do
  for required in boot.img manifest.env avb.info.txt avb.verify.txt; do
    [ -f "$avb/$required" ] || die "AVB proof is incomplete: $avb/$required"
  done
  for boundary in \
    'LUMA_PIXEL9_DIRECT_KERNEL_AVB_WRAPPER_VERSION=1' \
    'SCOPE=offline-stock-size-avb-parser-shape-experiment' \
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
    grep -Fqx "$boundary" "$avb/manifest.env" || die "AVB proof lacks: $boundary"
  done
done

for relative in boot.img manifest.env avb.info.txt avb.verify.txt; do
  cmp "$avb_a/$relative" "$avb_b/$relative" ||
    die "AVB wrapper reproducibility differs: $relative"
done

candidate_bytes=$(stat -c %s "$avb_a/boot.img")
candidate_sha=$(sha256sum "$avb_a/boot.img" | awk '{print $1}')
inner_sha=$(sed -n 's/^INNER_WRAPPER_SHA256=//p' "$avb_a/manifest.env")
[ -n "$inner_sha" ] || die 'inner wrapper digest is absent'
grep -Fqx "PARTITION_BYTES=$candidate_bytes" "$avb_a/manifest.env" ||
  die 'AVB candidate byte size differs from its manifest'
grep -Fqx "PARTITION_SHA256=$candidate_sha" "$avb_a/manifest.env" ||
  die 'AVB candidate digest differs from its manifest'
grep -Fqx 'LUMA_PIXEL9_CANDIDATE_REVOCATION_VERSION=1' "$prior_revocation" ||
  die 'prior revocation version differs'
grep -Fqx "WRAPPER_SHA256=$inner_sha" "$prior_revocation" ||
  die 'AVB inner image is not the exact revoked predecessor'
grep -Fqx 'BOOT_AUTHORIZATION_CONSUMED=true' "$prior_revocation" ||
  die 'prior authorization is not recorded as consumed'
grep -Fqx 'RETRY_AUTHORIZED=false' "$prior_revocation" ||
  die 'prior candidate is not explicitly revoked'
grep -Fqx 'PARTITIONS_FLASHED=false' "$prior_revocation" ||
  die 'prior attempt lacks its non-flash boundary'

mkdir -p "$output_dir/avb-proof"
for artifact in boot.img manifest.env avb.info.txt avb.verify.txt; do
  install -m 0644 "$avb_a/$artifact" "$output_dir/avb-proof/$artifact"
done
install -m 0644 "$prior_revocation" "$output_dir/prior-revocation.env"

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_AVB_CANDIDATE_VERSION=1\n'
  printf 'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs-stock-size-avb\n'
  printf 'COMPARISON=byte-for-byte\n'
  printf 'AVB_PARTITION_IDENTICAL=true\n'
  printf 'AVB_MANIFEST_IDENTICAL=true\n'
  printf 'AVB_INFO_IDENTICAL=true\n'
  printf 'AVB_VERIFY_REPORT_IDENTICAL=true\n'
  printf 'PRIOR_UNPADDED_CANDIDATE_REVOKED=true\n'
  printf 'INNER_WRAPPER_SHA256=%s\n' "$inner_sha"
  printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 stock-size AVB diagnostic candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact candidate SHA-256: %s\n' "$candidate_sha"
