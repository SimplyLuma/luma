#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compress the embedded-FIT U-Boot ARM64 Image in the same legacy LZ4 shape as
# Tokay's stock kernel, place only that kernel in an Android v4 boot wrapper,
# and round-trip every layer. This is offline assembly: it never contacts,
# unlocks, boots, or flashes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-embedded-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
stock_audit=${2:?usage: build-pixel9-embedded-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
ramboot_proof=${3:?usage: build-pixel9-embedded-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
output_dir=${4:?usage: build-pixel9-embedded-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
tools_dir=$source_root/aosp-mkbootimg
mkbootimg=$tools_dir/mkbootimg.py
unpack_bootimg=$stock_audit/tools/unpack_bootimg.py
stock_manifest=$stock_audit/manifest.env
stock_info=$stock_audit/metadata/boot.info.txt
ramboot_manifest=$ramboot_proof/manifest.env
uboot=$ramboot_proof/artifacts/u-boot.bin
compressed=$output_dir/u-boot-embedded-fit.Image.lz4
decompressed=$output_dir/u-boot-embedded-fit.roundtrip.bin
empty_ramdisk=$output_dir/empty-ramdisk
candidate=$output_dir/pixel9-read-only-ramboot.img
roundtrip=$output_dir/roundtrip
lz4_bin=/usr/bin/lz4
entry_inspector=$repo_root/scripts/mobile/inspect-pixel9-kernel-entry.py
entry_report=$(mktemp)
trap 'rm -f -- "$entry_report"' EXIT

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'the Pixel 9 embedded wrapper must be assembled off-device on Linux/aarch64'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to assemble a wrapper on a Pixel target' ;;
  esac
fi
for tool in cmp file grep install python3 rpm sha256sum stat; do
  command -v "$tool" >/dev/null 2>&1 || die "missing wrapper tool: $tool"
done
[ -x "$lz4_bin" ] || die 'pinned LZ4 executable is absent'

for required in "$mkbootimg" "$tools_dir/gki/generate_gki_certificate.py" \
  "$unpack_bootimg" "$stock_manifest" "$stock_info" "$ramboot_manifest" "$uboot" \
  "$entry_inspector"; do
  [ -f "$required" ] || die "wrapper input is absent: $required"
done

"$entry_inspector" --require-stock-contract "$uboot" >"$entry_report" ||
  die 'embedded loader does not satisfy the stock Tokay PE/COFF EFI entry contract'
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
  grep -Fqx "$entry_boundary" "$entry_report" || \
    die "embedded loader entry proof lacks: $entry_boundary"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(sha256sum "$mkbootimg" | awk '{print $1}')" = "$PIXEL9_MKBOOTIMG_SHA256" ] || \
  die 'mkbootimg.py differs from the pin'
[ "$(sha256sum "$tools_dir/gki/generate_gki_certificate.py" | awk '{print $1}')" = \
  "$PIXEL9_GKI_CERTIFICATE_TOOL_SHA256" ] || die 'GKI certificate helper differs from the pin'
[ "$(sha256sum "$unpack_bootimg" | awk '{print $1}')" = \
  "$PIXEL9_UNPACK_BOOTIMG_SHA256" ] || die 'unpack_bootimg.py differs from the pin'
[ "$(rpm -q lz4)" = "$PIXEL9_LZ4_NEVRA" ] || die 'LZ4 package identity differs from the pin'
[ "$(sha256sum "$lz4_bin" | awk '{print $1}')" = "$PIXEL9_LZ4_BINARY_SHA256" ] || \
  die 'LZ4 executable differs from the pin'

grep -Fqx "FACTORY_SHA256=$PIXEL9_FACTORY_IMAGE_SHA256" "$stock_manifest" || \
  die 'stock audit does not match the exact recovery factory image'
grep -Fqx "MKBOOTIMG_COMMIT=$PIXEL9_MKBOOTIMG_COMMIT" "$stock_manifest" || \
  die 'stock audit does not match the AOSP tool revision'
for stock_boundary in \
  'boot image header version: 4' \
  'ramdisk size: 0' \
  'os version: None' \
  'os patch level: None' \
  'command line args: ' \
  'boot.img signature size: 0'; do
  grep -Fqx "$stock_boundary" "$stock_info" || \
    die "stock Tokay boot boundary changed: $stock_boundary"
done

uboot_sha=$(sha256sum "$uboot" | awk '{print $1}')
grep -Fqx 'LUMA_PIXEL9_UBOOT_RAMBOOT_VERSION=3' "$ramboot_manifest" || \
  die 'ramboot proof is not the embedded-FIT loader revision'
grep -Fqx "UBOOT_BIN_SHA256=$uboot_sha" "$ramboot_manifest" || \
  die 'ramboot manifest does not bind the packaged U-Boot binary'
for loader_boundary in \
  'FIT_SOURCE=embedded-rodata' \
  'FIT_EMBEDDED_ONCE=true' \
  'ANDROID_BOOT_RAMDISK_REQUIRED=false' \
  'PERSISTENT_STORAGE_INITIALIZED=false' \
  'USB_UPDATE_INTERFACE_COMPILED=false' \
  'INTERACTIVE_INPUT_COMPILED=false' \
  'PHONE_ACCESSED=false'; do
  grep -Fqx "$loader_boundary" "$ramboot_manifest" || \
    die "embedded loader proof lacks: $loader_boundary"
done

python3 - "$uboot" <<'PY'
import pathlib
import struct
import sys

image = pathlib.Path(sys.argv[1]).read_bytes()
if len(image) < 64 or image[56:60] != b'ARMd':
    raise SystemExit('U-Boot binary is not an ARM64 Image payload')
declared = struct.unpack_from('<Q', image, 16)[0]
if declared < len(image):
    raise SystemExit('ARM64 Image header understates the linked payload size')
PY

mkdir -p "$output_dir" "$roundtrip"
install -m 0644 "$entry_report" "$output_dir/uboot-entry-contract.env"
"$lz4_bin" -q -f -l -12 -T1 "$uboot" "$compressed"
python3 - "$compressed" <<'PY'
import pathlib
import sys

payload = pathlib.Path(sys.argv[1]).read_bytes()
if payload[:4] != b'\x02\x21\x4c\x18':
    raise SystemExit('kernel payload is not a legacy LZ4 stream')
PY
"$lz4_bin" -q -f -d "$compressed" "$decompressed"
cmp -s "$uboot" "$decompressed" || die 'LZ4 decompression round trip differs'
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
  grep -Fqx "$wrapper_boundary" "$output_dir/wrapper.info.txt" || \
    die "wrapper boundary differs from stock: $wrapper_boundary"
done
grep -Fqx "kernel_size: $(stat -c %s "$compressed")" "$output_dir/wrapper.info.txt" || \
  die 'round-tripped compressed-kernel size differs'
cmp -s "$compressed" "$roundtrip/kernel" || \
  die 'round-tripped compressed-kernel bytes differ'
cmp -s "$empty_ramdisk" "$roundtrip/ramdisk" || \
  die 'round-tripped Android ramdisk is not empty'
[ "$(stat -c %s "$candidate")" -le "$PIXEL9_STOCK_BOOT_IMAGE_BYTES" ] || \
  die 'kernel-only wrapper exceeds the stock boot partition image size'

{
  printf 'LUMA_PIXEL9_ANDROID_RAMBOOT_WRAPPER_VERSION=3\n'
  printf 'SCOPE=offline-v4-kernel-only-embedded-fit-wrapper\n'
  printf 'OBSERVED_BUILD_ID=%s\n' "$PIXEL9_OBSERVED_BUILD_ID"
  printf 'FACTORY_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'MKBOOTIMG_COMMIT=%s\n' "$PIXEL9_MKBOOTIMG_COMMIT"
  printf 'LZ4_NEVRA=%s\n' "$PIXEL9_LZ4_NEVRA"
  printf 'LZ4_BINARY_SHA256=%s\n' "$PIXEL9_LZ4_BINARY_SHA256"
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
  printf 'FIT_LINKED_IN_KERNEL=true\n'
  printf 'UBOOT_BYTES=%s\n' "$(stat -c %s "$uboot")"
  printf 'UBOOT_SHA256=%s\n' "$uboot_sha"
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

printf 'Pixel 9 offline embedded-FIT Android v4 wrapper: %s\n' "$candidate"
