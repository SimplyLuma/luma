#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Assemble a deterministic, storage-unmounted Tokay diagnostic FIT. This is an
# offline artifact builder; it refuses to run on a Pixel and never packages an
# Android boot image or contacts a device.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-diagnostic-fit.sh SOURCE_ROOT MODULE_PROOF BUSYBOX OUTPUT_DIR}
module_proof=${2:?usage: build-pixel9-diagnostic-fit.sh SOURCE_ROOT MODULE_PROOF BUSYBOX OUTPUT_DIR}
busybox=${3:?usage: build-pixel9-diagnostic-fit.sh SOURCE_ROOT MODULE_PROOF BUSYBOX OUTPUT_DIR}
output_dir=${4:?usage: build-pixel9-diagnostic-fit.sh SOURCE_ROOT MODULE_PROOF BUSYBOX OUTPUT_DIR}
artifact_dir=$output_dir/artifacts
root=$output_dir/initramfs-root
tools_build=$output_dir/uboot-tools-build
init_source=$repo_root/config/mobile/pixel9-physical/diagnostic-init
jobs=${LUMA_PIXEL9_FIT_BUILD_JOBS:-4}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

case "$jobs" in
  ''|*[!0-9]*) die 'LUMA_PIXEL9_FIT_BUILD_JOBS must be a positive integer' ;;
esac
[ "$jobs" -ge 1 ] || die 'LUMA_PIXEL9_FIT_BUILD_JOBS must be at least 1'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'the Pixel 9 diagnostic FIT must be built off-device on Linux/aarch64'

if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to assemble a payload on a Pixel target' ;;
  esac
fi

for tool in awk cpio file fdtget fdtput find gcc git install make \
  python3 rpm sha256sum sort stat touch; do
  command -v "$tool" >/dev/null 2>&1 || die "missing diagnostic FIT tool: $tool"
done

uboot=$source_root/u-boot-zumapro
source_manifest=$source_root/luma-source-manifest.env
proof_manifest=$module_proof/manifest.env
kernel=$module_proof/artifacts/Image
dtb=$module_proof/artifacts/zumapro-tokay.dtb

[ -d "$uboot/.git" ] || die "missing full U-Boot checkout: $uboot"
[ -f "$source_manifest" ] || die "missing full-source manifest: $source_manifest"
[ -f "$proof_manifest" ] || die "missing module proof manifest: $proof_manifest"
[ -f "$kernel" ] || die "missing proved kernel Image: $kernel"
[ -f "$dtb" ] || die "missing proved Tokay DTB: $dtb"
[ -f "$busybox" ] || die "missing static BusyBox: $busybox"
[ -f "$init_source" ] || die "missing diagnostic init policy: $init_source"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(git -C "$uboot" rev-parse HEAD)" = "$PIXEL9_UBOOT_COMMIT" ] || \
  die 'U-Boot checkout differs from the pinned commit'
[ -z "$(git -C "$uboot" status --porcelain --untracked-files=no)" ] || \
  die 'U-Boot checkout contains tracked modifications'
grep -Fqx "UBOOT_COMMIT=$PIXEL9_UBOOT_COMMIT" "$source_manifest" || \
  die 'full-source manifest does not match the U-Boot pin'
grep -Fqx "LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" "$proof_manifest" || \
  die 'module proof does not match the Linux pin'
grep -Fqx 'PHONE_ACCESSED=false' "$proof_manifest" || \
  die 'module proof lacks the no-phone boundary'
grep -Fqx 'MODULES_BUILT=true' "$proof_manifest" || \
  die 'module compile proof is incomplete'
grep -Fqx "IMAGE_SHA256=$(sha256sum "$kernel" | awk '{print $1}')" "$proof_manifest" || \
  die 'kernel Image differs from its proof manifest'
grep -Fqx "TOKAY_DTB_SHA256=$(sha256sum "$dtb" | awk '{print $1}')" "$proof_manifest" || \
  die 'Tokay DTB differs from its proof manifest'

[ "$(stat -c %s "$busybox")" = "$PIXEL9_DIAGNOSTIC_BUSYBOX_BYTES" ] || \
  die 'BusyBox byte size differs from the pin'
[ "$(sha256sum "$busybox" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_BUSYBOX_SHA256" ] || \
  die 'BusyBox digest differs from the pin'
[ "$(rpm -qf --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$busybox")" = \
  "$PIXEL9_DIAGNOSTIC_BUSYBOX_NEVRA" ] || die 'BusyBox package identity differs from the pin'
file "$busybox" | grep -Fq 'statically linked' || die 'BusyBox is not statically linked'
[ "$(sha256sum "$init_source" | awk '{print $1}')" = "$PIXEL9_DIAGNOSTIC_INIT_SHA256" ] || \
  die 'diagnostic init policy differs from the pin'

