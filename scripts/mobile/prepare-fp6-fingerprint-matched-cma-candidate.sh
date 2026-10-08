#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose the rollback-safe FP6 matched-CMA boot candidate. It combines the
# v13 accepted boot header with a unique-release kernel, a matching initramfs
# module subset, and the exact stock reusable QSEECom pool semantics.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-matched-cma-candidate.sh V13_BOOT KERNEL_BUNDLE OUTPUT_DIR}
bundle=${2:?missing kernel bundle}
output_dir=${3:?missing output directory}
manifest=$bundle/manifest.env
kernel_release=${LUMA_MATCHED_CMA_KERNEL_RELEASE_OVERRIDE:-7.1.2-luma-fp-cma1}
old_kernel_release=${LUMA_MATCHED_CMA_OLD_KERNEL_RELEASE_OVERRIDE:-7.1.2}
candidate_name=${LUMA_MATCHED_CMA_CANDIDATE_NAME:-boot-fp6-luma-fingerprint-matched-cma-v14.img}
candidate=$output_dir/$candidate_name
signing_key=${LUMA_MATCHED_CMA_SIGNING_KEY:-}
source_tree=${LUMA_MATCHED_CMA_SOURCE_TREE:-}
signing_key_sha=94f151fcd2cec29112beb22cf226ea9cc2acb5f000de05b518ffab7657e14220
expected_signing_key_sha=${LUMA_MATCHED_CMA_SIGNING_KEY_SHA256_OVERRIDE:-$signing_key_sha}
expected_base_boot_sha=${LUMA_MATCHED_CMA_BASE_BOOT_SHA256_OVERRIDE:-$FP6_FINGERPRINT_STOCK_NODE_NAMES_BOOT_SHA256}
expected_base_kernel_sha=${LUMA_MATCHED_CMA_BASE_KERNEL_SHA256_OVERRIDE:-$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256}
expected_base_ramdisk_sha=${LUMA_MATCHED_CMA_BASE_RAMDISK_SHA256_OVERRIDE:-$FP6_FINGERPRINT_BASE_RAMDISK_SHA256}
expected_base_dtb_sha=${LUMA_MATCHED_CMA_BASE_DTB_SHA256_OVERRIDE:-$FP6_FINGERPRINT_STOCK_NODE_NAMES_DTB_SHA256}
preserve_dtb=${LUMA_MATCHED_CMA_PRESERVE_DTB:-false}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cc cmp cpio fdtget fdtput file find gzip install llvm-strip mkbootimg \
  mktemp modinfo python3 sha256sum sort stat tar touch unpack_bootimg zstd; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$base_boot" "$bundle/Image.gz" "$bundle/config" \
  "$bundle/qseecomtee.ko" "$bundle/focaltech_fp.ko" \
  "$bundle/modules-$kernel_release.tar.zst" "$manifest"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ -f "$signing_key" ] && [ ! -L "$signing_key" ] || die 'reproducible signing key absent'
[ "$(hash "$signing_key")" = "$expected_signing_key_sha" ] || die 'reproducible signing key differs'
[ -f "$source_tree/scripts/sign-file.c" ] || die 'kernel sign-file source absent'
case "$preserve_dtb" in true|false) ;; *) die 'invalid DTB preservation setting' ;; esac
[ "$(hash "$base_boot")" = "$expected_base_boot_sha" ] || die 'accepted base boot differs'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'kernel bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "$KERNEL_RELEASE" = "$kernel_release" ] || die 'kernel release differs'
[ "$DMA_CMA_ENABLED" = true ] || die 'DMA-CMA gate closed'
[ "$CMA_SIZE_MBYTES" = 32 ] || die 'global CMA size differs'
[ "$TZMEM_MODE" = shmbridge ] || die 'SHM-Bridge mode absent'
[ "$DEDICATED_QSEECOM_HEAPS" = true ] || die 'dedicated heaps absent'
[ "$COMPLETE_MATCHING_MODULE_TREE" = true ] || die 'module tree incomplete'
[ "$(hash "$bundle/Image.gz")" = "$IMAGE_SHA256" ] || die 'kernel hash differs from manifest'
[ "$(hash "$bundle/config")" = "$CONFIG_SHA256" ] || die 'config hash differs from manifest'
[ "$(hash "$bundle/qseecomtee.ko")" = "$QSEECOMTEE_KO_SHA256" ] || die 'QSEE module hash differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FOCALTECH_FP_KO_SHA256" ] || die 'FocalTech module hash differs'
[ "$(hash "$bundle/modules-$kernel_release.tar.zst")" = "$MODULE_TREE_SHA256" ] || die 'module archive hash differs'

