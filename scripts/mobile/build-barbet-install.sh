#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Assemble the full barbet test set from the shared ARM userdata image and a
# boot image built from the Luma sm7250 tag, with SHA256SUMS and the exact
# install commands. Never flashes and never overwrites an existing set.
#
# usage: build-barbet-install.sh --userdata FILE --userdata-sha256 SHA256
#          --boot-image FILE --output DIR
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'build-barbet-install: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

userdata="" userdata_sha="" boot_image="" output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --userdata) userdata=$(realpath -e "${2:?}"); shift 2 ;;
    --userdata-sha256) userdata_sha=${2:?}; shift 2 ;;
    --boot-image) boot_image=$(realpath -e "${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$userdata" ] && [ -n "$userdata_sha" ] && [ -n "$boot_image" ] && [ -n "$output" ] || usage
[ ! -e "$output" ] || fail "output exists; never overwrite a set: $output"

actual=$(sha256sum "$userdata" | cut -d' ' -f1)
[ "$actual" = "$userdata_sha" ] ||
  fail "userdata SHA-256 mismatch: got $actual"
partition_size=100663296

work=$(mktemp -d "${TMPDIR:-/tmp}/barbet-install.XXXXXX")
trap 'rm -rf "$work"' EXIT
umask 022

[ -n "$boot_image" ] || fail "--boot-image is required"
[ "$(stat -c %s "$boot_image")" = "$partition_size" ] ||
  fail "boot image is not the $partition_size-byte boot_b image"

mkdir -p "$output"
cp "$boot_image" "$output/boot.img"
cp --reflink=auto "$userdata" "$output/userdata.img"
(cd "$output" && sha256sum boot.img userdata.img > SHA256SUMS)

cat >"$output/INSTALL.txt" <<'EOF'
Pixel 5a (barbet) full install. This erases Android and userdata.
Preserved: boot_a (Android), modemst1, modemst2, fsg, fsc, persist, dtbo, vbmeta,
bootloader, super, the partition table and slot metadata beyond selecting slot b.

  fastboot flash userdata userdata.img
  fastboot flash boot_b boot.img
  fastboot --set-active=b
  fastboot reboot

After the first boot, place the operator-extracted firmware in the shared
store /var/lib/luma/device-firmware: the GPU zap/gmu/sqe files, modem.mdt and
its segments, wlanmdsp.mbn, bdwlan.bin and
ath10k/WCN3990/hw1.0/{board-2.bin,firmware-5.bin}. Put the ADSP and the
RT5514P DSP blobs in /var/lib/luma/device-firmware-barbet (the RT5514P files
are also accepted from the shared store). Never write boot_a, dtbo, vbmeta,
modemst*, fsg, fsc, persist, super, the bootloader or the partition table.
EOF
printf 'SET=%s\nBOOT_SHA256=%s\nUSERDATA_SHA256=%s\n' "$output" \
  "$(sha256sum "$output/boot.img" | cut -d' ' -f1)" "$userdata_sha"