mkdir -p "$artifact_dir" "$root/bin" "$tools_build"
source_date_epoch=$(git -C "$uboot" show -s --format=%ct "$PIXEL9_UBOOT_COMMIT")
export SOURCE_DATE_EPOCH="$source_date_epoch"
export KBUILD_BUILD_TIMESTAMP="@$source_date_epoch"
export KBUILD_BUILD_USER=luma
export KBUILD_BUILD_HOST=pixel9-builder

# Build mkimage from the same immutable U-Boot source used by the loader. No
# distribution-supplied image formatter enters the candidate boundary.
make -C "$uboot" O="$tools_build" tools-only_defconfig
make -C "$uboot" O="$tools_build" -j"$jobs" tools-only
mkimage=$tools_build/tools/mkimage
[ -x "$mkimage" ] || die 'pinned U-Boot build did not produce mkimage'

install -m 0755 "$busybox" "$root/bin/busybox"
install -m 0755 "$init_source" "$root/init"
find "$root" -exec touch -h -d "@$source_date_epoch" {} +
(
  cd "$root"
  find . -print0 | LC_ALL=C sort -z | \
    cpio --null --quiet --create --format=newc --reproducible --owner=0:0
) >"$artifact_dir/initramfs.cpio"

install -m 0644 "$kernel" "$artifact_dir/Image"
install -m 0644 "$dtb" "$artifact_dir/zumapro-tokay.dtb"
bootargs='reboot=warm panic=10 rdinit=/init loglevel=7 printk.time=1 module.sig_enforce=1'
fdtput -t s "$artifact_dir/zumapro-tokay.dtb" /chosen bootargs "$bootargs"

kernel_size=$(stat -c %s "$artifact_dir/Image")
dtb_size=$(stat -c %s "$artifact_dir/zumapro-tokay.dtb")
initramfs_size=$(stat -c %s "$artifact_dir/initramfs.cpio")
kernel_image_size=$(python3 - "$artifact_dir/Image" <<'PY'
import pathlib
import struct
import sys

data = pathlib.Path(sys.argv[1]).read_bytes()[:64]
if len(data) != 64 or struct.unpack_from('<I', data, 56)[0] != 0x644d5241:
    raise SystemExit('kernel does not have an AArch64 Image header')
print(struct.unpack_from('<Q', data, 16)[0])
PY
)

hex_to_dec() {
  printf '%d\n' "$(( $1 ))"
}
kernel_load=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_KERNEL_LOAD")
kernel_window_end=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_KERNEL_WINDOW_END")
fdt_load=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_FDT_LOAD")
fdt_window_end=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_FDT_WINDOW_END")
initramfs_load=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_INITRAMFS_LOAD")
initramfs_window_end=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_INITRAMFS_WINDOW_END")
reserved_start=$(hex_to_dec "$PIXEL9_DIAGNOSTIC_FIRST_RESERVED_START")

[ $((kernel_load + kernel_image_size)) -le "$kernel_window_end" ] || \
  die 'AArch64 Image exceeds its bounded load window'
[ $((fdt_load + dtb_size)) -le "$fdt_window_end" ] || \
  die 'Tokay DTB exceeds its bounded load window'
[ $((initramfs_load + initramfs_size)) -le "$initramfs_window_end" ] || \
  die 'diagnostic initramfs exceeds its bounded load window'
[ "$kernel_window_end" -le "$fdt_load" ] || die 'kernel and FDT windows overlap'
[ "$fdt_window_end" -le "$initramfs_load" ] || die 'FDT and initramfs windows overlap'
[ "$initramfs_window_end" -le "$reserved_start" ] || \
  die 'a diagnostic load window reaches Tokay reserved memory'

cat >"$output_dir/payload.its" <<EOF
/dts-v1/;

/ {
  description = "Luma Pixel 9 volatile diagnostic";
  #address-cells = <1>;

  images {
    kernel {
      description = "Pinned Zumapro Linux Image";
      data = /incbin/("artifacts/Image");
      type = "kernel";
      arch = "arm64";
      os = "linux";
      compression = "none";
      load = <$PIXEL9_DIAGNOSTIC_KERNEL_LOAD>;
      entry = <$PIXEL9_DIAGNOSTIC_KERNEL_LOAD>;
      hash-1 { algo = "sha256"; };
    };
    fdt {
      description = "Pinned Tokay device tree with diagnostic bootargs";
      data = /incbin/("artifacts/zumapro-tokay.dtb");
      type = "flat_dt";
      arch = "arm64";
      compression = "none";
      load = <$PIXEL9_DIAGNOSTIC_FDT_LOAD>;
      hash-1 { algo = "sha256"; };
    };
    ramdisk {
      description = "Persistent-storage-unmounted BusyBox diagnostic initramfs";
      data = /incbin/("artifacts/initramfs.cpio");
      type = "ramdisk";
      arch = "arm64";
      os = "linux";
      compression = "none";
      load = <$PIXEL9_DIAGNOSTIC_INITRAMFS_LOAD>;
      hash-1 { algo = "sha256"; };
    };
  };

  configurations {
    default = "tokay-diagnostic";
    tokay-diagnostic {
      description = "Tokay native Linux volatile diagnostic";
      kernel = "kernel";
      fdt = "fdt";
      ramdisk = "ramdisk";
    };
  };
};
EOF

