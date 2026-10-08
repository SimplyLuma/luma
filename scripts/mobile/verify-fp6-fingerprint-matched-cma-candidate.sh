#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-matched-cma-candidate.sh BOOT_IMAGE KERNEL_BUNDLE}
bundle=${2:?missing kernel bundle}
kernel_release=$FP6_FINGERPRINT_MATCHED_CMA_KERNEL_RELEASE

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cpio fdtget find gzip head mktemp modinfo sha256sum stat tail tar unpack_bootimg wc zstd; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
[ "$FP6_FINGERPRINT_MATCHED_CMA_BUILD_READY" = true ] || die 'build gate closed'
[ "$FP6_FINGERPRINT_MATCHED_CMA_BOOT_CANDIDATE_READY" = true ] || die 'boot gate closed'
[ -f "$candidate" ] || die 'candidate missing'
[ -f "$bundle/modules-$kernel_release.tar.zst" ] || die 'module archive missing'
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_MATCHED_CMA_BOOT_SHA256" ] || die 'boot hash differs'
[ "$(stat -c %s "$candidate")" = "$FP6_FINGERPRINT_MATCHED_CMA_BOOT_SIZE" ] || die 'boot size differs'
[ "$(head -c "$FP6_FINGERPRINT_MATCHED_CMA_BOOT_PREFIX_SIZE" "$candidate" | sha256sum | awk '{print $1}')" = "$FP6_FINGERPRINT_MATCHED_CMA_BOOT_PREFIX_SHA256" ] || die 'boot prefix differs'
[ "$(tail -c 64 "$candidate" | sha256sum | awk '{print $1}')" = "$FP6_FINGERPRINT_MATCHED_CMA_AVB_FOOTER_SHA256" ] || die 'official AVB footer differs'
[ "$(hash "$bundle/modules-$kernel_release.tar.zst")" = "$FP6_FINGERPRINT_MATCHED_CMA_MODULE_TREE_SHA256" ] || die 'module archive differs'

work=$(mktemp -d)
cleanup() {
  [ ! -e "$work" ] || find "$work" -depth -delete
}
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/boot" "$work/ramdisk" "$work/modules"
unpack_bootimg --boot_img "$candidate" --out "$work/boot" >"$work/boot-info.txt"
[ "$(hash "$work/boot/kernel")" = "$FP6_FINGERPRINT_MATCHED_CMA_KERNEL_SHA256" ] || die 'kernel differs'
[ "$(hash "$work/boot/ramdisk")" = "$FP6_FINGERPRINT_MATCHED_CMA_RAMDISK_SHA256" ] || die 'ramdisk differs'
[ "$(stat -c %s "$work/boot/ramdisk")" -le "$FP6_FINGERPRINT_MATCHED_CMA_RAMDISK_MAX_SIZE" ] || die 'ramdisk exceeds physical boot budget'
[ "$(hash "$work/boot/dtb")" = "$FP6_FINGERPRINT_MATCHED_CMA_DTB_SHA256" ] || die 'DTB differs'

zstd -q -dc "$bundle/modules-$kernel_release.tar.zst" | tar -C "$work/modules" -xf -
module_root=$work/modules/lib/modules/$kernel_release
[ -d "$module_root" ] || die 'module root absent'
module_count=$(find "$module_root" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) | wc -l | tr -d ' ')
[ "$module_count" = "$FP6_FINGERPRINT_MATCHED_CMA_MODULE_COUNT" ] || die 'module count differs'
[ ! -e "$module_root/build" ] && [ ! -e "$module_root/source" ] || die 'module archive contains build links'
qsee_module=$module_root/kernel/drivers/tee/qseecom/qseecomtee.ko.zst
focal_module=$module_root/kernel/drivers/input/finger/focal_finger/focaltech_fp.ko.zst
[ -f "$qsee_module" ] && [ -f "$focal_module" ] || die 'fingerprint backend modules absent'
[ "$(zstd -q -dc "$qsee_module" | sha256sum | awk '{print $1}')" = "$FP6_FINGERPRINT_MATCHED_CMA_INSTALLED_QSEECOMTEE_KO_SHA256" ] || die 'installed QSEE module differs'
[ "$(zstd -q -dc "$focal_module" | sha256sum | awk '{print $1}')" = "$FP6_FINGERPRINT_MATCHED_CMA_INSTALLED_FOCALTECH_FP_KO_SHA256" ] || die 'installed FocalTech module differs'
for module in "$qsee_module" "$focal_module"; do
  [ "$(modinfo -F vermagic "$module" | cut -d ' ' -f 1)" = "$kernel_release" ] || die 'fingerprint module vermagic differs'
  [ "$(modinfo -F signer "$module")" = "$FP6_FINGERPRINT_MATCHED_CMA_MODULE_SIGNER" ] || die 'fingerprint module signer differs'
done

(
  cd "$work/ramdisk"
  gzip -dc "$work/boot/ramdisk" | cpio -idm --quiet
)
[ -d "$work/ramdisk/usr/lib/modules/$kernel_release" ] || die 'matching initramfs modules absent'
[ ! -e "$work/ramdisk/usr/lib/modules/7.1.2" ] || die 'old ABI modules remain in candidate initramfs'
initramfs_module_count=$(find "$work/ramdisk/usr/lib/modules/$kernel_release" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) | wc -l | tr -d ' ')
[ "$initramfs_module_count" = "$FP6_FINGERPRINT_MATCHED_CMA_INITRAMFS_MODULE_COUNT" ] || die 'initramfs module count differs'
grep -Fxq panel-novatek-nt37705 "$work/ramdisk/usr/lib/modules/initramfs.load" || die 'panel early-load entry absent'
grep -Fxq spi-geni-qcom "$work/ramdisk/usr/lib/modules/initramfs.load" || die 'SPI early-load entry absent'

dtb=$work/boot/dtb
apps=/reserved-memory/qseecom_region
ta=/reserved-memory/qseecom_ta_region
fdtget "$dtb" "$apps" reusable >/dev/null || die 'apps pool not reusable'
fdtget "$dtb" "$ta" reusable >/dev/null || die 'TA pool not reusable'
! fdtget "$dtb" "$apps" no-map >/dev/null 2>&1 || die 'apps no-map retained'
! fdtget "$dtb" "$ta" no-map >/dev/null 2>&1 || die 'TA no-map retained'
[ "$(fdtget -t x "$dtb" "$apps" size)" = '0 1400000' ] || die 'apps size differs'
[ "$(fdtget -t x "$dtb" "$ta" size)" = '0 1000000' ] || die 'TA size differs'
[ "$(fdtget "$dtb" /aliases qseecom_mem)" = "$apps" ] || die 'apps alias differs'
[ "$(fdtget "$dtb" /aliases qseecom_ta_mem)" = "$ta" ] || die 'TA alias differs'
fdtget "$dtb" /soc@0/qseecom@c1700000 qcom,appsbl-qseecom-support >/dev/null || die 'AppsBL marker absent'
fdtget "$dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null || die 'dedicated heaps absent'
! fdtget "$dtb" /firmware/scm memory-region >/dev/null 2>&1 || die 'global SCM pool reintroduced'

printf 'FP6 fingerprint matched-CMA candidate verified: %s\n' "$candidate"
