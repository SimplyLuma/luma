#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build only the S3NRN4V NFC modules and a minimal overlay-merged DTB on an
# isolated Linux/aarch64 builder.  The accepted kernel is never relinked and
# this script cannot contact a phone, boot an image, or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-nfc.env"

input_dir=${1:?usage: build-fp6-nfc-candidate.sh INPUT_DIR OUTPUT_DIR}
output_dir=${2:?usage: build-fp6-nfc-candidate.sh INPUT_DIR OUTPUT_DIR}
archive=$input_dir/linux-v7.1.2-milos.tar.gz
symvers=$input_dir/Module.symvers
base_config=$input_dir/config
base_dtb=$input_dir/milos-fairphone-fp6.dtb
overlay=$repo_root/config/mobile/fp6-nfc/milos-fairphone-fp6-nfc.dtso
patch_dir=$repo_root/patches/linux-milos-nfc
build_timestamp='2026-08-20 00:00:00 UTC'

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

for tool in cut dtc fdtdump fdtget fdtoverlay find git grep install make \
  modinfo mktemp sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || die "missing build tool: $tool"
done
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'run only on an isolated Linux/aarch64 builder'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
for artifact in "$archive" "$symvers" "$base_config" "$base_dtb" "$overlay"; do
  [ -f "$artifact" ] || die "required input is missing: $artifact"
done
[ "$(hash "$archive")" = "$FP6_NFC_KERNEL_ARCHIVE_SHA256" ] || die 'kernel archive differs'
[ "$(hash "$symvers")" = "$FP6_NFC_MODULE_SYMVERS_SHA256" ] || die 'Module.symvers differs'
[ "$(hash "$base_config")" = "$FP6_NFC_BASE_CONFIG_SHA256" ] || die 'base config differs'
[ "$(hash "$base_dtb")" = "$FP6_NFC_BASE_DTB_SHA256" ] || die 'accepted sensor DTB differs'
[ "$(hash "$overlay")" = "$FP6_NFC_OVERLAY_SHA256" ] || die 'NFC overlay differs'

IFS=, read -r -a expected_patch_hashes <<<"$FP6_NFC_PATCH_SHA256"
mapfile -t patches < <(find "$patch_dir" -maxdepth 1 -type f -name '*.patch' | sort)
[ "${#patches[@]}" -eq 7 ] && [ "${#expected_patch_hashes[@]}" -eq 7 ] ||
  die 'expected exactly seven NFC patches and hashes'
for index in "${!patches[@]}"; do
  [ "$(hash "${patches[$index]}")" = "${expected_patch_hashes[$index]}" ] ||
    die "NFC patch differs: ${patches[$index]}"
done

parent=$(dirname -- "$output_dir")
mkdir -p "$parent"
work_dir=$(mktemp -d "$parent/nfc-build.XXXXXX")
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT HUP INT TERM
tar -xzf "$archive" -C "$work_dir"
source_tree=$work_dir/linux
[ -d "$source_tree" ] || die 'kernel archive layout differs'
install -m 0644 "$symvers" "$source_tree/Module.symvers"
install -m 0644 "$base_config" "$source_tree/.config"

for patch in "${patches[@]}"; do
  git -C "$source_tree" apply --check "$patch"
  git -C "$source_tree" apply "$patch"
done
grep -Fq 'firmware_request_nowarn' "$source_tree/drivers/nfc/s3fwrn5/nci.c" ||
  die 'missing-calibration fallback implementation is absent'

make_args=(ARCH=arm64 LLVM=1 LOCALVERSION= KBUILD_BUILD_USER=luma
  KBUILD_BUILD_HOST=isolated KBUILD_BUILD_TIMESTAMP="$build_timestamp"
  KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos
  "KCFLAGS=-ffile-prefix-map=$source_tree=. -fdebug-prefix-map=$source_tree=. -fmacro-prefix-map=$source_tree=.")
"$source_tree/scripts/config" --file "$source_tree/.config" --module NFC
"$source_tree/scripts/config" --file "$source_tree/.config" --module NFC_NCI
"$source_tree/scripts/config" --file "$source_tree/.config" --module NFC_S3FWRN5
"$source_tree/scripts/config" --file "$source_tree/.config" --module NFC_S3FWRN5_I2C
make -C "$source_tree" "${make_args[@]}" olddefconfig
grep -qx 'CONFIG_NFC=m' "$source_tree/.config"
grep -qx 'CONFIG_NFC_NCI=m' "$source_tree/.config"
grep -qx 'CONFIG_NFC_S3FWRN5=m' "$source_tree/.config"
grep -qx 'CONFIG_NFC_S3FWRN5_I2C=m' "$source_tree/.config"
[ "$(make -s -C "$source_tree" "${make_args[@]}" kernelrelease)" = 7.1.2 ] ||
  die 'kernel release differs'
make -C "$source_tree" "${make_args[@]}" prepare modules_prepare
# modules_prepare intentionally does not generate a complete Module.symvers;
# restore the exact symbols from the proven 7.1.2 kernel build afterward.
install -m 0644 "$symvers" "$source_tree/Module.symvers"
make -j4 -C "$source_tree" "${make_args[@]}" M=net/nfc modules
make -j4 -C "$source_tree" "${make_args[@]}" M=drivers/nfc/s3fwrn5 \
  KBUILD_EXTRA_SYMBOLS="$source_tree/net/nfc/Module.symvers" modules

