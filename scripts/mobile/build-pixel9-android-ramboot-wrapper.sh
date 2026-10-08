#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Wrap one digest-bound read-only U-Boot binary and its exact FIT as an Android
# v4 downloaded-boot image, then round-trip it with pinned AOSP tools. This is
# offline assembly only: it does not contact, unlock, boot, or flash a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

source_root=${1:?usage: build-pixel9-android-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
stock_audit=${2:?usage: build-pixel9-android-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
ramboot_proof=${3:?usage: build-pixel9-android-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
output_dir=${4:?usage: build-pixel9-android-ramboot-wrapper.sh SOURCE_ROOT STOCK_AUDIT RAMBOOT_PROOF OUTPUT_DIR}
tools_dir=$source_root/aosp-mkbootimg
mkbootimg=$tools_dir/mkbootimg.py
unpack_bootimg=$stock_audit/tools/unpack_bootimg.py
stock_manifest=$stock_audit/manifest.env
stock_info=$stock_audit/metadata/boot.info.txt
ramboot_manifest=$ramboot_proof/manifest.env
uboot=$ramboot_proof/artifacts/u-boot.bin
fit=$ramboot_proof/artifacts/payload.fit
candidate=$output_dir/pixel9-read-only-ramboot.img
roundtrip=$output_dir/roundtrip
entry_inspector=$repo_root/scripts/mobile/inspect-pixel9-kernel-entry.py
entry_report=$(mktemp)
trap 'rm -f -- "$entry_report"' EXIT

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || \
  die 'the Pixel 9 Android wrapper must be assembled off-device on Linux/aarch64'
if [ -r /sys/firmware/devicetree/base/model ]; then
  build_device=$(tr -d '\000' </sys/firmware/devicetree/base/model)
  case "$build_device" in
    *Pixel*|*tokay*|*Tokay*) die 'refusing to assemble a wrapper on a Pixel target' ;;
  esac
fi
for tool in cmp grep python3 sha256sum stat; do
  command -v "$tool" >/dev/null 2>&1 || die "missing wrapper tool: $tool"
done

for required in "$mkbootimg" "$tools_dir/gki/generate_gki_certificate.py" \
  "$unpack_bootimg" "$stock_manifest" "$stock_info" "$ramboot_manifest" \
  "$uboot" "$fit" "$entry_inspector"; do
  [ -f "$required" ] || die "wrapper input is absent: $required"
done

"$entry_inspector" --require-stock-contract "$uboot" >"$entry_report" ||
  die 'loader does not satisfy the stock Tokay PE/COFF EFI entry contract'
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
    die "loader entry proof lacks: $entry_boundary"
done
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

[ "$(sha256sum "$mkbootimg" | awk '{print $1}')" = "$PIXEL9_MKBOOTIMG_SHA256" ] || \
  die 'mkbootimg.py differs from the pin'
[ "$(sha256sum "$tools_dir/gki/generate_gki_certificate.py" | awk '{print $1}')" = \
  "$PIXEL9_GKI_CERTIFICATE_TOOL_SHA256" ] || die 'GKI certificate helper differs from the pin'
[ "$(sha256sum "$unpack_bootimg" | awk '{print $1}')" = \
  "$PIXEL9_UNPACK_BOOTIMG_SHA256" ] || die 'unpack_bootimg.py differs from the pin'
grep -Fqx "FACTORY_SHA256=$PIXEL9_FACTORY_IMAGE_SHA256" "$stock_manifest" || \
  die 'stock audit does not match the exact recovery factory image'
grep -Fqx "MKBOOTIMG_COMMIT=$PIXEL9_MKBOOTIMG_COMMIT" "$stock_manifest" || \
  die 'stock audit does not match the AOSP tool revision'
grep -Fqx 'boot image header version: 4' "$stock_info" || \
  die 'stock Tokay boot image is not Android header v4'
grep -Fqx 'ramdisk size: 0' "$stock_info" || die 'stock Tokay boot ramdisk boundary changed'
grep -Fqx 'command line args: ' "$stock_info" || die 'stock Tokay boot cmdline boundary changed'
grep -Fqx 'boot.img signature size: 0' "$stock_info" || \
  die 'stock Tokay GKI boot-signature boundary changed'

fit_sha=$(sha256sum "$fit" | awk '{print $1}')
uboot_sha=$(sha256sum "$uboot" | awk '{print $1}')
grep -Fqx "FIT_SHA256=$fit_sha" "$ramboot_manifest" || \
  die 'ramboot manifest does not bind the packaged FIT'
grep -Fqx "UBOOT_BIN_SHA256=$uboot_sha" "$ramboot_manifest" || \
  die 'ramboot manifest does not bind the packaged U-Boot binary'
grep -Fqx 'PERSISTENT_STORAGE_INITIALIZED=false' "$ramboot_manifest" || \
  die 'ramboot proof lacks its storage-disabled boundary'
grep -Fqx 'USB_UPDATE_INTERFACE_COMPILED=false' "$ramboot_manifest" || \
  die 'ramboot proof lacks its update-disabled boundary'
grep -Fqx 'INTERACTIVE_INPUT_COMPILED=false' "$ramboot_manifest" || \
  die 'ramboot proof lacks its no-input boundary'
grep -Fqx 'PHONE_ACCESSED=false' "$ramboot_manifest" || \
  die 'ramboot proof lacks its no-phone boundary'

mkdir -p "$output_dir" "$roundtrip"
install -m 0644 "$entry_report" "$output_dir/uboot-entry-contract.env"
PYTHONPATH="$tools_dir" python3 "$mkbootimg" \
  --header_version 4 \
  --kernel "$uboot" \
  --ramdisk "$fit" \
  --output "$candidate"

python3 "$unpack_bootimg" --boot_img "$candidate" --out "$roundtrip" \
  --format=info >"$output_dir/wrapper.info.txt"
grep -Fqx 'boot image header version: 4' "$output_dir/wrapper.info.txt" || \
  die 'round-tripped wrapper is not header v4'
grep -Fqx "kernel_size: $(stat -c %s "$uboot")" "$output_dir/wrapper.info.txt" || \
  die 'round-tripped U-Boot size differs'
grep -Fqx "ramdisk size: $(stat -c %s "$fit")" "$output_dir/wrapper.info.txt" || \
  die 'round-tripped FIT size differs'
grep -Fqx 'command line args: ' "$output_dir/wrapper.info.txt" || \
  die 'wrapper unexpectedly adds an Android cmdline'
grep -Fqx 'boot.img signature size: 0' "$output_dir/wrapper.info.txt" || \
  die 'wrapper unexpectedly adds a GKI signature'
cmp -s "$uboot" "$roundtrip/kernel" || die 'round-tripped U-Boot bytes differ'
cmp -s "$fit" "$roundtrip/ramdisk" || die 'round-tripped FIT bytes differ'

{
  printf 'LUMA_PIXEL9_ANDROID_RAMBOOT_WRAPPER_VERSION=1\n'
  printf 'SCOPE=offline-v4-downloaded-boot-wrapper\n'
  printf 'OBSERVED_BUILD_ID=%s\n' "$PIXEL9_OBSERVED_BUILD_ID"
  printf 'FACTORY_SHA256=%s\n' "$PIXEL9_FACTORY_IMAGE_SHA256"
  printf 'MKBOOTIMG_COMMIT=%s\n' "$PIXEL9_MKBOOTIMG_COMMIT"
  printf 'HEADER_VERSION=4\n'
  printf 'ANDROID_CMDLINE_EMPTY=true\n'
  printf 'GKI_BOOT_SIGNATURE_INCLUDED=false\n'
  printf 'ENTRY_CONTRACT=linux-arm64-pe32plus-efi-application\n'
  printf 'ENTRY_CONTRACT_MATCHES_STOCK=true\n'
  printf 'PE_COFF_HEADER_PRESENT=true\n'
  printf 'PE_MACHINE=0xaa64\n'
  printf 'PE_SUBSYSTEM=10\n'
  printf 'UBOOT_BYTES=%s\n' "$(stat -c %s "$uboot")"
  printf 'UBOOT_SHA256=%s\n' "$uboot_sha"
  printf 'FIT_BYTES=%s\n' "$(stat -c %s "$fit")"
  printf 'FIT_SHA256=%s\n' "$fit_sha"
  printf 'WRAPPER_BYTES=%s\n' "$(stat -c %s "$candidate")"
  printf 'WRAPPER_SHA256=%s\n' "$(sha256sum "$candidate" | awk '{print $1}')"
  printf 'ROUNDTRIP_KERNEL_IDENTICAL=true\n'
  printf 'ROUNDTRIP_RAMDISK_IDENTICAL=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'BOOTLOADER_UNLOCKED=false\n'
  printf 'AVB_SIGNED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 offline Android v4 ramboot wrapper: %s\n' "$candidate"
