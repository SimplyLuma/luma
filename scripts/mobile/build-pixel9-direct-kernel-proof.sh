#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Build an offline Tokay kernel which keeps Linux's stock-compatible arm64
# PE/COFF entry, links one exact native DTB, and embeds the already-proved
# storage-unmounted diagnostic initramfs. This produces no Android wrapper and
# never contacts, unlocks, boots, or flashes a phone.

set -euo pipefail
umask 022
export GIT_PAGER=cat

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-direct-kernel-proof.sh SOURCE_ROOT INITRAMFS_CPIO OUTPUT_DIR}
initramfs=${2:?usage: build-pixel9-direct-kernel-proof.sh SOURCE_ROOT INITRAMFS_CPIO OUTPUT_DIR}
output_dir=${3:?usage: build-pixel9-direct-kernel-proof.sh SOURCE_ROOT INITRAMFS_CPIO OUTPUT_DIR}
linux=$source_root/linux-zumapro
source_manifest=$source_root/luma-source-manifest.env
source_dir=$output_dir/source
build_dir=$output_dir/build
artifact_dir=$output_dir/artifacts
patch_path=$repo_root/$PIXEL9_DIRECT_KERNEL_PATCH
persistent_dtb_patch_path=$repo_root/$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH
copy_dtb_patch_path=$repo_root/$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH
entry_inspector=$repo_root/scripts/mobile/inspect-pixel9-kernel-entry.py
jobs=${LUMA_PIXEL9_KERNEL_BUILD_JOBS:-8}
diagnostic_variant=${LUMA_PIXEL9_DIAGNOSTIC_VARIANT:-acm-v1}
initramfs_kernel_compression=none
bootargs='reboot=warm panic=10 rdinit=/init loglevel=7 printk.time=1 module.sig_enforce=1'

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

case "$jobs" in
  ''|*[!0-9]*) die 'LUMA_PIXEL9_KERNEL_BUILD_JOBS must be a positive integer' ;;
esac
[ "$jobs" -ge 1 ] || die 'LUMA_PIXEL9_KERNEL_BUILD_JOBS must be at least 1'
case "$diagnostic_variant" in
  acm-v1)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=1'
    report_transports=usb-acm
    embedded_dtb_lifetime=init-rodata
    persistent_dtb_patch_sha=none
    copy_dtb_patch_sha=none
    ;;
  acm-ecm-v2)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=2'
    report_transports=usb-acm,usb-ecm-http
    embedded_dtb_lifetime=init-rodata
    persistent_dtb_patch_sha=none
    copy_dtb_patch_sha=none
    ;;
  acm-ecm-persistent-dtb-v3)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=2'
    report_transports=usb-acm,usb-ecm-http
    embedded_dtb_lifetime=persistent-rodata
    persistent_dtb_patch_sha=$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256
    copy_dtb_patch_sha=none
    ;;
  acm-ecm-copy-dtb-v4)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_ECM_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=2'
    report_transports=usb-acm,usb-ecm-http
    embedded_dtb_lifetime=copied-from-init-rodata
    persistent_dtb_patch_sha=none
    copy_dtb_patch_sha=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256
    ;;
  ufs-delay-copy-dtb-v5)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_UFS_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_UFS_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=3'
    report_transports=usb-ecm-http
    embedded_dtb_lifetime=copied-from-init-rodata
    persistent_dtb_patch_sha=none
    copy_dtb_patch_sha=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256
    ;;
  fedora-userspace-copy-dtb-v6)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=4'
    report_transports=usb-ecm-http
    embedded_dtb_lifetime=copied-from-init-rodata
    persistent_dtb_patch_sha=none
    copy_dtb_patch_sha=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256
    ;;
  fedora-zstd-userspace-copy-dtb-v7)
    expected_initramfs_bytes=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_BYTES
    expected_initramfs_sha=$PIXEL9_DIAGNOSTIC_FEDORA_INITRAMFS_SHA256
    expected_report_marker='LUMA_PIXEL9_DIAGNOSTIC_REPORT_VERSION=4'
    report_transports=usb-ecm-http
    embedded_dtb_lifetime=copied-from-init-rodata
    persistent_dtb_patch_sha=none
    copy_dtb_patch_sha=$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256
    initramfs_kernel_compression=zstd
    ;;
  *) die "unsupported diagnostic variant: $diagnostic_variant" ;;
