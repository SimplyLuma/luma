#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Incrementally build only the WCD9378 SoundWire module and FP6 DTB needed to
# test the stock-aligned AMIC3 master-port mapping. Run solely in the existing
# isolated Linux/aarch64 builder; this script cannot access the phone.

set -euo pipefail
umask 022

build_root=${1:?usage: build-fp6-amic3-routing-candidate.sh BUILD_ROOT PATCH OUTPUT_DIR}
mapping_patch=${2:?usage: build-fp6-amic3-routing-candidate.sh BUILD_ROOT PATCH OUTPUT_DIR}
output_dir=${3:?usage: build-fp6-amic3-routing-candidate.sh BUILD_ROOT PATCH OUTPUT_DIR}
rootfs=$build_root/lab/root
source_tree=$rootfs/work/linux
config_sha=a78af97c72e7d454348aa87dbe4bdabc81ab8328e9ae807610842059ef061d09
image_sha=760eb683f88ced7f869bc169ba123c52f0df6163c6cc2812b6676d812a3efb68
dtb_sha=42427369df56f961ca1aa60f02edb6b8f63cb0a60b7aed4e4f820cb5fda6951b
sdw_source_sha=3ca1f5263713481f6233378df1b2dc1adefb2cc0a33b7024eedf4b3673a9e14f
dts_source_sha=c05ba319553b004fa1be7a0b7ad7e048ebbd47de6ec3ee62370c59ed009a10e2
patch_sha=6cee0f009ff183e6b406e3d499de66b1c0d0bd570ebf6a0eb3ee53b37c556424
build_timestamp='2026-08-19 00:00:00 UTC'

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the isolated builder'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] ||
  die 'run only on the Linux/aarch64 builder'
[ -x /mnt/lima-rosetta/rosetta ] || die 'Lima Rosetta is unavailable'
if [ -r /sys/firmware/devicetree/base/model ]; then
  model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$model" in *Fairphone*) die 'refuse to compile on the FP6 target' ;; esac
fi
[ -d "$source_tree" ] || die 'verified v3 source tree is missing'
[ -f "$mapping_patch" ] || die 'AMIC3 mapping patch is missing'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$mapping_patch")" = "$patch_sha" ] || die 'mapping patch differs'
[ "$(hash "$source_tree/.config")" = "$config_sha" ] || die 'v3 config differs'
[ "$(hash "$source_tree/arch/arm64/boot/vmlinuz")" = "$image_sha" ] ||
  die 'v3 kernel differs'
[ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb")" = "$dtb_sha" ] ||
  die 'input DTB differs'
[ "$(hash "$source_tree/sound/soc/codecs/wcd9378-sdw.c")" = "$sdw_source_sha" ] ||
  die 'input WCD9378 SoundWire source differs'
[ "$(hash "$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dts")" = "$dts_source_sha" ] ||
  die 'input FP6 DTS differs'
git -C "$source_tree" apply --check "$mapping_patch"
git -C "$source_tree" apply "$mapping_patch"

mounted_dev=false
mounted_proc=false
mounted_rosetta=false
mounted_sys=false
cleanup() {
  if [ "$mounted_rosetta" = true ]; then umount -l -R "$rootfs/mnt/lima-rosetta" 2>/dev/null || true; fi
  if [ "$mounted_sys" = true ]; then umount -l -R "$rootfs/sys" 2>/dev/null || true; fi
  if [ "$mounted_proc" = true ]; then umount -l "$rootfs/proc" 2>/dev/null || true; fi
  if [ "$mounted_dev" = true ]; then umount -l -R "$rootfs/dev" 2>/dev/null || true; fi
}
trap cleanup EXIT HUP INT TERM

mount --rbind /dev "$rootfs/dev"; mounted_dev=true
mount -t proc proc "$rootfs/proc"; mounted_proc=true
mount --rbind /sys "$rootfs/sys"; mounted_sys=true
mount --rbind /mnt/lima-rosetta "$rootfs/mnt/lima-rosetta"; mounted_rosetta=true

chroot "$rootfs" /bin/sh -eu -c "
  cd /work/linux
  grep -qx 'CONFIG_MODULE_ALLOW_BTF_MISMATCH=y' .config
  test \"\$(make ARCH=arm64 LLVM=1 LOCALVERSION= -s kernelrelease)\" = 7.1.2
  make -j4 ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos \\
    KBUILD_EXTRA_SYMBOLS=/work/linux/drivers/base/regmap/Module.symvers \\
    M=sound/soc/codecs modules
  make -j4 ARCH=arm64 LLVM=1 KBUILD_BUILD_USER=pmos \\
    KBUILD_BUILD_HOST=build KBUILD_BUILD_TIMESTAMP='$build_timestamp' \\
    KBUILD_BUILD_VERSION=1-postmarketos-qcom-milos \\
    qcom/milos-fairphone-fp6.dtb
"

module=$source_tree/sound/soc/codecs/snd-soc-wcd9378-sdw.ko
dtb=$source_tree/arch/arm64/boot/dts/qcom/milos-fairphone-fp6.dtb
[ -f "$module" ] || die 'rebuilt WCD9378 SoundWire module is missing'
[ -f "$dtb" ] || die 'rebuilt FP6 DTB is missing'
[ "$(hash "$dtb")" != "$dtb_sha" ] || die 'DTB did not change'

mkdir -p "$output_dir"
install -m 0644 "$module" "$output_dir/snd-soc-wcd9378-sdw.ko"
install -m 0644 "$dtb" "$output_dir/milos-fairphone-fp6.dtb"
{
  printf 'LUMA_FP6_AMIC3_ROUTING_BUILD_VERSION=1\n'
  printf 'KERNEL_RELEASE=7.1.2\n'
  printf 'BASE_KERNEL_SHA256=%s\n' "$image_sha"
  printf 'PATCH_SHA256=%s\n' "$patch_sha"
  printf 'WCD9378_SDW_KO_SHA256=%s\n' "$(hash "$output_dir/snd-soc-wcd9378-sdw.ko")"
  printf 'DTB_SHA256=%s\n' "$(hash "$output_dir/milos-fairphone-fp6.dtb")"
  printf 'MODULE_BTF_BASE=v3\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'FP6 AMIC3 routing bundle: %s\n' "$output_dir"
