#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-nfc.env"

builder=$repo_root/scripts/mobile/build-fp6-nfc-candidate.sh
repacker=$repo_root/scripts/mobile/prepare-fp6-nfc-boot-candidate.sh
installer=$repo_root/scripts/mobile/install-fp6-nfc-modules.sh
calibration_installer=$repo_root/scripts/mobile/install-fp6-nfc-calibration.sh
inspector=$repo_root/scripts/mobile/inspect-fp6-nfc-reader.sh
acceptor=$repo_root/scripts/mobile/accept-fp6-nfc-reader.sh
bash -n "$builder" "$repacker" "$installer" "$calibration_installer" "$inspector" "$acceptor"

[ "$FP6_NFC_UPSTREAM_BASE_COMMIT" = dfe0125e73541d1984e4d2d37bd069a036bf0451 ]
[ "$FP6_NFC_UPSTREAM_COMBINED_COMMIT" = af49850e65bdf5dc6f4a14fb9e8e5c5814522d33 ]
[ "$FP6_NFC_CONTROLLER" = samsung-s3nrn4v ]
[ "$FP6_NFC_STOCK_VENDOR_IMAGE_SHA256" = 4159a51642c7f6462012891197a237ad86768a1f55853c6ab3b38d6ff2b144b4 ]
[ "$FP6_NFC_HWREG_SHA256" = c3f3f463ab8c129b288cf93a5224a0064d0429158eb622b1a2064768d25fd021 ]
[ "$FP6_NFC_SWREG_SHA256" = d3f9fedfa3c8ec8a1f4412104c4e899819c33b5ec618b23fce2912554d8368bb ]
[ "$FP6_NFC_LOCAL_STOCK_PROVENANCE_ACCEPTED" = true ]
[ "$FP6_NFC_REDISTRIBUTION_ACCEPTED" = false ]
[ "$FP6_NFC_FIRMWARE_PROVENANCE_ACCEPTED" = false ]
[ "$FP6_NFC_READER_CANDIDATE_READY" = true ]
[ "$FP6_NFC_RAM_BOOT_EXECUTED" = true ]
[ "$FP6_NFC_ENUMERATION_ACCEPTED" = true ]
[ "$FP6_NFC_READER_TOOL_PACKAGE" = neard-tools-0.19-8.fc44.aarch64 ]
[ "$FP6_NFC_LOCAL_CALIBRATION_INSTALLED" = true ]
[ "$FP6_NFC_EMPTY_FIELD_POLL_ACCEPTED" = true ]
[ "$FP6_NFC_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_NFC_TARGET_DETECTION_REPRODUCED" = true ]
[ "$FP6_NFC_PAYLOAD_READ_ACCEPTED" = false ]
[ "$FP6_NFC_WRITE_OR_CARD_EMULATION_ACCEPTED" = false ]
[ "$FP6_NFC_BOOT_SHA256" = 1f7ec55cc841a35e7c7971178b0370f574ca18a1e71635c5c235cd76f5bebf7e ]
[ "$FP6_NFC_S3FWRN5_I2C_KO_SHA256" = df905d9ad4240b708a11c029af98344fa7d7ea23284ef0eb21f1655c6e67746a ]

[ "$(find "$repo_root/patches/linux-milos-nfc" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')" = 7 ]
grep -Fq 'compatible = "samsung,s3nrn4v"' \
  "$repo_root/patches/linux-milos-nfc/0005-arm64-dts-qcom-fp6-refresh-NFC-node-v4.patch"
grep -Fq 'S3FWRN5_VARIANT_S3NRN4V' \
  "$repo_root/patches/linux-milos-nfc/0006-nfc-s3fwrn5-refresh-S3NRN4V-v4.patch"
grep -Fq 'samsung/s3nrn4v/hwreg.bin' \
  "$repo_root/patches/linux-milos-nfc/0004-nfc-s3fwrn5-support-S3NRN4V-variant.patch"
grep -Fq 'samsung/s3nrn4v/swreg.bin' \
  "$repo_root/patches/linux-milos-nfc/0004-nfc-s3fwrn5-support-S3NRN4V-variant.patch"
grep -Fq 'firmware_request_nowarn' \
  "$repo_root/patches/linux-milos-nfc/0004-nfc-s3fwrn5-support-S3NRN4V-variant.patch"
