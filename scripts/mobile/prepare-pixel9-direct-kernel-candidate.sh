#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Combine two independent direct-kernel proofs and two independent Android
# wrapper proofs into one immutable, still-unauthorized RAM-boot candidate.
# This is offline packaging only and contains no phone transport command.

set -euo pipefail
umask 022

proof_a=${1:?usage: prepare-pixel9-direct-kernel-candidate.sh PROOF_A PROOF_B WRAPPER_A WRAPPER_B OUTPUT_DIR}
proof_b=${2:?usage: prepare-pixel9-direct-kernel-candidate.sh PROOF_A PROOF_B WRAPPER_A WRAPPER_B OUTPUT_DIR}
wrapper_a=${3:?usage: prepare-pixel9-direct-kernel-candidate.sh PROOF_A PROOF_B WRAPPER_A WRAPPER_B OUTPUT_DIR}
wrapper_b=${4:?usage: prepare-pixel9-direct-kernel-candidate.sh PROOF_A PROOF_B WRAPPER_A WRAPPER_B OUTPUT_DIR}
output_dir=${5:?usage: prepare-pixel9-direct-kernel-candidate.sh PROOF_A PROOF_B WRAPPER_A WRAPPER_B OUTPUT_DIR}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the direct-kernel candidate must be packaged off-device on Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to package a candidate on a Pixel target' ;;
  esac
fi
for tool in awk cmp grep install mkdir sha256sum stat tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing candidate-packaging tool: $tool"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

for proof in "$proof_a" "$proof_b"; do
  for required in manifest.env artifacts/Image artifacts/zumapro-tokay.dtb \
    artifacts/diagnostic-initramfs.cpio artifacts/config artifacts/entry-contract.env; do
    [ -f "$proof/$required" ] || die "direct proof is incomplete: $proof/$required"
  done
  for boundary in \
    'LUMA_PIXEL9_DIRECT_KERNEL_PROOF_VERSION=1' \
    'ENTRY_CONTRACT_MATCHES_STOCK=true' \
    'TOKAY_DTB_EMBEDDED_ONCE=true' \
    'INITRAMFS_EMBEDDED_ONCE=true' \
    'PERSISTENT_FILESYSTEM_MOUNTS=false' \
    'INTERACTIVE_SHELL=false' \
    'PHONE_ACCESSED=false' \
    'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
    'BOOT_AUTHORIZED=false' \
    'FLASH_AUTHORIZED=false'; do
    grep -Fqx "$boundary" "$proof/manifest.env" ||
      die "direct proof lacks: $boundary"
  done
done

for wrapper in "$wrapper_a" "$wrapper_b"; do
  for required in manifest.env Image.lz4 Image.roundtrip \
    pixel9-read-only-ramboot.img kernel-entry-contract.env wrapper.info.txt \
    roundtrip/kernel roundtrip/ramdisk; do
    [ -f "$wrapper/$required" ] || die "wrapper proof is incomplete: $wrapper/$required"
  done
  for boundary in \
    'LUMA_PIXEL9_DIRECT_KERNEL_WRAPPER_VERSION=1' \
    'HEADER_VERSION=4' \
    'LZ4_FORMAT=legacy' \
    'ANDROID_CMDLINE_EMPTY=true' \
    'ANDROID_OS_VERSION_EMPTY=true' \
    'ANDROID_OS_PATCH_LEVEL_EMPTY=true' \
    'ANDROID_BOOT_RAMDISK_EMPTY=true' \
    'GKI_BOOT_SIGNATURE_INCLUDED=false' \
    'ENTRY_CONTRACT_MATCHES_STOCK=true' \
    'TOKAY_DTB_LINKED_IN_KERNEL=true' \
    'INITRAMFS_LINKED_IN_KERNEL=true' \
    'LZ4_ROUNDTRIP_IDENTICAL=true' \
    'ROUNDTRIP_KERNEL_IDENTICAL=true' \
    'ROUNDTRIP_RAMDISK_IDENTICAL=true' \
    'PHONE_ACCESSED=false' \
    'BOOT_AUTHORIZED=false' \
    'FLASH_AUTHORIZED=false'; do
    grep -Fqx "$boundary" "$wrapper/manifest.env" ||
      die "wrapper proof lacks: $boundary"
  done
done

