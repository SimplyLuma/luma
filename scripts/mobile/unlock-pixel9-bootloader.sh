#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# One-shot Tokay T4 unlock workflow. Unlocking erases all user data. This
# script validates the exact recovery set and offline candidate before touching
# the phone, then performs only the unlock transaction. It never boots or
# flashes the candidate and leaves those later gates closed.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

candidate_dir=${1:?usage: unlock-pixel9-bootloader.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
recovery_dir=${2:?usage: unlock-pixel9-bootloader.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
evidence_dir=${3:?usage: unlock-pixel9-bootloader.sh CANDIDATE_DIR RECOVERY_DIR EVIDENCE_DIR}
candidate=$candidate_dir/wrapper-proof/pixel9-read-only-ramboot.img
candidate_manifest=$candidate_dir/wrapper-proof/manifest.env
reproducibility=$candidate_dir/reproducibility.env
factory=$recovery_dir/$PIXEL9_FACTORY_IMAGE_FILENAME
ota=$recovery_dir/$PIXEL9_FULL_OTA_FILENAME
adb_bin=${ADB:-adb}
fastboot_bin=${FASTBOOT:-fastboot}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "${LUMA_PIXEL9_UNLOCK_AUTHORIZED:-0}" = 1 ] || \
  die 'one-shot unlock authorization is absent; set LUMA_PIXEL9_UNLOCK_AUTHORIZED=1'
[ ! -e "$evidence_dir" ] || die "refusing to replace evidence: $evidence_dir"
for tool in awk grep head mkdir sed sha256sum sleep tail tr wc "$adb_bin" "$fastboot_bin"; do
  command -v "$tool" >/dev/null 2>&1 || die "missing unlock tool: $tool"
done

for required in "$candidate" "$candidate_manifest" "$reproducibility" "$factory" "$ota"; do
  [ -f "$required" ] || die "unlock prerequisite is absent: $required"
done
grep -Fqx 'COMPARISON=byte-for-byte' "$reproducibility" || \
  die 'candidate does not have a byte-for-byte reproducibility proof'
grep -Fqx 'WRAPPER_IDENTICAL=true' "$reproducibility" || \
  die 'candidate wrapper reproducibility is incomplete'
candidate_sha=$(sha256sum "$candidate" | awk '{print $1}')
grep -Fqx "WRAPPER_SHA256=$candidate_sha" "$candidate_manifest" || \
  die 'candidate wrapper differs from its manifest'
grep -Fqx 'ROUNDTRIP_KERNEL_IDENTICAL=true' "$candidate_manifest" || \
  die 'candidate U-Boot round trip is incomplete'
grep -Fqx 'ROUNDTRIP_RAMDISK_IDENTICAL=true' "$candidate_manifest" || \
  die 'candidate FIT round trip is incomplete'
grep -Fqx 'PHONE_ACCESSED=false' "$candidate_manifest" || \
  die 'candidate was not built within the no-phone boundary'
grep -Fqx 'BOOT_AUTHORIZED=false' "$candidate_manifest" || \
  die 'candidate manifest unexpectedly opens the boot gate'
grep -Fqx 'FLASH_AUTHORIZED=false' "$candidate_manifest" || \
  die 'candidate manifest unexpectedly opens the flash gate'

[ "$(wc -c <"$factory" | tr -d '[:space:]')" = "$PIXEL9_FACTORY_IMAGE_BYTES" ] || \
  die 'factory recovery size differs from the pin'
[ "$(sha256sum "$factory" | awk '{print $1}')" = "$PIXEL9_FACTORY_IMAGE_SHA256" ] || \
  die 'factory recovery digest differs from the pin'
[ "$(wc -c <"$ota" | tr -d '[:space:]')" = "$PIXEL9_FULL_OTA_BYTES" ] || \
  die 'full OTA size differs from the pin'
[ "$(sha256sum "$ota" | awk '{print $1}')" = "$PIXEL9_FULL_OTA_SHA256" ] || \
  die 'full OTA digest differs from the pin'

adb_rows=$($adb_bin devices | awk 'NR > 1 && $2 == "device" {count++} END {print count+0}')
[ "$adb_rows" -eq 1 ] || die 'expected exactly one authorized ADB device'
device=$($adb_bin shell getprop ro.product.device | tr -d '\r')
model=$($adb_bin shell getprop ro.product.model | tr -d '\r')
[ "$device" = tokay ] || die 'authorized ADB device is not tokay'
[ "$model" = 'Pixel 9' ] || die 'authorized ADB model is not Pixel 9'
battery=$($adb_bin shell dumpsys battery | sed -n 's/^[[:space:]]*level: //p' | head -1 | tr -d '\r')
case "$battery" in
  ''|*[!0-9]*) die 'could not validate Pixel battery level' ;;
esac
[ "$battery" -ge 80 ] || die "Pixel battery must be at least 80%; observed $battery%"

mkdir -p "$evidence_dir"
{
  printf 'LUMA_PIXEL9_UNLOCK_ATTEMPT_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'MODEL=Pixel 9\n'
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'FACTORY_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'FULL_OTA_SHA256=%s\n' "$PIXEL9_FULL_OTA_SHA256"
  printf 'BATTERY_PERCENT=%s\n' "$battery"
  printf 'UNLOCK_AUTHORIZED=true\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$evidence_dir/preflight.env"

$adb_bin reboot bootloader

deadline=$((SECONDS + 45))
while [ "$SECONDS" -lt "$deadline" ]; do
  fastboot_rows=$($fastboot_bin devices 2>/dev/null | awk 'NF {count++} END {print count+0}')
  [ "$fastboot_rows" -eq 1 ] && break
  sleep 1
done
[ "${fastboot_rows:-0}" -eq 1 ] || die 'exactly one fastboot device did not appear'

getvar() {
  "$fastboot_bin" getvar "$1" 2>&1 | sed -n "s/.*$1: //p" | tail -1 | tr -d '\r'
}

[ "$(getvar product)" = tokay ] || die 'fastboot target is not tokay'
[ "$(getvar unlocked)" = no ] || die 'bootloader is not in the expected locked state'
[ "$(getvar slot-successful:a)" = yes ] || die 'stock slot A is not successful'
[ "$(getvar slot-unbootable:a)" = no ] || die 'stock slot A is unbootable'
[ "$(getvar slot-successful:b)" = yes ] || die 'stock slot B is not successful'
[ "$(getvar slot-unbootable:b)" = no ] || die 'stock slot B is unbootable'

printf 'Pixel 9 unlock confirmation is now required on the phone. This erases all user data.\n'
"$fastboot_bin" flashing unlock

[ "$(getvar unlocked)" = yes ] || die 'bootloader did not report unlocked after confirmation'
{
  printf 'LUMA_PIXEL9_UNLOCK_COMPLETION_VERSION=1\n'
  printf 'DEVICE=tokay\n'
  printf 'BOOTLOADER_UNLOCKED=true\n'
  printf 'USERDATA_WIPE_EXPECTED=true\n'
  printf 'UNLOCK_AUTHORIZATION_CONSUMED=true\n'
  printf 'CANDIDATE_BOOTED=false\n'
  printf 'PARTITIONS_FLASHED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$evidence_dir/completion.env"

printf 'Pixel 9 bootloader unlock complete; candidate boot and flashing remain unauthorized.\n'
