#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build a Tokay U-Boot derivative which can consume only one exact in-memory
# FIT supplied by the previous bootloader. Persistent storage, USB update,
# interactive input, and EFI paths are compiled out. This remains an offline
# candidate proof and does not create an Android boot image or contact a phone.

set -euo pipefail
umask 022
export GIT_PAGER=cat

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-uboot-ramboot.sh SOURCE_ROOT FIT OUTPUT_DIR}
fit=${2:?usage: build-pixel9-uboot-ramboot.sh SOURCE_ROOT FIT OUTPUT_DIR}
output_dir=${3:?usage: build-pixel9-uboot-ramboot.sh SOURCE_ROOT FIT OUTPUT_DIR}
source_dir=$output_dir/source
build_dir=$output_dir/build
artifact_dir=$output_dir/artifacts
jobs=${LUMA_PIXEL9_UBOOT_BUILD_JOBS:-4}
patch_path=$repo_root/$PIXEL9_UBOOT_RAMBOOT_PATCH
load_window_patch_path=$repo_root/$PIXEL9_UBOOT_LOAD_WINDOW_PATCH
handoff_patch_path=$repo_root/$PIXEL9_UBOOT_HANDOFF_OBSERVABILITY_PATCH
embedded_fit_patch_path=$repo_root/$PIXEL9_UBOOT_EMBEDDED_FIT_PATCH

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

case "$jobs" in
  ''|*[!0-9]*) die 'LUMA_PIXEL9_UBOOT_BUILD_JOBS must be a positive integer' ;;
esac
[ "$jobs" -ge 1 ] || die 'LUMA_PIXEL9_UBOOT_BUILD_JOBS must be at least 1'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'the Pixel 9 ramboot loader must be built off-device on Linux/aarch64'

if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to compile on a Pixel target' ;;
  esac
fi

for tool in awk gcc git install make nm python3 sha256sum strings; do
  command -v "$tool" >/dev/null 2>&1 || die "missing ramboot build tool: $tool"
done
case "$(gcc -dumpmachine)" in
  aarch64*) ;;
  *) die 'native GCC target is not AArch64' ;;
esac

uboot=$source_root/u-boot-zumapro
source_manifest=$source_root/luma-source-manifest.env
[ -d "$uboot/.git" ] || die "missing full U-Boot checkout: $uboot"
[ -f "$source_manifest" ] || die "missing full-source manifest: $source_manifest"
[ -f "$fit" ] || die "missing diagnostic FIT: $fit"
[ -f "$patch_path" ] || die "missing Luma ramboot patch: $patch_path"
[ -f "$load_window_patch_path" ] || \
  die "missing Luma load-window patch: $load_window_patch_path"
[ -f "$handoff_patch_path" ] || \
  die "missing Luma handoff-observability patch: $handoff_patch_path"
[ -f "$embedded_fit_patch_path" ] || \
  die "missing Luma embedded-FIT patch: $embedded_fit_patch_path"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(git -C "$uboot" rev-parse HEAD)" = "$PIXEL9_UBOOT_COMMIT" ] || \
  die 'U-Boot checkout differs from the pinned commit'
[ -z "$(git -C "$uboot" status --porcelain --untracked-files=no)" ] || \
  die 'U-Boot checkout contains tracked modifications'
[ "$(sha256sum "$uboot/configs/$PIXEL9_UBOOT_DEFCONFIG" | awk '{print $1}')" = \
  "$PIXEL9_UBOOT_DEFCONFIG_SHA256" ] || die 'Tokay U-Boot defconfig checksum differs'
[ "$(sha256sum "$patch_path" | awk '{print $1}')" = \
  "$PIXEL9_UBOOT_RAMBOOT_PATCH_SHA256" ] || die 'Luma ramboot patch checksum differs'
[ "$(sha256sum "$load_window_patch_path" | awk '{print $1}')" = \
  "$PIXEL9_UBOOT_LOAD_WINDOW_PATCH_SHA256" ] || \
  die 'Luma load-window patch checksum differs'