for relative in manifest.env artifacts/Image artifacts/zumapro-tokay.dtb \
  artifacts/diagnostic-initramfs.cpio artifacts/config artifacts/entry-contract.env; do
  cmp "$proof_a/$relative" "$proof_b/$relative" ||
    die "direct-kernel reproducibility differs: $relative"
done
for relative in manifest.env Image.lz4 Image.roundtrip \
  pixel9-read-only-ramboot.img kernel-entry-contract.env wrapper.info.txt \
  roundtrip/kernel roundtrip/ramdisk; do
  cmp "$wrapper_a/$relative" "$wrapper_b/$relative" ||
    die "wrapper reproducibility differs: $relative"
done

kernel_sha=$(sha256sum "$proof_a/artifacts/Image" | awk '{print $1}')
compressed_sha=$(sha256sum "$wrapper_a/Image.lz4" | awk '{print $1}')
wrapper_sha=$(sha256sum "$wrapper_a/pixel9-read-only-ramboot.img" | awk '{print $1}')
grep -Fqx "IMAGE_SHA256=$kernel_sha" "$proof_a/manifest.env" ||
  die 'kernel digest differs from the direct-proof manifest'
grep -Fqx "KERNEL_SHA256=$kernel_sha" "$wrapper_a/manifest.env" ||
  die 'wrapper does not bind the reproduced kernel'
grep -Fqx "COMPRESSED_KERNEL_SHA256=$compressed_sha" "$wrapper_a/manifest.env" ||
  die 'compressed-kernel digest differs from the wrapper manifest'
grep -Fqx "WRAPPER_SHA256=$wrapper_sha" "$wrapper_a/manifest.env" ||
  die 'wrapper digest differs from its manifest'

mkdir -p "$output_dir/kernel-proof/artifacts" "$output_dir/wrapper-proof/roundtrip"
install -m 0644 "$proof_a/manifest.env" "$output_dir/kernel-proof/manifest.env"
for artifact in Image zumapro-tokay.dtb diagnostic-initramfs.cpio config entry-contract.env; do
  install -m 0644 "$proof_a/artifacts/$artifact" "$output_dir/kernel-proof/artifacts/$artifact"
done
for artifact in manifest.env Image.lz4 Image.roundtrip pixel9-read-only-ramboot.img \
  kernel-entry-contract.env wrapper.info.txt; do
  install -m 0644 "$wrapper_a/$artifact" "$output_dir/wrapper-proof/$artifact"
done
install -m 0644 "$wrapper_a/roundtrip/kernel" "$output_dir/wrapper-proof/roundtrip/kernel"
install -m 0644 "$wrapper_a/roundtrip/ramdisk" "$output_dir/wrapper-proof/roundtrip/ramdisk"

{
  printf 'LUMA_PIXEL9_DIRECT_CANDIDATE_REPRODUCIBILITY_VERSION=1\n'
  printf 'CANDIDATE_KIND=direct-kernel-embedded-dtb-initramfs\n'
  printf 'COMPARISON=byte-for-byte\n'
  printf 'KERNEL_IMAGE_IDENTICAL=true\n'
  printf 'TOKAY_DTB_IDENTICAL=true\n'
  printf 'INITRAMFS_IDENTICAL=true\n'
  printf 'KERNEL_CONFIG_IDENTICAL=true\n'
  printf 'ENTRY_REPORT_IDENTICAL=true\n'
  printf 'COMPRESSED_KERNEL_IDENTICAL=true\n'
  printf 'WRAPPER_IDENTICAL=true\n'
  printf 'ROUNDTRIP_KERNEL_IDENTICAL=true\n'
  printf 'ROUNDTRIP_RAMDISK_IDENTICAL=true\n'
  printf 'KERNEL_SHA256=%s\n' "$kernel_sha"
  printf 'COMPRESSED_KERNEL_SHA256=%s\n' "$compressed_sha"
  printf 'WRAPPER_BYTES=%s\n' "$(stat -c %s "$wrapper_a/pixel9-read-only-ramboot.img")"
  printf 'WRAPPER_SHA256=%s\n' "$wrapper_sha"
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/reproducibility.env"
chmod 0644 "$output_dir/reproducibility.env"

printf 'Pixel 9 direct-kernel candidate (unauthorized): %s\n' "$output_dir"
printf 'Exact wrapper SHA-256: %s\n' "$wrapper_sha"
