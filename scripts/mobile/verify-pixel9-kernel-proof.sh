#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compare two independently completed compile-only Pixel 9 kernel proofs.
# This reads only local build output and never contacts a phone.

set -euo pipefail
umask 022

first=${1:?usage: verify-pixel9-kernel-proof.sh FIRST_PROOF SECOND_PROOF OUTPUT_MANIFEST}
second=${2:?usage: verify-pixel9-kernel-proof.sh FIRST_PROOF SECOND_PROOF OUTPUT_MANIFEST}
output=${3:?usage: verify-pixel9-kernel-proof.sh FIRST_PROOF SECOND_PROOF OUTPUT_MANIFEST}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in cmp mkdir sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "missing comparison tool: $tool"
done
[ ! -e "$output" ] || die "refusing to replace existing output: $output"

for proof in "$first" "$second"; do
  for relative in artifacts/Image artifacts/zumapro-tokay.dtb artifacts/config manifest.env; do
    [ -s "$proof/$relative" ] || die "missing proof input: $proof/$relative"
  done
  grep -Fqx 'SCOPE=compile-only-unmodified-upstream' "$proof/manifest.env" ||
    die "not a compile-only Pixel 9 proof: $proof"
  grep -Fqx 'PHONE_ACCESSED=false' "$proof/manifest.env" ||
    die "proof leaves phone access ambiguous: $proof"
  grep -Fqx 'BOOT_AUTHORIZED=false' "$proof/manifest.env" ||
    die "proof leaves boot authorization ambiguous: $proof"
  grep -Fqx 'FLASH_AUTHORIZED=false' "$proof/manifest.env" ||
    die "proof leaves flash authorization ambiguous: $proof"
done

for relative in artifacts/Image artifacts/zumapro-tokay.dtb artifacts/config manifest.env; do
  cmp -s "$first/$relative" "$second/$relative" ||
    die "proofs differ: $relative"
done

mkdir -p "$(dirname -- "$output")"
{
  printf 'LUMA_PIXEL9_KERNEL_REPRODUCIBILITY_VERSION=1\n'
  printf 'COMPARISON=byte-for-byte\n'
  printf 'IMAGE_REPRODUCIBLE=true\n'
  printf 'TOKAY_DTB_REPRODUCIBLE=true\n'
  printf 'CONFIG_REPRODUCIBLE=true\n'
  printf 'MANIFEST_REPRODUCIBLE=true\n'
  printf 'IMAGE_SHA256=%s\n' "$(sha256sum "$first/artifacts/Image" | awk '{print $1}')"
  printf 'TOKAY_DTB_SHA256=%s\n' "$(sha256sum "$first/artifacts/zumapro-tokay.dtb" | awk '{print $1}')"
  printf 'CONFIG_SHA256=%s\n' "$(sha256sum "$first/artifacts/config" | awk '{print $1}')"
  printf 'PHONE_ACCESSED=false\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output"
chmod 0644 "$output"

printf 'Pixel 9 kernel proof comparison: PASS: %s\n' "$output"