[ "$(sha256sum "$handoff_patch_path" | awk '{print $1}')" = \
  "$PIXEL9_UBOOT_HANDOFF_OBSERVABILITY_PATCH_SHA256" ] || \
  die 'Luma handoff-observability patch checksum differs'
[ "$(sha256sum "$embedded_fit_patch_path" | awk '{print $1}')" = \
  "$PIXEL9_UBOOT_EMBEDDED_FIT_PATCH_SHA256" ] || \
  die 'Luma embedded-FIT patch checksum differs'
grep -Fqx "UBOOT_COMMIT=$PIXEL9_UBOOT_COMMIT" "$source_manifest" || \
  die 'full-source manifest does not match the U-Boot pin'
grep -Fqx 'PHONE_ACCESSED=false' "$source_manifest" || \
  die 'full-source manifest lacks the no-phone boundary'

fit_size=$(python3 - "$fit" <<'PY'
import pathlib
import struct
import sys

path = pathlib.Path(sys.argv[1])
with path.open('rb') as stream:
    header = stream.read(8)
if len(header) != 8:
    raise SystemExit('FIT is shorter than its FDT header')
magic, total = struct.unpack('>II', header)
if magic != 0xd00dfeed:
    raise SystemExit('FIT does not begin with an FDT magic value')
actual = path.stat().st_size
if total != actual:
    raise SystemExit(f'FIT totalsize {total} differs from file size {actual}')
print(actual)
PY
)
fit_sha256=$(sha256sum "$fit" | awk '{print $1}')
case "$fit_sha256" in
  *[!0-9a-f]*|'') die 'FIT SHA-256 is malformed' ;;
esac
[ "${#fit_sha256}" -eq 64 ] || die 'FIT SHA-256 is not full length'

mkdir -p "$output_dir"
git clone -q --no-hardlinks --no-checkout "$uboot" "$source_dir"
git -C "$source_dir" checkout -q --detach "$PIXEL9_UBOOT_COMMIT"
git -C "$source_dir" apply --recount --check "$patch_path"
git -C "$source_dir" apply --recount "$patch_path"
git -C "$source_dir" apply --recount --check "$load_window_patch_path"
git -C "$source_dir" apply --recount "$load_window_patch_path"
git -C "$source_dir" apply --recount --check "$handoff_patch_path"
git -C "$source_dir" apply --recount "$handoff_patch_path"
git -C "$source_dir" apply --recount --check "$embedded_fit_patch_path"
git -C "$source_dir" apply --recount "$embedded_fit_patch_path"
install -m 0644 "$fit" \
  "$source_dir/board/samsung/exynos-mobile/luma-payload.fit"
git -C "$source_dir" diff --check

mkdir -p "$build_dir/board/samsung/exynos-mobile" "$artifact_dir"
install -m 0644 "$fit" \
  "$build_dir/board/samsung/exynos-mobile/luma-payload.fit"
source_date_epoch=$(git -C "$uboot" show -s --format=%ct "$PIXEL9_UBOOT_COMMIT")
export SOURCE_DATE_EPOCH="$source_date_epoch"
export KBUILD_BUILD_TIMESTAMP="@$source_date_epoch"
export KBUILD_BUILD_USER=luma
export KBUILD_BUILD_HOST=pixel9-builder
export KBUILD_BUILD_VERSION=1

make -C "$source_dir" O="$build_dir" ARCH=arm CROSS_COMPILE= \
  "$PIXEL9_UBOOT_DEFCONFIG"
config="$source_dir/scripts/config --file $build_dir/.config"
$config --enable LUMA_RAMBOOT_DIAGNOSTIC
$config --set-str LUMA_EXPECTED_FIT_SHA256 "$fit_sha256"
$config --set-val LUMA_EXPECTED_FIT_SIZE "$fit_size"
$config --enable CMD_HASH
$config --enable SHA256
$config --enable CMD_ECHO
$config --enable CMD_SLEEP