run_mkbootimg_args0() {
  local args_file=$1 output=$2
  python3 - "$args_file" "$output" <<'PY'
import os
import subprocess
import sys

args_file, output = sys.argv[1:]
with open(args_file, "rb") as stream:
    args = stream.read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
subprocess.run(
    ["mkbootimg", "--output", output, *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY
}

mkdir -p "$output_dir"
work=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() {
  [ ! -e "$work" ] || find "$work" -depth -delete
}
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/base" "$work/base-verify" "$work/roundtrip" \
  "$work/candidate" "$work/ramdisk" "$work/modules"
cc -O2 -I "$source_tree/tools/include/uapi" -o "$work/sign-file" \
  "$source_tree/scripts/sign-file.c" -lcrypto
file "$work/sign-file" | grep -Fq 'ARM aarch64' || die 'native sign-file architecture differs'
args_file=$work/mkbootimg.args0
roundtrip=$work/base-roundtrip.img
matched_dtb=$output_dir/milos-fairphone-fp6-fingerprint-matched-cma-v14.dtb
matched_ramdisk=$output_dir/ramdisk-fp6-fingerprint-matched-cma-v14.cpio.gz

unpack_bootimg --boot_img "$base_boot" --out "$work/base-verify" >"$work/base-info.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
unpack_bootimg --boot_img "$roundtrip" --out "$work/roundtrip" >"$work/roundtrip-info.txt"
cmp "$work/base-info.txt" "$work/roundtrip-info.txt"
cmp "$work/base-verify/kernel" "$work/roundtrip/kernel"
cmp "$work/base-verify/ramdisk" "$work/roundtrip/ramdisk"
cmp "$work/base-verify/dtb" "$work/roundtrip/dtb"
[ "$(hash "$work/base/kernel")" = "$expected_base_kernel_sha" ] || die 'base kernel differs'
[ "$(hash "$work/base/ramdisk")" = "$expected_base_ramdisk_sha" ] || die 'base ramdisk differs'
[ "$(hash "$work/base/dtb")" = "$expected_base_dtb_sha" ] || die 'base DTB differs'

zstd -q -dc "$bundle/modules-$kernel_release.tar.zst" | tar -C "$work/modules" -xf -
new_modules=$work/modules/lib/modules/$kernel_release
[ -d "$new_modules" ] || die 'new module root absent'

(
  cd "$work/ramdisk"
  gzip -dc "$work/base/ramdisk" | cpio -idm --quiet
)
old_modules=$work/ramdisk/usr/lib/modules/$old_kernel_release
ramdisk_modules=$work/ramdisk/usr/lib/modules/$kernel_release
[ -d "$old_modules" ] || die 'old initramfs module root absent'
mkdir -p "$ramdisk_modules"
initramfs_module_count=0
while IFS= read -r old_module; do
  relative=${old_module#"$old_modules"/}
  [ -f "$new_modules/$relative" ] || die "matching initramfs module absent: $relative"
  case "$relative" in
    *.ko.zst) ;;
    *) die "unsupported initramfs module compression: $relative" ;;
  esac
  module_work=$work/initramfs-module.ko
  zstd -q -dc "$new_modules/$relative" >"$module_work"
  llvm-strip --strip-debug "$module_work"
  "$work/sign-file" sha256 "$signing_key" "$signing_key" "$module_work"
  [ "$(modinfo -F vermagic "$module_work" | cut -d' ' -f1)" = "$kernel_release" ] ||
    die "stripped initramfs module vermagic differs: $relative"
  [ "$(modinfo -F signer "$module_work")" = 'Build time autogenerated kernel key' ] ||
    die "stripped initramfs module signer differs: $relative"
  mkdir -p "$(dirname "$ramdisk_modules/$relative")"
  zstd -q -19 -T1 -f "$module_work" -o "$ramdisk_modules/$relative"
  chmod 0644 "$ramdisk_modules/$relative"
  initramfs_module_count=$((initramfs_module_count + 1))
done < <(find "$old_modules" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) | LC_ALL=C sort)
[ "$initramfs_module_count" -eq 16 ] || die 'initramfs module count differs'
for metadata in "$new_modules"/modules.*; do
  [ -f "$metadata" ] || continue
  install -m 0644 "$metadata" "$ramdisk_modules/$(basename "$metadata")"
