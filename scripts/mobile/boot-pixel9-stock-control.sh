#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# One-shot Tokay downloaded-boot transport control. This validates and
# RAM-boots only the exact signed stock boot.img extracted from the matching
# factory set. It never flashes, erases, changes slots, or relocks the phone.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

audit_dir=${1:?usage: boot-pixel9-stock-control.sh STOCK_BOOT_AUDIT_DIR RECOVERY_DIR EVIDENCE_DIR}
recovery_dir=${2:?usage: boot-pixel9-stock-control.sh STOCK_BOOT_AUDIT_DIR RECOVERY_DIR EVIDENCE_DIR}
evidence_dir=${3:?usage: boot-pixel9-stock-control.sh STOCK_BOOT_AUDIT_DIR RECOVERY_DIR EVIDENCE_DIR}
stock_boot=$audit_dir/images/boot.img
audit_manifest=$audit_dir/manifest.env
boot_summary=$audit_dir/metadata/boot.img.summary.env
boot_info=$audit_dir/metadata/boot.info.txt
factory=$recovery_dir/$PIXEL9_FACTORY_IMAGE_FILENAME
ota=$recovery_dir/$PIXEL9_FULL_OTA_FILENAME
fastboot_bin=${FASTBOOT:-fastboot}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "${LUMA_PIXEL9_STOCK_BOOT_CONTROL_AUTHORIZED:-0}" = 1 ] || \
  die 'one-shot stock-control authorization is absent; set LUMA_PIXEL9_STOCK_BOOT_CONTROL_AUTHORIZED=1'
[ ! -e "$evidence_dir" ] || die "refusing to replace evidence: $evidence_dir"
for tool in awk grep mkdir sed sha256sum tail tr wc "$fastboot_bin"; do
  command -v "$tool" >/dev/null 2>&1 || die "missing stock-control tool: $tool"
done

for required in \
  "$stock_boot" "$audit_manifest" "$boot_summary" "$boot_info" \
  "$factory" "$ota"; do
  [ -f "$required" ] || die "stock-control prerequisite is absent: $required"
done

grep -Fqx 'SCOPE=offline-stock-layout-read-only' "$audit_manifest" || \
  die 'stock audit does not retain its read-only scope'
grep -Fqx "FACTORY_SHA256=$PIXEL9_FACTORY_IMAGE_SHA256" "$audit_manifest" || \
  die 'stock audit differs from the active factory pin'
for boundary in \
  'FACTORY_FLASH_SCRIPTS_EXECUTED=false' \
  'PHONE_ACCESSED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  grep -Fqx "$boundary" "$audit_manifest" || \
    die "stock audit lacks boundary: $boundary"
done

stock_boot_bytes=$(wc -c <"$stock_boot" | tr -d '[:space:]')
stock_boot_sha=$(sha256sum "$stock_boot" | awk '{print $1}')
[ "$stock_boot_bytes" = "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || \
  die 'stock boot size differs from the active pin'
[ "$stock_boot_sha" = "$PIXEL9_STOCK_BOOT_IMAGE_SHA256" ] || \
  die 'stock boot digest differs from the active pin'
grep -Fqx "BYTES=$stock_boot_bytes" "$boot_summary" || \
  die 'stock boot size differs from its audit summary'
grep -Fqx "SHA256=$stock_boot_sha" "$boot_summary" || \
  die 'stock boot digest differs from its audit summary'
grep -Fqx 'AVB_INFO_AVAILABLE=true' "$boot_summary" || \
  die 'stock boot lacks its signed AVB audit'
grep -Fqx 'boot magic: ANDROID!' "$boot_info" || \
  die 'stock control is not an Android boot image'
grep -Fqx 'ramdisk size: 0' "$boot_info" || \
  die 'stock control unexpectedly contains a boot ramdisk'
grep -Fqx 'boot image header version: 4' "$boot_info" || \
  die 'stock control is not header version 4'

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
[ "$(getvar version-bootloader)" = "$PIXEL9_OBSERVED_BOOTLOADER" ] || \
  die 'bootloader version differs from the selected recovery set'
[ "$(getvar version-baseband)" = "$PIXEL9_RECOVERY_REQUIRED_BASEBAND" ] || \
  die 'baseband version differs from the selected recovery set'
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
[ "$stock_boot_bytes" -le "$max_download_dec" ] || \
  die 'stock boot image exceeds the bootloader download limit'

mkdir -p "$evidence_dir"
{
  printf 'LUMA_PIXEL9_STOCK_BOOT_CONTROL_ATTEMPT_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'OBSERVED_BUILD_ID=%s\n' "$PIXEL9_OBSERVED_BUILD_ID"
  printf 'STOCK_BOOT_BYTES=%s\n' "$stock_boot_bytes"
  printf 'STOCK_BOOT_SHA256=%s\n' "$stock_boot_sha"
  printf 'FACTORY_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'FULL_OTA_SHA256=%s\n' "$PIXEL9_FULL_OTA_SHA256"
  printf 'CURRENT_SLOT=%s\n' "$current_slot"
  printf 'MAX_DOWNLOAD_BYTES=%s\n' "$max_download_dec"
  printf 'BATTERY_SOC_OK=true\n'
  printf 'BOOTLOADER_UNLOCKED=true\n'
  printf 'STOCK_BOOT_CONTROL_AUTHORIZED=true\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$evidence_dir/preflight.env"

printf 'Requesting RAM-only Tokay stock control boot of SHA-256 %s\n' "$stock_boot_sha"
"$fastboot_bin" boot "$stock_boot"

{
  printf 'LUMA_PIXEL9_STOCK_BOOT_CONTROL_COMMAND_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'STOCK_BOOT_SHA256=%s\n' "$stock_boot_sha"
  printf 'TEMPORARY_BOOT_COMMAND_ACCEPTED=true\n'
  printf 'STOCK_OS_BOOT_CONFIRMED=false\n'
  printf 'STOCK_BOOT_CONTROL_AUTHORIZATION_CONSUMED=true\n'
  printf 'PARTITIONS_FLASHED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
  printf 'RELOCK_AUTHORIZED=false\n'
} >"$evidence_dir/command.env"

printf 'Tokay accepted the RAM-only signed-stock control request; the boot result remains to be observed.\n'
