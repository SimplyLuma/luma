#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Encode one verified direct Tokay kernel proof with the stock legacy-LZ4 and
# Android header-v4 shape, then round-trip every layer. This is offline
# assembly only: it never contacts, unlocks, boots, or flashes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-direct-kernel-wrapper.sh SOURCE_ROOT STOCK_AUDIT DIRECT_PROOF OUTPUT_DIR}
stock_audit=${2:?usage: build-pixel9-direct-kernel-wrapper.sh SOURCE_ROOT STOCK_AUDIT DIRECT_PROOF OUTPUT_DIR}
direct_proof=${3:?usage: build-pixel9-direct-kernel-wrapper.sh SOURCE_ROOT STOCK_AUDIT DIRECT_PROOF OUTPUT_DIR}
output_dir=${4:?usage: build-pixel9-direct-kernel-wrapper.sh SOURCE_ROOT STOCK_AUDIT DIRECT_PROOF OUTPUT_DIR}
tools_dir=$source_root/aosp-mkbootimg
mkbootimg=$tools_dir/mkbootimg.py
unpack_bootimg=$stock_audit/tools/unpack_bootimg.py
stock_manifest=$stock_audit/manifest.env
stock_info=$stock_audit/metadata/boot.info.txt
proof_manifest=$direct_proof/manifest.env
kernel=$direct_proof/artifacts/Image
compressed=$output_dir/Image.lz4
decompressed=$output_dir/Image.roundtrip
empty_ramdisk=$output_dir/empty-ramdisk
candidate=$output_dir/pixel9-read-only-ramboot.img
roundtrip=$output_dir/roundtrip
entry_inspector=$repo_root/scripts/mobile/inspect-pixel9-kernel-entry.py
entry_report=$(mktemp)
trap 'rm -f -- "$entry_report"' EXIT

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] || die 'the Pixel 9 direct wrapper requires an off-device Linux builder'
build_arch=$(uname -m)
case "$build_arch" in
  aarch64)
    lz4_nevra=$PIXEL9_LZ4_NEVRA
    lz4_sha=$PIXEL9_LZ4_BINARY_SHA256
    ;;
  x86_64)
    lz4_nevra=$PIXEL9_LZ4_X86_64_NEVRA
    lz4_sha=$PIXEL9_LZ4_X86_64_BINARY_SHA256
    ;;
  *) die "unsupported wrapper-builder architecture: $build_arch" ;;
esac
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to assemble a wrapper on a Pixel target' ;;
  esac
fi
for tool in awk cmp grep install mkdir python3 rpm sha256sum stat tr; do
  command -v "$tool" >/dev/null 2>&1 || die "missing direct-wrapper tool: $tool"
done
lz4_bin=/usr/bin/lz4
[ -x "$lz4_bin" ] || die 'pinned LZ4 executable is absent'

for required in "$mkbootimg" "$tools_dir/gki/generate_gki_certificate.py" \
  "$unpack_bootimg" "$stock_manifest" "$stock_info" "$proof_manifest" \
  "$kernel" "$entry_inspector"; do
  [ -f "$required" ] || die "direct-wrapper input is absent: $required"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(sha256sum "$mkbootimg" | awk '{print $1}')" = "$PIXEL9_MKBOOTIMG_SHA256" ] ||
  die 'mkbootimg.py differs from the pin'
[ "$(sha256sum "$tools_dir/gki/generate_gki_certificate.py" | awk '{print $1}')" = \
  "$PIXEL9_GKI_CERTIFICATE_TOOL_SHA256" ] || die 'GKI certificate helper differs from the pin'
[ "$(sha256sum "$unpack_bootimg" | awk '{print $1}')" = \
  "$PIXEL9_UNPACK_BOOTIMG_SHA256" ] || die 'unpack_bootimg.py differs from the pin'
[ "$(rpm -q lz4)" = "$lz4_nevra" ] || die 'LZ4 package identity differs from the architecture pin'
[ "$(sha256sum "$lz4_bin" | awk '{print $1}')" = "$lz4_sha" ] ||
  die 'LZ4 executable differs from the architecture pin'

grep -Fqx "FACTORY_SHA256=$PIXEL9_FACTORY_IMAGE_SHA256" "$stock_manifest" ||
  die 'stock audit does not match the exact recovery factory image'