for symbol in \
  MMC DM_MMC MMC_WRITE CMD_MMC MTD DM_MTD MTD_PARTITIONS SPI_FLASH \
  DM_SPI_FLASH CMD_SF CMD_MTD CMD_PART CMD_SAVEENV ENV_IS_IN_MMC \
  ENV_IS_IN_FAT ENV_IS_IN_EXT4 EFI_LOADER CMD_BOOTEFI \
  CMD_BOOTEFI_BOOTMGR CMD_BOOTEFI_SELFTEST CMD_EFIDEBUG CMD_EFICONFIG \
  BOOTSTD BOOTFLOW_FULL BOOTMETH_EFILOADER CMD_BOOTFLOW CMD_BOOTMETH \
  FS_EXT4 EXT4_WRITE FS_FAT FAT_WRITE CMD_FS_GENERIC CMD_EXT4 CMD_FAT \
  CMD_LOAD NET CMD_NET CMD_TFTPBOOT CMD_DHCP CMD_PXE CMD_NFS BUTTON \
  BUTTON_CMD BUTTON_KEYBOARD CMD_BUTTON CMD_BOOTMENU CMD_TERMINAL \
  EFI_RUNTIME_UPDATE_CAPSULE EFI_CAPSULE_ON_DISK \
  EFI_CAPSULE_ON_DISK_EARLY EFI_CAPSULE_FIRMWARE_RAW UFS SCSI CMD_UFS \
  CMD_SCSI USB USB_GADGET USB_FUNCTION_FASTBOOT FASTBOOT FASTBOOT_FLASH \
  FASTBOOT_FLASH_BLOCK DFU DFU_MMC AUTO_COMPLETE CMDLINE_EDITING SYS_LONGHELP \
  CMD_2048 CMD_ARMFFA CMD_BDI CMD_BIND CMD_BMP CMD_BOOTD CMD_CACHE CMD_CLS \
  CMD_CONFIG CMD_CONITRACE CMD_CONSOLE CMD_CRC32 CMD_CYCLIC CMD_DATE CMD_DM \
  CMD_EDITENV CMD_ELF CMD_ELF_BOOTVX CMD_ENV_EXISTS CMD_EXCEPTION \
  CMD_EXPORTENV CMD_FDT CMD_GETTIME CMD_GO CMD_HELP CMD_HISTORY CMD_IMI \
  CMD_IMPORTENV CMD_INI CMD_ITEST CMD_KASLRSEED CMD_LICENSE CMD_LOADB \
  CMD_LOADS CMD_LZMADEC CMD_MEMORY CMD_PAUSE CMD_PINMUX CMD_POWEROFF \
  CMD_PSTORE CMD_RANDOM CMD_RNG CMD_RUN CMD_SELECT_FONT CMD_SETEXPR \
  CMD_SM3SUM CMD_SOURCE CMD_SYSBOOT CMD_TIMER CMD_TIME CMD_UFETCH CMD_UNLZ4 \
  CMD_UNZIP CMD_UUID CMD_VIDCONSOLE CMD_XIMG; do
  $config --disable "$symbol"
done

make -C "$source_dir" O="$build_dir" ARCH=arm CROSS_COMPILE= olddefconfig

for required in \
  CONFIG_LUMA_RAMBOOT_DIAGNOSTIC=y CONFIG_LUMA_EXPECTED_FIT_SIZE=$fit_size \
  CONFIG_CMD_HASH=y CONFIG_SHA256=y CONFIG_CMD_ECHO=y CONFIG_CMD_SLEEP=y \
  CONFIG_FIT=y CONFIG_SUPPORT_RAW_INITRD=y CONFIG_ENV_IS_NOWHERE=y \
  CONFIG_SYS_DEVICE_NULLDEV=y; do
  grep -Fqx "$required" "$build_dir/.config" || \
    die "required ramboot configuration is absent: $required"
done