(
  cd "$output_dir"
  "$mkimage" -f payload.its artifacts/payload.fit
)

fit_size=$(stat -c %s "$artifact_dir/payload.fit")
python3 - "$artifact_dir/payload.fit" <<'PY'
import pathlib
import struct
import sys

path = pathlib.Path(sys.argv[1])
header = path.read_bytes()[:8]
if len(header) != 8:
    raise SystemExit('FIT is shorter than its FDT header')
magic, total = struct.unpack('>II', header)
if magic != 0xd00dfeed or total != path.stat().st_size:
    raise SystemExit('FIT header does not cover the complete inline payload')
PY

for spec in \
  "/images/kernel:$PIXEL9_DIAGNOSTIC_KERNEL_LOAD" \
  "/images/fdt:$PIXEL9_DIAGNOSTIC_FDT_LOAD" \
  "/images/ramdisk:$PIXEL9_DIAGNOSTIC_INITRAMFS_LOAD"; do
  node=${spec%%:*}
  expected=${spec#*:}
  actual=$(fdtget -t x "$artifact_dir/payload.fit" "$node" load)
  [ "$((16#$actual))" -eq "$((expected))" ] || die "FIT load address differs: $node"
done

{
  printf 'LUMA_PIXEL9_DIAGNOSTIC_FIT_VERSION=1\n'
  printf 'SCOPE=offline-volatile-storage-unmounted-diagnostic\n'
  printf 'LINUX_COMMIT=%s\n' "$PIXEL9_LINUX_COMMIT"
  printf 'UBOOT_COMMIT=%s\n' "$PIXEL9_UBOOT_COMMIT"
  printf 'SOURCE_DATE_EPOCH=%s\n' "$source_date_epoch"
  printf 'BUSYBOX_NEVRA=%s\n' "$PIXEL9_DIAGNOSTIC_BUSYBOX_NEVRA"
  printf 'BUSYBOX_SHA256=%s\n' "$PIXEL9_DIAGNOSTIC_BUSYBOX_SHA256"
  printf 'MKIMAGE_SHA256=%s\n' "$(sha256sum "$mkimage" | awk '{print $1}')"
  printf 'IMAGE_BYTES=%s\n' "$kernel_size"
  printf 'IMAGE_HEADER_SIZE=%s\n' "$kernel_image_size"
  printf 'IMAGE_SHA256=%s\n' "$(sha256sum "$artifact_dir/Image" | awk '{print $1}')"
  printf 'TOKAY_DTB_BYTES=%s\n' "$dtb_size"
  printf 'TOKAY_DTB_SHA256=%s\n' "$(sha256sum "$artifact_dir/zumapro-tokay.dtb" | awk '{print $1}')"
  printf 'INITRAMFS_BYTES=%s\n' "$initramfs_size"
  printf 'INITRAMFS_SHA256=%s\n' "$(sha256sum "$artifact_dir/initramfs.cpio" | awk '{print $1}')"
  printf 'FIT_BYTES=%s\n' "$fit_size"
  printf 'FIT_SHA256=%s\n' "$(sha256sum "$artifact_dir/payload.fit" | awk '{print $1}')"
  printf 'KERNEL_LOAD=%s\n' "$PIXEL9_DIAGNOSTIC_KERNEL_LOAD"
  printf 'FDT_LOAD=%s\n' "$PIXEL9_DIAGNOSTIC_FDT_LOAD"
  printf 'INITRAMFS_LOAD=%s\n' "$PIXEL9_DIAGNOSTIC_INITRAMFS_LOAD"
  printf 'FIRST_RESERVED_START=%s\n' "$PIXEL9_DIAGNOSTIC_FIRST_RESERVED_START"
  printf 'PERSISTENT_FILESYSTEM_MOUNTS=false\n'
  printf 'MODULES_INCLUDED=false\n'
  printf 'MODULE_AUTOLOAD=false\n'
  printf 'INTERACTIVE_SHELL=false\n'
  printf 'AUTOMATIC_REBOOT_SECONDS=180\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 offline diagnostic FIT: %s\n' "$output_dir"