grep -Fqx "MKBOOTIMG_COMMIT=$PIXEL9_MKBOOTIMG_COMMIT" "$stock_manifest" ||
  die 'stock audit does not match the AOSP tool revision'
for stock_boundary in \
  'boot image header version: 4' \
  'ramdisk size: 0' \
  'os version: None' \
  'os patch level: None' \
  'command line args: ' \
  'boot.img signature size: 0'; do
  grep -Fqx "$stock_boundary" "$stock_info" ||
    die "stock Tokay boot boundary changed: $stock_boundary"
done

kernel_bytes=$(stat -c %s "$kernel")
kernel_sha=$(sha256sum "$kernel" | awk '{print $1}')
diagnostic_variant=$(sed -n 's/^DIAGNOSTIC_VARIANT=//p' "$proof_manifest")
report_transports=$(sed -n 's/^REPORT_TRANSPORTS=//p' "$proof_manifest")
linux_commit=$(sed -n 's/^LINUX_COMMIT=//p' "$proof_manifest")
direct_patch_sha=$(sed -n 's/^DIRECT_KERNEL_PATCH_SHA256=//p' "$proof_manifest")
persistent_dtb_patch_sha=$(sed -n 's/^PERSISTENT_DTB_PATCH_SHA256=//p' "$proof_manifest")
copy_dtb_patch_sha=$(sed -n 's/^COPY_DTB_PATCH_SHA256=//p' "$proof_manifest")
embedded_dtb_lifetime=$(sed -n 's/^EMBEDDED_DTB_LIFETIME=//p' "$proof_manifest")
tokay_dtb_sha=$(sed -n 's/^TOKAY_DTB_SHA256=//p' "$proof_manifest")
initramfs_sha=$(sed -n 's/^INITRAMFS_SHA256=//p' "$proof_manifest")
initramfs_kernel_compression=$(sed -n 's/^INITRAMFS_KERNEL_COMPRESSION=//p' "$proof_manifest")
embedded_initramfs_sha=$(sed -n 's/^EMBEDDED_INITRAMFS_SHA256=//p' "$proof_manifest")
grep -Fqx 'LUMA_PIXEL9_DIRECT_KERNEL_PROOF_VERSION=1' "$proof_manifest" ||
  die 'direct-kernel manifest version differs'
[ -n "$diagnostic_variant" ] || die 'direct-kernel diagnostic variant is absent'
[ -n "$report_transports" ] || die 'direct-kernel report transports are absent'
[ "$linux_commit" = "$PIXEL9_LINUX_COMMIT" ] || die 'direct-kernel Linux revision differs from the pin'
[ "$direct_patch_sha" = "$PIXEL9_DIRECT_KERNEL_PATCH_SHA256" ] ||
  die 'direct-kernel patch digest differs from the pin'
[ -n "$persistent_dtb_patch_sha" ] || persistent_dtb_patch_sha=none
[ -n "$copy_dtb_patch_sha" ] || copy_dtb_patch_sha=none
[ -n "$embedded_dtb_lifetime" ] || embedded_dtb_lifetime=init-rodata
[ -n "$initramfs_kernel_compression" ] || initramfs_kernel_compression=none
[ -n "$embedded_initramfs_sha" ] || embedded_initramfs_sha=$initramfs_sha
case "$diagnostic_variant:$persistent_dtb_patch_sha:$copy_dtb_patch_sha:$embedded_dtb_lifetime" in
  acm-v1:none:none:init-rodata|acm-ecm-v2:none:none:init-rodata) ;;
  acm-ecm-persistent-dtb-v3:$PIXEL9_DIRECT_KERNEL_PERSISTENT_DTB_PATCH_SHA256:none:persistent-rodata) ;;
  acm-ecm-copy-dtb-v4:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  ufs-delay-copy-dtb-v5:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  fedora-userspace-copy-dtb-v6:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  fedora-zstd-userspace-copy-dtb-v7:none:$PIXEL9_DIRECT_KERNEL_COPY_DTB_PATCH_SHA256:copied-from-init-rodata) ;;
  *) die 'direct-kernel DTB lifetime provenance is inconsistent' ;;