done
find "$old_modules" -depth -delete
find "$work/ramdisk" -exec touch -h -d '2026-08-22 00:00:00 UTC' {} +
(
  cd "$work/ramdisk"
  find . -print0 | LC_ALL=C sort -z |
    cpio --null --create --format=newc --owner=0:0 --reproducible --quiet |
    gzip -n >"$matched_ramdisk"
)
[ "$(stat -c %s "$matched_ramdisk")" -le $((24 * 1024 * 1024)) ] ||
  die 'stripped initramfs exceeds 24 MiB'

install -m 0644 "$work/base/dtb" "$matched_dtb"
if [ "$preserve_dtb" = false ]; then
  apps=/reserved-memory/qseecom_region
  ta=/reserved-memory/qseecom_ta_region
  fdtput -d "$matched_dtb" "$apps" no-map
  fdtput -d "$matched_dtb" "$ta" no-map
  fdtput "$matched_dtb" "$apps" reusable
  fdtput "$matched_dtb" "$ta" reusable
  fdtget "$matched_dtb" "$apps" reusable >/dev/null || die 'apps pool is not reusable'
  fdtget "$matched_dtb" "$ta" reusable >/dev/null || die 'TA pool is not reusable'
  ! fdtget "$matched_dtb" "$apps" no-map >/dev/null 2>&1 || die 'apps no-map retained'
  ! fdtget "$matched_dtb" "$ta" no-map >/dev/null 2>&1 || die 'TA no-map retained'
  [ "$(fdtget -t x "$matched_dtb" "$apps" size)" = '0 1400000' ] || die 'apps size differs'
  [ "$(fdtget -t x "$matched_dtb" "$ta" size)" = '0 1000000' ] || die 'TA size differs'
  [ "$(fdtget -t x "$matched_dtb" "$apps" alignment)" = '0 400000' ] || die 'apps alignment differs'
  [ "$(fdtget -t x "$matched_dtb" "$ta" alignment)" = '0 400000' ] || die 'TA alignment differs'
  [ "$(fdtget "$matched_dtb" /aliases qseecom_mem)" = "$apps" ] || die 'apps alias differs'
  [ "$(fdtget "$matched_dtb" /aliases qseecom_ta_mem)" = "$ta" ] || die 'TA alias differs'
  fdtget "$matched_dtb" /soc@0/qseecom@c1700000 qcom,appsbl-qseecom-support >/dev/null || die 'AppsBL marker absent'
  fdtget "$matched_dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null || die 'dedicated heaps absent'
  ! fdtget "$matched_dtb" /firmware/scm memory-region >/dev/null 2>&1 || die 'global SCM pool reintroduced'
else
  [ "$(hash "$matched_dtb")" = "$expected_base_dtb_sha" ] || die 'preserved DTB differs'
fi

install -m 0644 "$bundle/Image.gz" "$work/base/kernel"
install -m 0644 "$matched_ramdisk" "$work/base/ramdisk"
install -m 0644 "$matched_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$output_dir/boot-info.txt"
cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$matched_ramdisk" "$work/candidate/ramdisk"
cmp "$matched_dtb" "$work/candidate/dtb"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] || die 'candidate exceeds boot partition'

{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=14\n'
  printf 'SCOPE=fp6-fingerprint-matched-cma\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$expected_base_boot_sha"
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'KERNEL_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'DTB_SHA256=%s\n' "$(hash "$matched_dtb")"
  printf 'RAMDISK_SHA256=%s\n' "$(hash "$matched_ramdisk")"
  printf 'MODULE_TREE_SHA256=%s\n' "$MODULE_TREE_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
  printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'UNIQUE_KERNEL_RELEASE=true\n'
  printf 'ROLLBACK_MODULE_TREE_PRESERVED=true\n'
  printf 'MATCHED_INITRAMFS_MODULES=true\n'
  printf 'INITRAMFS_MODULES_STRIPPED_RESIGNED=true\n'
  printf 'INITRAMFS_MODULE_COUNT=%s\n' "$initramfs_module_count"
  printf 'COMPLETE_ROOTFS_MODULE_TREE_REQUIRED=true\n'
  printf 'DMA_CMA_ENABLED=true\n'
  printf 'GLOBAL_CMA_MBYTES=32\n'
  printf 'QSEECOM_POOLS_REUSABLE=true\n'
  printf 'QSEECOM_POOLS_NO_MAP=false\n'
  printf 'TRUSTLET_INCLUDED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$matched_dtb" "$matched_ramdisk"

printf 'FP6 fingerprint matched-CMA candidate: %s\n' "$candidate"
