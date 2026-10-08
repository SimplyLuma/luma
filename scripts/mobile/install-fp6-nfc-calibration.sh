#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install only owner-supplied, hash-pinned stock FP6 calibration bytes.  This
# script deliberately does not load modules, power NFC, poll, or restart a
# service; controller activation remains a separate physical acceptance step.

set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] ||
  die 'device identity differs'
[ "$#" -eq 1 ] || die 'usage: install-fp6-nfc-calibration.sh STAGING_DIRECTORY'
stage=$1
[ -d "$stage" ] || die 'staging directory is absent'

hw_source=$stage/sec_s3nrn4v_hwreg.bin
sw_source=$stage/sec_s3nrn4v_swreg.bin
hw_hash=c3f3f463ab8c129b288cf93a5224a0064d0429158eb622b1a2064768d25fd021
sw_hash=d3f9fedfa3c8ec8a1f4412104c4e899819c33b5ec618b23fce2912554d8368bb
[ -f "$hw_source" ] && [ "$(hash "$hw_source")" = "$hw_hash" ] ||
  die 'stock HW calibration hash differs'
[ -f "$sw_source" ] && [ "$(hash "$sw_source")" = "$sw_hash" ] ||
  die 'stock SW calibration hash differs'
[ "$(wc -c <"$hw_source" | tr -d ' ')" = 3232 ] || die 'HW calibration size differs'
[ "$(wc -c <"$sw_source" | tr -d ' ')" = 336 ] || die 'SW calibration size differs'

destination=/usr/lib/firmware/samsung/s3nrn4v
if [ -e "$destination/hwreg.bin" ] || [ -e "$destination/swreg.bin" ]; then
  die 'destination already contains NFC calibration; refusing to overwrite'
fi
install -d -m 0755 "$destination"
install -m 0644 "$hw_source" "$destination/hwreg.bin"
install -m 0644 "$sw_source" "$destination/swreg.bin"
sync "$destination/hwreg.bin" "$destination/swreg.bin"
[ "$(hash "$destination/hwreg.bin")" = "$hw_hash" ] || die 'installed HW hash differs'
[ "$(hash "$destination/swreg.bin")" = "$sw_hash" ] || die 'installed SW hash differs'

printf 'LUMA_FP6_NFC_CALIBRATION_INSTALL_VERSION=1\n'
printf 'SOURCE=owner-stock-vendor-image\n'
printf 'REDISTRIBUTABLE=false\n'
printf 'FILES_INSTALLED=2\n'
printf 'MODULES_LOADED=false\n'
printf 'NFC_POWERED=false\n'
printf 'NFC_POLLED=false\n'
printf 'SERVICES_RESTARTED=false\n'
printf 'PARTITIONS_WRITTEN=false\n'