esac
[ -n "$tokay_dtb_sha" ] || die 'direct-kernel Tokay DTB digest is absent'
[ -n "$initramfs_sha" ] || die 'direct-kernel initramfs digest is absent'
case "$diagnostic_variant:$initramfs_kernel_compression" in
  fedora-zstd-userspace-copy-dtb-v7:zstd) ;;
  fedora-zstd-userspace-copy-dtb-v7:*) die 'compressed-Fedora variant lacks Zstandard initramfs provenance' ;;
  *:none) ;;
  *) die 'unexpected initramfs compression provenance' ;;
esac
grep -Fqx "IMAGE_BYTES=$kernel_bytes" "$proof_manifest" ||
  die 'direct-kernel byte size differs from its manifest'
grep -Fqx "IMAGE_SHA256=$kernel_sha" "$proof_manifest" ||
  die 'direct-kernel digest differs from its manifest'
for proof_boundary in \
  'ENTRY_CONTRACT_MATCHES_STOCK=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_SUBSYSTEM=10' \
  'TOKAY_DTB_EMBEDDED_ONCE=true' \
  'INITRAMFS_EMBEDDED_ONCE=true' \
  'BOOTARGS_FORCED=true' \
  'PERSISTENT_FILESYSTEM_MOUNTS=false' \
  'MODULES_INCLUDED=false' \
  'INTERACTIVE_SHELL=false' \
  'PHONE_ACCESSED=false' \
  'ANDROID_BOOT_IMAGE_ASSEMBLED=false' \
  'BOOT_AUTHORIZED=false' \
  'FLASH_AUTHORIZED=false'; do
  grep -Fqx "$proof_boundary" "$proof_manifest" ||
    die "direct-kernel proof lacks: $proof_boundary"
done

python3 "$entry_inspector" --require-stock-contract "$kernel" >"$entry_report" ||
  die 'direct kernel does not satisfy the stock Tokay entry contract'
for entry_boundary in \
  'ARM64_IMAGE_MAGIC_PRESENT=true' \
  'DOS_MZ_SIGNATURE_PRESENT=true' \
  'PE_COFF_HEADER_PRESENT=true' \
  'PE_MACHINE=0xaa64' \
  'PE_OPTIONAL_MAGIC=0x20b' \
  'PE_SUBSYSTEM=10' \
  'PE_SUBSYSTEM_NAME=EFI_APPLICATION' \
  'PE_SECTIONS_STRUCTURALLY_VALID=true' \
  'PE_ENTRY_IN_SECTION=true' \
  'ENTRY_CONTRACT_MATCHES_STOCK=true'; do
  grep -Fqx "$entry_boundary" "$entry_report" ||
    die "direct-kernel entry proof lacks: $entry_boundary"
done

mkdir -p "$output_dir" "$roundtrip"
install -m 0644 "$entry_report" "$output_dir/kernel-entry-contract.env"
"$lz4_bin" -q -f -l -12 -T1 "$kernel" "$compressed"
python3 - "$compressed" <<'PY'
import pathlib
import sys

payload = pathlib.Path(sys.argv[1]).read_bytes()
if payload[:4] != b'\x02\x21\x4c\x18':
    raise SystemExit('kernel payload is not a legacy LZ4 stream')
PY
"$lz4_bin" -q -f -d "$compressed" "$decompressed"
cmp -s "$kernel" "$decompressed" || die 'LZ4 decompression round trip differs'
install -m 0644 /dev/null "$empty_ramdisk"

PYTHONPATH="$tools_dir" python3 "$mkbootimg" \
  --header_version 4 \
  --kernel "$compressed" \
  --output "$candidate"
python3 "$unpack_bootimg" --boot_img "$candidate" --out "$roundtrip" \
  --format=info >"$output_dir/wrapper.info.txt"
for wrapper_boundary in \
  'boot image header version: 4' \
  'ramdisk size: 0' \
  'os version: None' \
  'os patch level: None' \
  'command line args: ' \
  'boot.img signature size: 0'; do
  grep -Fqx "$wrapper_boundary" "$output_dir/wrapper.info.txt" ||
    die "wrapper boundary differs from stock: $wrapper_boundary"
done
grep -Fqx "kernel_size: $(stat -c %s "$compressed")" "$output_dir/wrapper.info.txt" ||
  die 'round-tripped compressed-kernel size differs'
cmp -s "$compressed" "$roundtrip/kernel" ||
  die 'round-tripped compressed-kernel bytes differ'