esac
[ "$(uname -s)" = Linux ] || die 'the Pixel 9 direct-kernel proof requires Linux'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to compile on a Pixel target' ;;
  esac
fi

for tool in awk clang cmp cpio file fdtget git install make python3 sha256sum strings; do
  command -v "$tool" >/dev/null 2>&1 || die "missing direct-kernel build tool: $tool"
done
if [ "$initramfs_kernel_compression" = zstd ]; then
  command -v zstd >/dev/null 2>&1 || die 'missing Zstandard tool for the compressed Fedora initramfs'
fi
for required in "$source_manifest" "$initramfs" "$patch_path" "$entry_inspector"; do
  [ -f "$required" ] || die "missing direct-kernel input: $required"
done
if [ "$embedded_dtb_lifetime" = persistent-rodata ]; then
  [ -f "$persistent_dtb_patch_path" ] || die 'persistent-DTB patch input is absent'
fi
if [ "$embedded_dtb_lifetime" = copied-from-init-rodata ]; then
  [ -f "$copy_dtb_patch_path" ] || die 'copy-DTB patch input is absent'
fi
[ -d "$linux/.git" ] || die "missing full Linux checkout: $linux"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(git -C "$linux" rev-parse HEAD)" = "$PIXEL9_LINUX_COMMIT" ] ||
  die 'Linux checkout differs from the pinned commit'
[ -z "$(git -C "$linux" status --porcelain --untracked-files=no)" ] ||
  die 'Linux checkout contains tracked modifications'
[ "$(sha256sum "$linux/arch/arm64/configs/$PIXEL9_LINUX_DEFCONFIG" | awk '{print $1}')" = \
  "$PIXEL9_LINUX_DEFCONFIG_SHA256" ] || die 'Tokay defconfig checksum differs'
[ "$(sha256sum "$patch_path" | awk '{print $1}')" = "$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" ] ||
  die 'direct-kernel patch checksum differs'
if [ "$embedded_dtb_lifetime" = persistent-rodata ]; then
  [ "$(sha256sum "$persistent_dtb_patch_path" | awk '{print $1}')" = \
    "$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256" ] ||
    die 'persistent-DTB patch checksum differs'
fi
if [ "$embedded_dtb_lifetime" = copied-from-init-rodata ]; then
  [ "$(sha256sum "$copy_dtb_patch_path" | awk '{print $1}')" = \
    "$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256" ] ||
    die 'copy-DTB patch checksum differs'
fi
[ "$(stat -c %s "$initramfs")" = "$expected_initramfs_bytes" ] ||
  die 'diagnostic initramfs byte size differs from the pin'
[ "$(sha256sum "$initramfs" | awk '{print $1}')" = "$expected_initramfs_sha" ] ||
  die 'diagnostic initramfs digest differs from the pin'
grep -Fqx "LINUX_COMMIT=$PIXEL9_LINUX_COMMIT" "$source_manifest" ||
  die 'full-source manifest does not match the Linux pin'
grep -Fqx 'BUILD_READY=true' "$source_manifest" ||
  die 'source checkout is not build-ready'
grep -Fqx 'PHONE_ACCESSED=false' "$source_manifest" ||
  die 'full-source manifest lacks the no-phone boundary'

