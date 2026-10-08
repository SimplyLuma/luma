#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline FP6 QSEECom-owned-heaps boot candidate from the physically
# accepted SHM-Bridge boot. The exact ramdisk and boot-header fields remain
# unchanged. This script never contacts a phone or writes a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-dedicated-heaps-candidate.sh SHMBRIDGE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
bundle=${2:?usage: prepare-fp6-fingerprint-dedicated-heaps-candidate.sh SHMBRIDGE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
output_dir=${3:?usage: prepare-fp6-fingerprint-dedicated-heaps-candidate.sh SHMBRIDGE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-dedicated-heaps.dtso
candidate=$output_dir/boot-fp6-luma-fingerprint-dedicated-heaps-v10.img
manifest=$bundle/manifest.env

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtoverlay fdtget fdtput grep install mkbootimg mktemp python3 \
  sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$base_boot" "$bundle/Image.gz" "$bundle/config" \
  "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko" "$manifest" "$overlay"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" ] ||
  die 'accepted SHM-Bridge base checksum differs'
[ "$(hash "$overlay")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_OVERLAY_SHA256" ] ||
  die 'dedicated-heaps overlay checksum differs'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'kernel bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "$KERNEL_RELEASE" = 7.1.2 ] || die 'kernel release differs'
[ "$TZMEM_MODE" = shmbridge ] || die 'kernel is not in SHM-Bridge mode'
[ "$STOCK_HEAPS_ENABLED" = true ] || die 'stock-heaps base patch is absent'
[ "$DEDICATED_HEAPS_ENABLED" = true ] || die 'dedicated-heaps gate is closed'
[ "$DEDICATED_HEAPS_PATCHSET_SHA256" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_PATCHSET_SHA256" ] ||
  die 'dedicated-heaps patchset differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_KERNEL_SHA256" ] ||
  die 'dedicated-heaps kernel checksum differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_CONFIG_SHA256" ] ||
  die 'kernel configuration differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOMTEE_KO_SHA256" ] ||
  die 'dedicated-heaps QSEECOM module differs'
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOMTEE_KO_SHA256" != \
  "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'dedicated-heaps QSEECOM module unexpectedly matches the old module'

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
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/base" "$work/base-verify" "$work/roundtrip" "$work/candidate"
args_file=$work/mkbootimg.args0
roundtrip=$work/base-roundtrip.img
overlay_blob=$work/dedicated-heaps.dtbo
detached_dtb=$work/base-without-global-scm-pool.dtb
enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-dedicated-heaps-v10.dtb

unpack_bootimg --boot_img "$base_boot" --out "$work/base-verify" \
  >"$work/base-info.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" \
  --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
unpack_bootimg --boot_img "$roundtrip" --out "$work/roundtrip" \
  >"$work/roundtrip-info.txt"
cmp "$work/base-info.txt" "$work/roundtrip-info.txt"
cmp "$work/base-verify/kernel" "$work/roundtrip/kernel"
cmp "$work/base-verify/ramdisk" "$work/roundtrip/ramdisk"
cmp "$work/base-verify/dtb" "$work/roundtrip/dtb"
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256" ] ||
  die 'base SHM-Bridge kernel differs'
[ "$(hash "$work/base/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'base ramdisk differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ] ||
  die 'base low-32-bit DTB differs'

install -m 0644 "$work/base/dtb" "$detached_dtb"
fdtput -d "$detached_dtb" /firmware/scm memory-region
! fdtget "$detached_dtb" /firmware/scm memory-region >/dev/null 2>&1 ||
  die 'failed to detach the global SCM reserved pool'
dtc -@ -I dts -O dtb -o "$overlay_blob" "$overlay"
fdtoverlay -i "$detached_dtb" -o "$enabled_dtb" "$overlay_blob"

[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'TA pool size differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool size)" = \
  '0 1400000' ] || die 'apps pool size differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool alignment)" = \
  '0 400000' ] || die 'apps pool alignment differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'apps pool range differs'
fdtget "$enabled_dtb" /reserved-memory/qseecom-apps-pool no-map >/dev/null ||
  die 'apps pool is not no-map'
! fdtget "$enabled_dtb" /reserved-memory/qseecom-apps-pool reusable >/dev/null 2>&1 ||
  die 'apps pool unexpectedly reusable'
ta_phandle=$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool phandle)
apps_phandle=$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool phandle)
child=/firmware/scm/qseecom-tee-heaps
! fdtget "$enabled_dtb" /firmware/scm memory-region >/dev/null 2>&1 ||
  die 'global SCM still owns a reserved pool'
! fdtget "$enabled_dtb" /firmware/scm qcom,qseecom-apps-region >/dev/null 2>&1 ||
  die 'global SCM unexpectedly owns the apps pool'
! fdtget "$enabled_dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null 2>&1 ||
  die 'global whole-pool SHM mode is unexpectedly enabled'
[ "$(fdtget "$enabled_dtb" "$child" compatible)" = 'luma,qseecom-tee-heaps' ] ||
  die 'QSEECom heap child compatible differs'
fdtget "$enabled_dtb" "$child" luma,dedicated-heaps >/dev/null ||
  die 'dedicated-heaps opt-in is absent'
[ "$(fdtget -t x "$enabled_dtb" "$child" memory-region)" = \
  "$ta_phandle $apps_phandle" ] || die 'QSEECom child pool ownership differs'
[ "$(fdtget "$enabled_dtb" "$child" memory-region-names)" = 'ta apps' ] ||
  die 'QSEECom child pool names differ'

install -m 0644 "$bundle/Image.gz" "$work/base/kernel"
install -m 0644 "$enabled_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" \
  >"$output_dir/boot-info.txt"
cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
cmp "$enabled_dtb" "$work/candidate/dtb"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

candidate_sha=$(hash "$candidate")
{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=10\n'
  printf 'SCOPE=fp6-fingerprint-dedicated-qseecom-heaps-boot-only\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$FP6_FINGERPRINT_DEDICATED_HEAPS_KERNEL_SHA256"
  printf 'CONFIG_SHA256=%s\n' "$FP6_FINGERPRINT_DEDICATED_HEAPS_CONFIG_SHA256"
  printf 'BASE_DTB_SHA256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256"
  printf 'DTB_SHA256=%s\n' "$(hash "$enabled_dtb")"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256"
  printf 'OVERLAY_SHA256=%s\n' "$FP6_FINGERPRINT_DEDICATED_HEAPS_OVERLAY_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'KERNEL_CHANGED=true\n'
  printf 'DTB_CHANGED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'FOCALTECH_MODULE_UNCHANGED=true\n'
  printf 'QSEECOM_MODULE_CHANGED=true\n'
  printf 'QSEECOM_MODULE_INSTALL_REQUIRED=true\n'
  printf 'GLOBAL_SCM_RESERVED_POOL=false\n'
  printf 'GLOBAL_SCM_MEMORY_REGION_DELETED_BY_COMPOSER=true\n'
  printf 'GLOBAL_SCM_WHOLE_POOL_SHMBRIDGE=false\n'
  printf 'QSEECOM_OWNS_TA_POOL=true\n'
  printf 'QSEECOM_OWNS_APPS_POOL=true\n'
  printf 'TA_POOL_SIZE_MBYTES=16\n'
  printf 'APPS_POOL_SIZE_MBYTES=20\n'
  printf 'POOLS_BELOW_4G=true\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$enabled_dtb"

printf 'FP6 fingerprint dedicated-heaps boot candidate: %s\n' "$candidate"