for forbidden in \
  CONFIG_MMC=y CONFIG_MTD=y CONFIG_SPI_FLASH=y CONFIG_CMD_SF=y \
  CONFIG_CMD_SAVEENV=y CONFIG_EFI_LOADER=y CONFIG_CMD_BOOTEFI=y \
  CONFIG_BOOTSTD=y CONFIG_FS_EXT4=y CONFIG_FS_FAT=y \
  CONFIG_CMD_FS_GENERIC=y CONFIG_NET=y CONFIG_CMD_NET=y CONFIG_BUTTON=y \
  CONFIG_BUTTON_KEYBOARD=y CONFIG_CMD_BOOTMENU=y CONFIG_UFS=y \
  CONFIG_SCSI=y CONFIG_USB=y CONFIG_USB_GADGET=y \
  CONFIG_USB_FUNCTION_FASTBOOT=y CONFIG_FASTBOOT=y CONFIG_DFU=y; do
  if grep -Fqx "$forbidden" "$build_dir/.config"; then
    die "forbidden ramboot configuration survived: $forbidden"
  fi
done

for forbidden_symbol in \
  CONFIG_AUTO_COMPLETE CONFIG_CMDLINE_EDITING CONFIG_SYS_LONGHELP \
  CONFIG_CMD_ELF CONFIG_CMD_EXPORTENV CONFIG_CMD_FDT CONFIG_CMD_GO \
  CONFIG_CMD_IMPORTENV CONFIG_CMD_LOADB CONFIG_CMD_LOADS CONFIG_CMD_MEMORY \
  CONFIG_CMD_NVEDIT CONFIG_CMD_PSTORE CONFIG_CMD_SOURCE CONFIG_CMD_SYSBOOT; do
  if grep -q "^${forbidden_symbol}=" "$build_dir/.config"; then
    die "arbitrary input/memory command survived: $forbidden_symbol"
  fi
done

make -C "$source_dir" O="$build_dir" ARCH=arm CROSS_COMPILE= -j"$jobs"

for artifact in u-boot.bin u-boot.dtb .config include/generated/environment.h; do
  [ -s "$build_dir/$artifact" ] || die "ramboot artifact is missing: $artifact"
done

environment=$build_dir/include/generated/environment.h
grep -Fq 'bootdelay=-2' "$environment" || die 'uninterruptible boot delay is absent'
grep -Fq 'stdin=nulldev' "$environment" || die 'null input device is absent'
grep -Fq 'hash sha256 ${luma_fit_addr} ${luma_fit_size}' "$environment" || \
  die 'FIT digest check is absent from boot command'
grep -Fq 'bootm ${luma_fit_addr}' "$environment" || \
  die 'bounded in-memory FIT handoff is absent'
grep -Fq 'LUMA_STAGE=FIT_BOUNDS_OK' "$environment" || \
  die 'FIT-bounds observation marker is absent'
grep -Fq 'LUMA_STAGE=FIT_EMBEDDED_OK' "$environment" || \
  die 'embedded-FIT observation marker is absent'
grep -Fq 'LUMA_STAGE=FIT_DIGEST_OK' "$environment" || \
  die 'FIT-digest observation marker is absent'
grep -Fq 'LUMA_STAGE=FAILURE_HOLD' "$environment" || \
  die 'failure observation marker is absent'
grep -Fq 'sleep 30' "$environment" || die 'bounded failure hold is absent'
if grep -Eq 'fastboot=|bootmenu_|bootefi bootmgr|stdin=button' "$environment"; then
  die 'interactive or update environment survived ramboot configuration'
fi

if strings "$build_dir/u-boot.bin" | \
  grep -Eiq 'fastboot flash|mmc write|sf write|scsi write|gpt write|saveenv|bootefi bootmgr'; then
  die 'persistent update command survived in the ramboot binary'
fi

for symbol in luma_fit_start luma_fit_end; do
  nm "$build_dir/u-boot" | awk -v symbol="$symbol" '$3 == symbol { found = 1 } END { exit !found }' || \
    die "embedded FIT linker symbol is absent: $symbol"