mkdir -p "$output_dir"
git clone -q --no-hardlinks --no-checkout "$linux" "$source_dir"
git -C "$source_dir" checkout -q --detach "$PIXEL9_LINUX_COMMIT"
git -C "$source_dir" apply --check "$patch_path"
git -C "$source_dir" apply "$patch_path"
if [ "$embedded_dtb_lifetime" = persistent-rodata ]; then
  git -C "$source_dir" apply --check "$persistent_dtb_patch_path"
  git -C "$source_dir" apply "$persistent_dtb_patch_path"
  grep -Fqx $'\t.section ".rodata", "a"' \
    "$source_dir/arch/arm64/kernel/luma_tokay_dtb.S" ||
    die 'embedded Tokay DTB is not in persistent read-only data'
  if grep -Fq '.init.rodata' "$source_dir/arch/arm64/kernel/luma_tokay_dtb.S"; then
    die 'embedded Tokay DTB still references freed init data'
  fi
fi
if [ "$embedded_dtb_lifetime" = copied-from-init-rodata ]; then
  git -C "$source_dir" apply --check "$copy_dtb_patch_path"
  git -C "$source_dir" apply "$copy_dtb_patch_path"
  grep -Fqx $'\t.section ".init.rodata", "a"' \
    "$source_dir/arch/arm64/kernel/luma_tokay_dtb.S" ||
    die 'copy-DTB variant changed the accepted init-section placement'
  grep -Fq 'unflatten_and_copy_device_tree();' \
    "$source_dir/arch/arm64/kernel/setup.c" ||
    die 'copy-DTB variant does not select the built-in-FDT copy path'
fi
git -C "$source_dir" diff --check
mkdir -p "$source_dir/luma" "$build_dir" "$artifact_dir"
install -m 0644 "$initramfs" "$source_dir/luma/diagnostic-initramfs.cpio"

source_date_epoch=$(git -C "$linux" show -s --format=%ct "$PIXEL9_LINUX_COMMIT")
export SOURCE_DATE_EPOCH="$source_date_epoch"
export KBUILD_BUILD_TIMESTAMP="@$source_date_epoch"
export KBUILD_BUILD_USER=luma
export KBUILD_BUILD_HOST=pixel9-builder
export KBUILD_BUILD_VERSION=1
# GNU build IDs cover unstripped link inputs, including assembler file symbols.
# Canonicalize both independent work directories so the kernel, VDSO, and
# compat-VDSO build IDs remain content-derived without absorbing host paths.
export KCFLAGS="-ffile-prefix-map=$source_dir=/usr/src/luma-linux-zumapro -ffile-prefix-map=$build_dir=/usr/lib/debug/luma-linux-zumapro"
export KAFLAGS="$KCFLAGS"
# arm64's compat vDSO deliberately builds with an isolated 32-bit flag set and
# therefore does not consume KCFLAGS/KAFLAGS. Supply its compiler explicitly so
# its debug records and content-derived build ID use the same canonical paths.
compat_cc="clang --target=arm-linux-gnueabi $KCFLAGS"

make -C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 CC_COMPAT="$compat_cc" \
  "$PIXEL9_LINUX_DEFCONFIG"
config="$source_dir/scripts/config --file $build_dir/.config"
$config --enable LUMA_TOKAY_EMBEDDED_DIAGNOSTIC
$config --enable EFI
$config --enable CMDLINE_FORCE
$config --set-str CMDLINE "$bootargs"
$config --set-str INITRAMFS_SOURCE luma/diagnostic-initramfs.cpio
$config --disable INITRAMFS_COMPRESSION_NONE
$config --disable INITRAMFS_COMPRESSION_GZIP
$config --disable INITRAMFS_COMPRESSION_BZIP2
$config --disable INITRAMFS_COMPRESSION_LZMA
$config --disable INITRAMFS_COMPRESSION_XZ
$config --disable INITRAMFS_COMPRESSION_LZO
$config --disable INITRAMFS_COMPRESSION_LZ4
$config --disable INITRAMFS_COMPRESSION_ZSTD
case "$initramfs_kernel_compression" in
  none) $config --enable INITRAMFS_COMPRESSION_NONE ;;
  zstd) $config --enable INITRAMFS_COMPRESSION_ZSTD ;;
  *) die "unsupported kernel initramfs compression: $initramfs_kernel_compression" ;;
esac
make -C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 CC_COMPAT="$compat_cc" olddefconfig

