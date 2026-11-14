#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Assemble the full barbet test set: the boot image from the source lock and the
# shared ARM userdata image, with SHA256SUMS and the exact install commands.
# Builds only the device parts; the OS userdata is the shared image. Never
# flashes and never overwrites an existing set.
#
# usage: build-barbet-install.sh --userdata FILE --userdata-sha256 SHA256
#          --output DIR [--boot-image FILE]
#          [--cache DIR --alpha-boot FILE]   # when --boot-image is absent
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
kernel_build="$repo_root/scripts/kernel/build-barbet.sh"
boot_build="$repo_root/scripts/mobile/build-barbet-boot-image.sh"
fail() { printf 'build-barbet-install: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

userdata="" userdata_sha="" boot_image="" cache="" alpha="" output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --userdata) userdata=$(realpath -e "${2:?}"); shift 2 ;;
    --userdata-sha256) userdata_sha=${2:?}; shift 2 ;;
    --boot-image) boot_image=$(realpath -e "${2:?}"); shift 2 ;;
    --cache) cache=$(realpath -e "${2:?}"); shift 2 ;;
    --alpha-boot) alpha=$(realpath -e "${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$userdata" ] && [ -n "$userdata_sha" ] && [ -n "$output" ] || usage
[ ! -e "$output" ] || fail "output exists; never overwrite a set: $output"

actual=$(sha256sum "$userdata" | cut -d' ' -f1)
[ "$actual" = "$userdata_sha" ] ||
  fail "userdata SHA-256 mismatch: got $actual"
partition_size=100663296

work=$(mktemp -d "${TMPDIR:-/tmp}/barbet-install.XXXXXX")
trap 'rm -rf "$work"' EXIT
umask 022

if [ -z "$boot_image" ]; then
  [ -n "$cache" ] && [ -n "$alpha" ] ||
    fail "--cache and --alpha-boot are required to build the boot image"
  "$kernel_build" --cache "$cache" "$work/kernel" >/dev/null
  "$boot_build" --kernel "$work/kernel/Image" \
    --dtb "$work/kernel/qcom/sm7250-google-barbet.dtb" \
    --alpha-boot "$alpha" --output "$work/boot.img" >/dev/null
  boot_image="$work/boot.img"
fi
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

After the first boot, place the operator-extracted firmware in
/var/lib/luma/device-firmware (ADSP, GPU, WCN3990, RT5514P DSP and
board-2) before testing audio or Wi-Fi. Never write boot_a, dtbo, vbmeta, modemst*,
fsg, fsc, persist, super, the bootloader or the partition table.
EOF
printf 'SET=%s\nBOOT_SHA256=%s\nUSERDATA_SHA256=%s\n' "$output" \
  "$(sha256sum "$output/boot.img" | cut -d' ' -f1)" "$userdata_sha"