modules=(
  "$source_tree/net/nfc/nfc.ko"
  "$source_tree/net/nfc/nci/nci.ko"
  "$source_tree/drivers/nfc/s3fwrn5/s3fwrn5.ko"
  "$source_tree/drivers/nfc/s3fwrn5/s3fwrn5_i2c.ko"
)
for module in "${modules[@]}"; do
  [ -f "$module" ] || die "NFC module is missing: $module"
  [ "$(modinfo -F vermagic "$module")" = '7.1.2 SMP preempt mod_unload aarch64' ] ||
    die "module vermagic differs: $module"
done
mapfile -t firmware < <(modinfo -F firmware "$source_tree/drivers/nfc/s3fwrn5/s3fwrn5.ko" | sort -u)
printf '%s\n' "${firmware[@]}" | grep -Fxq "$FP6_NFC_HWREG_PATH" || die 'hwreg declaration is absent'
printf '%s\n' "${firmware[@]}" | grep -Fxq "$FP6_NFC_SWREG_PATH" || die 'swreg declaration is absent'

dtbo=$work_dir/milos-fairphone-fp6-nfc.dtbo
merged_dtb=$work_dir/milos-fairphone-fp6-nfc.dtb
dtc -@ -Wno-avoid_default_addr_size -Wno-reg_format -Wno-clocks_property \
  -Wno-interrupts_extended_property -Wno-gpios_property \
  -I dts -O dtb -o "$dtbo" "$overlay"
fdtoverlay -i "$base_dtb" -o "$merged_dtb" "$dtbo"

nfc_path=/soc@0/geniqup@ac0000/i2c@a84000/nfc@27
tlmm_path=/soc@0/pinctrl@f100000/nfc-default-state
regulator_path=/soc@0/rsc@17a00000/regulators-0/ldo20
[ "$(fdtget -t s "$merged_dtb" "$nfc_path" compatible)" = samsung,s3nrn4v ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" reg)" = 27 ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" clocks)" = '28 2' ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" interrupts-extended)" = '35 1f 1' ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" en-gpios)" = '35 38 0' ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" wake-gpios)" = '35 7 0' ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" pvdd-supply)" = \
  "$(fdtget -t x "$merged_dtb" "$regulator_path" phandle)" ]
[ "$(fdtget -t x "$merged_dtb" "$nfc_path" pinctrl-0)" = \
  "$(fdtget -t x "$merged_dtb" "$tlmm_path" phandle)" ]
[ "$(fdtget -t s "$merged_dtb" "$tlmm_path/irq-pins" pins)" = gpio31 ]
[ "$(fdtget -t s "$merged_dtb" "$tlmm_path/pd-pins" pins)" = gpio56 ]

mkdir -p "$output_dir/modules"
install -m 0644 "$source_tree/.config" "$output_dir/config"
install -m 0644 "$dtbo" "$output_dir/milos-fairphone-fp6-nfc.dtbo"
install -m 0644 "$merged_dtb" "$output_dir/milos-fairphone-fp6-nfc.dtb"
for module in "${modules[@]}"; do
  install -m 0644 "$module" "$output_dir/modules/$(basename "$module")"
done
if find "$output_dir" -type f \( -name hwreg.bin -o -name swreg.bin \) -print | grep -q .; then
  die 'calibration blobs must not be included in the reader candidate'
fi

{
  printf 'LUMA_FP6_NFC_BUILD_VERSION=1\n'
  printf 'SCOPE=fp6-nfc-reader-diagnostic\n'
  printf 'UPSTREAM_COMMIT=%s\n' "$FP6_NFC_UPSTREAM_COMBINED_COMMIT"
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'KERNEL_ARCHIVE_SHA256=%s\n' "$FP6_NFC_KERNEL_ARCHIVE_SHA256"
  printf 'MODULE_SYMVERS_SHA256=%s\n' "$FP6_NFC_MODULE_SYMVERS_SHA256"
  printf 'BASE_CONFIG_SHA256=%s\n' "$FP6_NFC_BASE_CONFIG_SHA256"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$output_dir/config")"
  printf 'BASE_DTB_SHA256=%s\n' "$FP6_NFC_BASE_DTB_SHA256"
  printf 'OVERLAY_SHA256=%s\n' "$FP6_NFC_OVERLAY_SHA256"
  printf 'DTBO_SHA256=%s\n' "$(hash "$output_dir/milos-fairphone-fp6-nfc.dtbo")"
  printf 'DTB_SHA256=%s\n' "$(hash "$output_dir/milos-fairphone-fp6-nfc.dtb")"
  for module in "${modules[@]}"; do
    name=$(basename "$module" | tr '[:lower:].-' '[:upper:]__')
    printf '%s_SHA256=%s\n' "$name" "$(hash "$output_dir/modules/$(basename "$module")")"
  done
  printf 'KERNEL_RELINKED=false\n'
  printf 'BASE_DTB_PRESERVED=true\n'
  printf 'NFC_OVERLAY_ONLY=true\n'
  printf 'CALIBRATION_BLOBS_INCLUDED=false\n'
  printf 'MISSING_CALIBRATION_NONFATAL=true\n'
  printf 'READER_SCOPE_ONLY=true\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 NFC reader candidate bundle: %s\n' "$output_dir"