for required_config in \
  'CONFIG_LUMA_TOKAY_EMBEDDED_DIAGNOSTIC=y' \
  'CONFIG_EFI=y' \
  'CONFIG_EFI_STUB=y' \
  'CONFIG_CMDLINE_FORCE=y' \
  "CONFIG_CMDLINE=\"$bootargs\"" \
  'CONFIG_INITRAMFS_SOURCE="luma/diagnostic-initramfs.cpio"'; do
  grep -Fqx "$required_config" "$build_dir/.config" ||
    die "required direct-kernel configuration is absent: $required_config"
done
case "$initramfs_kernel_compression" in
  none) required_compression_config='CONFIG_INITRAMFS_COMPRESSION_NONE=y' ;;
  zstd) required_compression_config='CONFIG_INITRAMFS_COMPRESSION_ZSTD=y' ;;
esac
grep -Fqx "$required_compression_config" "$build_dir/.config" ||
  die "required initramfs compression configuration is absent: $required_compression_config"

# The linked assembly intentionally consumes the generated DTB, so build it
# first and only then link the kernel image.
make -C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 CC_COMPAT="$compat_cc" -j"$jobs" dtbs
make -C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 CC_COMPAT="$compat_cc" -j"$jobs" Image

image=$build_dir/arch/arm64/boot/Image
dtb=$build_dir/arch/arm64/boot/dts/exynos/google/zumapro-tokay.dtb
embedded_initramfs=$build_dir/usr/initramfs_inc_data
for artifact in "$image" "$dtb" "$embedded_initramfs" "$build_dir/.config"; do
  [ -s "$artifact" ] || die "direct-kernel artifact is missing: $artifact"
done
case "$initramfs_kernel_compression" in
  none)
    cmp "$embedded_initramfs" "$initramfs" ||
      die 'uncompressed linked initramfs differs from the audited CPIO'
    ;;
  zstd)
    zstd --quiet --decompress --stdout "$embedded_initramfs" |
      cmp - "$initramfs" || die 'Zstandard linked initramfs does not round-trip to the audited CPIO'
    ;;
esac

python3 "$entry_inspector" --require-stock-contract "$image" \
  >"$artifact_dir/entry-contract.env"
python3 - "$image" "$dtb" "$embedded_initramfs" <<'PY'
import pathlib
import sys

image = pathlib.Path(sys.argv[1]).read_bytes()
for label, path in (("Tokay DTB", sys.argv[2]), ("linked diagnostic initramfs", sys.argv[3])):
    payload = pathlib.Path(path).read_bytes()
    count = image.count(payload)
    if count != 1:
        raise SystemExit(f"expected one exact embedded {label}, found {count}")
PY
# The marker is a quoted argument inside /init, not a standalone string table
# entry. Extract once before searching: with pipefail, grep -q can otherwise
# close a large Fedora CPIO pipe early and turn cpio's harmless SIGPIPE into a
# nondeterministic validation failure. The exact-byte check above proves that
# this audited initramfs is present once in the Image.
archived_init=$artifact_dir/diagnostic-init
cpio -i --quiet --to-stdout init <"$initramfs" >"$archived_init"
chmod 0755 "$archived_init"
grep -Fq "$expected_report_marker" "$archived_init" ||
  die 'diagnostic report marker is absent from the embedded init script'
case "$diagnostic_variant" in acm-ecm-v2|acm-ecm-persistent-dtb-v3|acm-ecm-copy-dtb-v4|ufs-delay-copy-dtb-v5|fedora-userspace-copy-dtb-v6|fedora-zstd-userspace-copy-dtb-v7)
  grep -Fq 'USB_ECM_HTTP_READY=true' "$archived_init" ||
    die 'ECM report transport marker is absent from the embedded init script'
;; esac
fdtget -t s "$dtb" / model | grep -Fqx 'Pixel 9' || die 'embedded DTB model differs'
fdtget -t s "$dtb" / compatible | grep -Fq 'google,tokay' ||
  die 'embedded DTB compatibility differs'