done
python3 - "$build_dir/u-boot.bin" "$fit" <<'PY'
import pathlib
import sys

binary = pathlib.Path(sys.argv[1]).read_bytes()
fit = pathlib.Path(sys.argv[2]).read_bytes()
count = binary.count(fit)
if count != 1:
    raise SystemExit(f'expected exactly one embedded FIT, found {count}')
PY

install -m 0644 "$build_dir/u-boot.bin" "$artifact_dir/u-boot.bin"
install -m 0644 "$build_dir/u-boot.dtb" "$artifact_dir/u-boot.dtb"
install -m 0644 "$build_dir/.config" "$artifact_dir/config"
install -m 0644 "$environment" "$artifact_dir/environment.h"
install -m 0644 "$fit" "$artifact_dir/payload.fit"

uboot_version=$(sed -n 's/^#define PLAIN_VERSION "\(.*\)"/\1/p' \
  "$build_dir/include/generated/version_autogenerated.h")
compiler_line=$(gcc --version | sed -n '1p' | tr '\n' ' ')
{
  printf 'LUMA_PIXEL9_UBOOT_RAMBOOT_VERSION=3\n'
  printf 'SCOPE=offline-read-only-ramboot-candidate\n'
  printf 'UBOOT_COMMIT=%s\n' "$PIXEL9_UBOOT_COMMIT"
  printf 'RAMBOOT_PATCH_SHA256=%s\n' "$PIXEL9_UBOOT_RAMBOOT_PATCH_SHA256"
  printf 'LOAD_WINDOW_PATCH_SHA256=%s\n' "$PIXEL9_UBOOT_LOAD_WINDOW_PATCH_SHA256"
  printf 'HANDOFF_OBSERVABILITY_PATCH_SHA256=%s\n' \
    "$PIXEL9_UBOOT_HANDOFF_OBSERVABILITY_PATCH_SHA256"
  printf 'EMBEDDED_FIT_PATCH_SHA256=%s\n' \
    "$PIXEL9_UBOOT_EMBEDDED_FIT_PATCH_SHA256"
  printf 'SOURCE_DATE_EPOCH=%s\n' "$source_date_epoch"
  printf 'UBOOT_VERSION=%s\n' "$uboot_version"
  printf 'COMPILER=%s\n' "$compiler_line"
  printf 'FIT_BYTES=%s\n' "$fit_size"
  printf 'FIT_SHA256=%s\n' "$fit_sha256"
  printf 'UBOOT_BIN_SHA256=%s\n' "$(sha256sum "$artifact_dir/u-boot.bin" | awk '{print $1}')"
  printf 'UBOOT_DTB_SHA256=%s\n' "$(sha256sum "$artifact_dir/u-boot.dtb" | awk '{print $1}')"
  printf 'CONFIG_SHA256=%s\n' "$(sha256sum "$artifact_dir/config" | awk '{print $1}')"
  printf 'BUILD_HOST=Linux/aarch64\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PERSISTENT_STORAGE_INITIALIZED=false\n'
  printf 'USB_UPDATE_INTERFACE_COMPILED=false\n'
  printf 'INTERACTIVE_INPUT_COMPILED=false\n'
  printf 'ARBITRARY_MEMORY_COMMANDS_COMPILED=false\n'
  printf 'FIT_BOUNDS_REQUIRED=true\n'
  printf 'FIT_SIZE_COMPILED=true\n'
  printf 'PRIOR_STAGE_INITRD_END_REQUIRED=false\n'
  printf 'FIT_DIGEST_REQUIRED=true\n'
  printf 'FIT_SOURCE=embedded-rodata\n'
  printf 'FIT_EMBEDDED_ONCE=true\n'
  printf 'ANDROID_BOOT_RAMDISK_REQUIRED=false\n'
  printf 'FAILURE_HOLD_SECONDS=30\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 offline read-only ramboot loader: %s\n' "$output_dir"