grep -Fq 'extent-backed regular files/directories' \
  "$repo_root/scripts/mobile/extract-ext4-files.py"

[ "$(sha256sum "$repo_root/config/mobile/fp6-nfc/milos-fairphone-fp6-nfc.dtso" | cut -d ' ' -f 1)" = \
  "$FP6_NFC_OVERLAY_SHA256" ]
grep -Fq 'target-path = "/soc@0/geniqup@ac0000/i2c@a84000"' \
  "$repo_root/config/mobile/fp6-nfc/milos-fairphone-fp6-nfc.dtso"
grep -Fq 'RAM_BOOT_ONLY=true' "$builder"
grep -Fq 'PHONE_ACCESSED=false' "$builder"
grep -Fq "refuse to compile on the FP6 target" "$builder"
grep -Fq 'cmp "$base_dir/kernel" "$candidate_dir/kernel"' "$repacker"
grep -Fq 'cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"' "$repacker"
grep -Fq 'HEADER_ROUNDTRIP_EXACT=true' "$repacker"
grep -Fq 'MODULES_STAGED=false' "$repacker"
if grep -Eiq '(^|[[:space:]])(adb|fastboot|scp|ssh)([[:space:]]|$)' "$builder" "$repacker"; then
  printf 'FAIL: offline NFC tools must not contact a device\n' >&2
  exit 1
fi
grep -Fq 'modules_loaded=false' "$installer"
grep -Fq "$FP6_NFC_S3FWRN5_I2C_KO_SHA256" "$installer"
grep -Fq "$FP6_NFC_S3FWRN5_I2C_KO_SHA256" "$inspector"
grep -Fq 'services_restarted=false' "$installer"
grep -Fq 'nfc_polled=false' "$installer"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|systemctl|reboot|fastboot)([[:space:]]|$)' "$installer"; then
  printf 'FAIL: NFC installer must not load, restart, poll, or boot\n' >&2
  exit 1
fi
grep -Fq "$FP6_NFC_HWREG_SHA256" "$calibration_installer"
grep -Fq "$FP6_NFC_SWREG_SHA256" "$calibration_installer"
grep -Fq 'REDISTRIBUTABLE=false' "$calibration_installer"
grep -Fq 'NFC_POWERED=false' "$calibration_installer"
grep -Fq 'NFC_POLLED=false' "$calibration_installer"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|nfctool|systemctl|reboot|fastboot)([[:space:]]|$)' "$calibration_installer"; then
  printf 'FAIL: calibration installer must not load, power, poll, restart, or boot\n' >&2
  exit 1
fi
grep -Fq 'TAG_POLLED=false' "$inspector"
grep -Fq 'MUTATING_OPERATIONS=none' "$inspector"
if grep -Eiq '^[[:space:]]*(insmod|modprobe|rmmod|nfctool|nfc-poll|systemctl[[:space:]]+(start|stop|restart)|reboot|fastboot)([[:space:]]|$)' "$inspector"; then
  printf 'FAIL: NFC inspector must remain read-only\n' >&2
  exit 1
fi
grep -Fq 'POLL_MODE=initiator-read-only' "$acceptor"
grep -Fq 'IDENTIFIERS_EXPOSED=false' "$acceptor"
grep -Fq 'PAYLOADS_READ=false' "$acceptor"
grep -Fq 'WRITES_ATTEMPTED=false' "$acceptor"
grep -Fq 'TARGET_DETECTED=' "$acceptor"
grep -Fq 'Targets found for nfc' "$acceptor"
grep -Fq 'nfctool -d nfc0 -0' "$acceptor"
if grep -Eiq 'nfctool[^\n]*(--fw-download|--set-param|--sniff|--snep-sap)' "$acceptor"; then
  printf 'FAIL: NFC acceptance must remain a passive tag-presence test\n' >&2
  exit 1
fi

if find "$repo_root" -path "$repo_root/.git" -prune -o -type f \
  \( -name hwreg.bin -o -name swreg.bin \) -print | grep -q .; then
  printf 'error: non-redistributable S3NRN4V calibration payload is present in the repository\n' >&2
  exit 1
fi

printf 'Mobile FP6 NFC source and firmware gate: PASS\n'
