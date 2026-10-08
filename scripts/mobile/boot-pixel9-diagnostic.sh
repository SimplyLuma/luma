#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# One-shot Tokay T6 RAM-only diagnostic boot. This validates the final
# reproducible wrapper and recovery set, then invokes only `fastboot boot`.
# It never flashes, erases, changes slots, or relocks the phone.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

candidate_dir=${1:?usage: boot-pixel9-diagnostic.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
recovery_dir=${2:?usage: boot-pixel9-diagnostic.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
evidence_dir=${3:?usage: boot-pixel9-diagnostic.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
candidate=$candidate_dir/wrapper-proof/pixel9-read-only-ramboot.img
candidate_manifest=$candidate_dir/wrapper-proof/manifest.env
reproducibility=$candidate_dir/reproducibility.env
revocation=$candidate_dir/revocation.env
factory=$recovery_dir/$PIXEL9_FACTORY_IMAGE_FILENAME
ota=$recovery_dir/$PIXEL9_FULL_OTA_FILENAME
fastboot_bin=${FASTBOOT:-fastboot}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "${LUMA_PIXEL9_BOOT_AUTHORIZED:-0}" = 1 ] || \
  die 'one-shot temporary-boot authorization is absent; set LUMA_PIXEL9_BOOT_AUTHORIZED=1'
[ ! -e "$evidence_dir" ] || die "refusing to replace evidence: $evidence_dir"
for tool in awk grep mkdir sed sha256sum tail tr wc "$fastboot_bin"; do
  command -v "$tool" >/dev/null 2>&1 || die "missing temporary-boot tool: $tool"
done

for required in "$candidate" "$candidate_manifest" "$reproducibility" "$factory" "$ota"; do
  [ -f "$required" ] || die "temporary-boot prerequisite is absent: $required"
done
[ ! -e "$revocation" ] || die 'candidate has a revocation record'
grep -Fqx 'COMPARISON=byte-for-byte' "$reproducibility" || \
  die 'candidate does not have a byte-for-byte reproducibility proof'
for boundary in \
  'MODULE_IMAGE_IDENTICAL=true' \
  'FIT_IDENTICAL=true' \
  'UBOOT_IDENTICAL=true' \
  'WRAPPER_IDENTICAL=true' \
  'ROUNDTRIP_KERNEL_IDENTICAL=true' \
  'ROUNDTRIP_RAMDISK_IDENTICAL=true'; do
  grep -Fqx "$boundary" "$reproducibility" || \
    die "candidate reproducibility lacks: $boundary"
done

candidate_bytes=$(wc -c <"$candidate" | tr -d '[:space:]')
candidate_sha=$(sha256sum "$candidate" | awk '{print $1}')
grep -Fqx "WRAPPER_BYTES=$candidate_bytes" "$candidate_manifest" || \
  die 'candidate byte size differs from its manifest'
grep -Fqx "WRAPPER_SHA256=$candidate_sha" "$candidate_manifest" || \
  die 'candidate wrapper differs from its manifest'
grep -Fqx "WRAPPER_SHA256=$candidate_sha" "$reproducibility" || \
  die 'candidate wrapper differs from its reproducibility record'
for boundary in \
  'ROUNDTRIP_KERNEL_IDENTICAL=true' \
  'ROUNDTRIP_RAMDISK_IDENTICAL=true' \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_SUBSYSTEM=10' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  grep -Fqx "$boundary" "$candidate_manifest" || \
    die "candidate manifest lacks: $boundary"
done

[ "$(wc -c <"$factory" | tr -d '[:space:]')" = "$PIXEL9_FACTORY_IMAGE_BYTES" ] || \
  die 'factory recovery size differs from the pin'
[ "$(sha256sum "$factory" | awk '{print $1}')" = "$PIXEL9_FACTORY_IMAGE_SHA256" ] || \
  die 'factory recovery digest differs from the pin'
[ "$(wc -c <"$ota" | tr -d '[:space:]')" = "$PIXEL9_FULL_OTA_BYTES" ] || \
  die 'full OTA size differs from the pin'
[ "$(sha256sum "$ota" | awk '{print $1}')" = "$PIXEL9_FULL_OTA_SHA256" ] || \
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
case "$current_slot" in
  a|b) ;;
  *) die 'current slot is neither A nor B' ;;
esac
max_download=$(getvar max-download-size)
case "$max_download" in
  0x*)
    max_download_digits=${max_download#0x}
    case "$max_download_digits" in
      ''|*[!0-9a-fA-F]*) die 'bootloader max-download-size is malformed' ;;
    esac
    ;;
  ''|*[!0-9]*) die 'bootloader max-download-size is malformed' ;;
esac
max_download_dec=$((max_download))
[ "$candidate_bytes" -le "$max_download_dec" ] || \
  die 'candidate exceeds the bootloader download limit'

mkdir -p "$evidence_dir"
{
  printf 'LUMA_PIXEL9_TEMPORARY_BOOT_ATTEMPT_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'CANDIDATE_BYTES=%s\n' "$candidate_bytes"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'FACTORY_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'FULL_OTA_SHA256=%s\n' "$PIXEL9_FULL_OTA_SHA256"
  printf 'CURRENT_SLOT=%s\n' "$current_slot"
  printf 'MAX_DOWNLOAD_BYTES=%s\n' "$max_download_dec"
  printf 'BATTERY_SOC_OK=true\n'
  printf 'BOOTLOADER_UNLOCKED=true\n'
  printf 'BOOT_AUTHORIZED=true\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$evidence_dir/preflight.env"

printf 'Requesting RAM-only Tokay boot of SHA-256 %s\n' "$candidate_sha"
"$fastboot_bin" boot "$candidate"

{
  printf 'LUMA_PIXEL9_TEMPORARY_BOOT_COMMAND_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'TEMPORARY_BOOT_COMMAND_ACCEPTED=true\n'
  printf 'NATIVE_KERNEL_BOOT_CONFIRMED=false\n'
  printf 'BOOT_AUTHORIZATION_CONSUMED=true\n'
  printf 'PARTITIONS_FLASHED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
  printf 'RELOCK_AUTHORIZED=false\n'
} >"$evidence_dir/command.env"

printf 'Tokay accepted the RAM-only boot request; physical native boot remains to be observed.\n'