install -m 0644 "$image" "$artifact_dir/Image"
install -m 0644 "$dtb" "$artifact_dir/zumapro-tokay.dtb"
install -m 0644 "$initramfs" "$artifact_dir/diagnostic-initramfs.cpio"
install -m 0644 "$embedded_initramfs" "$artifact_dir/embedded-initramfs.data"
install -m 0644 "$build_dir/.config" "$artifact_dir/config"

kernel_release=$(make -s -C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 kernelrelease)
compiler_line=$(clang --version | sed -n '1p' | tr '\n' ' ')
{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_PROOF_VERSION=1\n'
  printf 'SCOPE=offline-embedded-dtb-initramfs-entry-proof\n'
  printf 'DIAGNOSTIC_VARIANT=%s\n' "$diagnostic_variant"
  printf 'REPORT_TRANSPORTS=%s\n' "$report_transports"
  printf 'LINUX_COMMIT=%s\n' "$PIXEL9_LINUX_COMMIT"
  printf 'DIRECT_KERNEL_PATCH_SHA256=%s\n' "$PIXEL9_DIRECT_KERNEL_PATCH_SHA256"
  printf 'PERSISTENT_DTB_PATCH_SHA256=%s\n' "$persistent_dtb_patch_sha"
  printf 'COPY_DTB_PATCH_SHA256=%s\n' "$copy_dtb_patch_sha"
  printf 'EMBEDDED_DTB_LIFETIME=%s\n' "$embedded_dtb_lifetime"
  printf 'SOURCE_DATE_EPOCH=%s\n' "$source_date_epoch"
  printf 'KERNEL_RELEASE=%s\n' "$kernel_release"
  printf 'COMPILER=%s\n' "$compiler_line"
  printf 'BUILD_HOST=%s/%s\n' "$(uname -s)" "$(uname -m)"
  printf 'BUILD_PATHS_NORMALIZED=true\n'
  printf 'COMPAT_VDSO_PATHS_NORMALIZED=true\n'
  printf 'CANONICAL_SOURCE_PATH=/usr/src/luma-linux-zumapro\n'
  printf 'CANONICAL_BUILD_PATH=/usr/lib/debug/luma-linux-zumapro\n'
  printf 'ENTRY_CONTRACT=linux-arm64-pe32plus-efi-application\n'
  printf 'ENTRY_CONTRACT_MATCHES_STOCK=true\n'
  printf 'PE_COFF_HEADER_PRESENT=true\n'
  printf 'PE_MACHINE=0xaa64\n'
  printf 'PE_SUBSYSTEM=10\n'
  printf 'TOKAY_DTB_EMBEDDED_ONCE=true\n'
  printf 'INITRAMFS_EMBEDDED_ONCE=true\n'
  printf 'BOOTARGS_FORCED=true\n'
  printf 'IMAGE_BYTES=%s\n' "$(stat -c %s "$artifact_dir/Image")"
  printf 'IMAGE_SHA256=%s\n' "$(sha256sum "$artifact_dir/Image" | awk '{print $1}')"
  printf 'TOKAY_DTB_SHA256=%s\n' "$(sha256sum "$artifact_dir/zumapro-tokay.dtb" | awk '{print $1}')"
  printf 'INITRAMFS_BYTES=%s\n' "$expected_initramfs_bytes"
  printf 'INITRAMFS_SHA256=%s\n' "$expected_initramfs_sha"
  printf 'INITRAMFS_KERNEL_COMPRESSION=%s\n' "$initramfs_kernel_compression"
  printf 'EMBEDDED_INITRAMFS_BYTES=%s\n' "$(stat -c %s "$embedded_initramfs")"
  printf 'EMBEDDED_INITRAMFS_SHA256=%s\n' "$(sha256sum "$embedded_initramfs" | awk '{print $1}')"
  printf 'PERSISTENT_FILESYSTEM_MOUNTS=false\n'
  printf 'MODULES_INCLUDED=false\n'
  printf 'INTERACTIVE_SHELL=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 direct-kernel offline proof: %s\n' "$output_dir"