cmp -s "$empty_ramdisk" "$roundtrip/ramdisk" ||
  die 'round-tripped Android ramdisk is not empty'
[ "$(stat -c %s "$candidate")" -le "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] ||
  die 'kernel-only wrapper exceeds the stock boot image size'

{
  printf 'LUMA_PIXEL9_DIRECT_KERNEL_WRAPPER_VERSION=1\n'
  printf 'SCOPE=offline-v4-kernel-only-direct-kernel-wrapper\n'
  printf 'DIAGNOSTIC_VARIANT=%s\n' "$diagnostic_variant"
  printf 'REPORT_TRANSPORTS=%s\n' "$report_transports"
  printf 'LINUX_COMMIT=%s\n' "$linux_commit"
  printf 'DIRECT_KERNEL_PATCH_SHA256=%s\n' "$direct_patch_sha"
  printf 'PERSISTENT_DTB_PATCH_SHA256=%s\n' "$persistent_dtb_patch_sha"
  printf 'COPY_DTB_PATCH_SHA256=%s\n' "$copy_dtb_patch_sha"
  printf 'EMBEDDED_DTB_LIFETIME=%s\n' "$embedded_dtb_lifetime"
  printf 'TOKAY_DTB_SHA256=%s\n' "$tokay_dtb_sha"
  printf 'INITRAMFS_SHA256=%s\n' "$initramfs_sha"
  printf 'INITRAMFS_KERNEL_COMPRESSION=%s\n' "$initramfs_kernel_compression"
  printf 'EMBEDDED_INITRAMFS_SHA256=%s\n' "$embedded_initramfs_sha"
  printf 'OBSERVED_BUILD_ID=%s\n' "$PIXEL9_OBSERVED_BUILD_ID"
  printf 'FACTORY_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'MKBOOTIMG_COMMIT=%s\n' "$PIXEL9_MKBOOTIMG_COMMIT"
  printf 'LZ4_BUILD_ARCH=%s\n' "$build_arch"
  printf 'LZ4_NEVRA=%s\n' "$lz4_nevra"
  printf 'LZ4_BINARY_SHA256=%s\n' "$lz4_sha"
  printf 'LZ4_FORMAT=legacy\n'
  printf 'LZ4_LEVEL=12\n'
  printf 'LZ4_THREADS=1\n'
  printf 'HEADER_VERSION=4\n'
  printf 'ANDROID_CMDLINE_EMPTY=true\n'
  printf 'ANDROID_OS_VERSION_EMPTY=true\n'
  printf 'ANDROID_OS_PATCH_LEVEL_EMPTY=true\n'
  printf 'ANDROID_BOOT_RAMDISK_EMPTY=true\n'
  printf 'GKI_BOOT_SIGNATURE_INCLUDED=false\n'
  printf 'ENTRY_CONTRACT=linux-arm64-pe32plus-efi-application\n'
  printf 'ENTRY_CONTRACT_MATCHES_STOCK=true\n'
  printf 'PE_COFF_HEADER_PRESENT=true\n'
  printf 'PE_MACHINE=0xaa64\n'
  printf 'PE_SUBSYSTEM=10\n'
  printf 'TOKAY_DTB_LINKED_IN_KERNEL=true\n'
  printf 'INITRAMFS_LINKED_IN_KERNEL=true\n'
  printf 'KERNEL_BYTES=%s\n' "$kernel_bytes"
  printf 'KERNEL_SHA256=%s\n' "$kernel_sha"
  printf 'COMPRESSED_KERNEL_BYTES=%s\n' "$(stat -c %s "$compressed")"
  printf 'COMPRESSED_KERNEL_SHA256=%s\n' "$(sha256sum "$compressed" | awk '{print $1}')"
  printf 'WRAPPER_BYTES=%s\n' "$(stat -c %s "$candidate")"
  printf 'WRAPPER_SHA256=%s\n' "$(sha256sum "$candidate" | awk '{print $1}')"
  printf 'LZ4_ROUNDTRIP_IDENTICAL=true\n'
  printf 'ROUNDTRIP_KERNEL_IDENTICAL=true\n'
  printf 'ROUNDTRIP_RAMDISK_IDENTICAL=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOTLOADER_UNLOCKED=false\n'
  printf 'AVB_SIGNED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 offline direct-kernel Android v4 wrapper: %s\n' "$candidate"
