#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Stream only the boot-critical images from an exact verified Google factory
# archive and decode them offline with pinned official AOSP tools. This does not
# execute flash scripts, assemble an image, sign data, or contact a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/pixel9-physical/inputs.env"

factory_zip=${1:?usage: analyze-pixel9-stock-boot.sh FACTORY_ZIP OUTPUT_DIR}
output_dir=${2:?usage: analyze-pixel9-stock-boot.sh FACTORY_ZIP OUTPUT_DIR}
images_dir=$output_dir/images
tools_dir=$output_dir/tools
unpacked_dir=$output_dir/unpacked
metadata_dir=$output_dir/metadata
entry_inspector=$repo_root/scripts/mobile/inspect-pixel9-kernel-entry.py

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in base64 bsdtar cpio curl fdtget file grep lz4 mkdir python3 sha256sum unzip wc; do
  command -v "$tool" >/dev/null 2>&1 || die "missing stock-image analysis tool: $tool"
done
[ -x "$entry_inspector" ] || die "missing kernel entry inspector: $entry_inspector"
[ -f "$factory_zip" ] || die "factory archive not found: $factory_zip"
[ ! -e "$output_dir" ] || die "refusing to replace existing output: $output_dir"

factory_bytes=$(wc -c <"$factory_zip" | tr -d '[:space:]')
[ "$factory_bytes" = "$PIXEL9_FACTORY_IMAGE_BYTES" ] ||
  die "factory archive byte size differs from the pin"
factory_sha=$(sha256sum "$factory_zip" | awk '{print $1}')
[ "$factory_sha" = "$PIXEL9_FACTORY_IMAGE_SHA256" ] ||
  die "factory archive SHA-256 differs from the pin"

nested_image=$(unzip -Z1 "$factory_zip" | grep -E '/image-[^/]+\.zip$')
[ -n "$nested_image" ] || die 'factory archive has no nested image archive'
case "$nested_image" in
  *$'\n'*) die 'factory archive contains multiple nested image archives' ;;
esac

mkdir -p "$images_dir" "$tools_dir" "$unpacked_dir" "$metadata_dir"
wanted=(
  android-info.txt
  fastboot-info.txt
  boot.img
  init_boot.img
  vendor_boot.img
  vendor_kernel_boot.img
  dtbo.img
  vbmeta.img
  vbmeta_vendor.img
  vbmeta_system.img
)
unzip -p "$factory_zip" "$nested_image" |
  bsdtar -xf - -C "$images_dir" "${wanted[@]}"
for name in "${wanted[@]}"; do
  [ -s "$images_dir/$name" ] || die "nested archive is missing: $name"
done

fetch_gitiles_script() {
  local url=$1
  local commit=$2
  local path=$3
  local expected_sha=$4
  local destination=$5

  curl -fsSL "$url/+/$commit/$path?format=TEXT" |
    python3 -c 'import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))' \
      >"$destination"
  [ "$(sha256sum "$destination" | awk '{print $1}')" = "$expected_sha" ] ||
    die "AOSP tool checksum differs: $path"
  chmod 0644 "$destination"
}

unpack_bootimg=$tools_dir/unpack_bootimg.py
avbtool=$tools_dir/avbtool.py
fetch_gitiles_script "$PIXEL9_MKBOOTIMG_URL" "$PIXEL9_MKBOOTIMG_COMMIT" \
  unpack_bootimg.py "$PIXEL9_UNPACK_BOOTIMG_SHA256" "$unpack_bootimg"
fetch_gitiles_script "$PIXEL9_AVB_URL" "$PIXEL9_AVB_COMMIT" \
  avbtool.py "$PIXEL9_AVBTOOL_SHA256" "$avbtool"

for base in boot init_boot vendor_boot vendor_kernel_boot; do
  mkdir -p "$unpacked_dir/$base"
  python3 "$unpack_bootimg" --boot_img "$images_dir/$base.img" \
    --out "$unpacked_dir/$base" --format=info >"$metadata_dir/$base.info.txt"
done

# Decode but never execute the compressed stock kernel and ramdisks. File-list
# inventories are sufficient to establish ownership and module boundaries.
lz4 -q -d -c "$unpacked_dir/boot/kernel" >"$unpacked_dir/boot/Image"
"$entry_inspector" --require-stock-contract "$unpacked_dir/boot/Image" \
  >"$metadata_dir/boot-kernel-entry.env"
{
  printf 'BYTES=%s\n' "$(wc -c <"$unpacked_dir/boot/Image" | tr -d '[:space:]')"
  printf 'SHA256=%s\n' "$(sha256sum "$unpacked_dir/boot/Image" | awk '{print $1}')"
  printf 'TYPE=%s\n' "$(file -b "$unpacked_dir/boot/Image")"
} >"$metadata_dir/boot-kernel-uncompressed.summary.env"

ramdisks=(
  init_boot/ramdisk
  vendor_boot/vendor_ramdisk00
  vendor_boot/vendor_ramdisk01
  vendor_kernel_boot/vendor_ramdisk00
)
for relative in "${ramdisks[@]}"; do
  inventory_name=${relative//\//-}
  lz4 -q -d -c "$unpacked_dir/$relative" |
    cpio -it >"$metadata_dir/$inventory_name.files.txt" \
      2>"$metadata_dir/$inventory_name.cpio.txt"
done

# vendor_kernel_boot contains a concatenation of stock DTBs. Split it by each
# validated FDT header, then record identity strings without decompiling or
# altering the blobs.
dtb_dir=$unpacked_dir/vendor_kernel_boot/dtbs
mkdir -p "$dtb_dir"
python3 - "$unpacked_dir/vendor_kernel_boot/dtb" "$dtb_dir" \
  >"$metadata_dir/vendor-kernel-boot-dtbs.layout.txt" <<'PY'
import pathlib
import struct
import sys

source = pathlib.Path(sys.argv[1]).read_bytes()
destination = pathlib.Path(sys.argv[2])
magic = b'\xd0\x0d\xfe\xed'
offset = 0
index = 0
while offset < len(source):
    if source[offset:offset + 4] != magic:
        raise SystemExit(f'invalid concatenated FDT magic at offset {offset}')
    total_size = struct.unpack_from('>I', source, offset + 4)[0]
    if total_size < 40 or offset + total_size > len(source):
        raise SystemExit(f'invalid FDT size at offset {offset}: {total_size}')
    blob = source[offset:offset + total_size]
    output = destination / f'dtb-{index:02d}.dtb'
    output.write_bytes(blob)
    print(f'{index:02d} {offset} {total_size} {output.name}')
    offset += total_size
    index += 1
if offset != len(source):
    raise SystemExit(f'unparsed FDT tail: {len(source) - offset} bytes')
PY

: >"$metadata_dir/vendor-kernel-boot-dtbs.identity.txt"
for dtb in "$dtb_dir"/*.dtb; do
  model=$(fdtget -t s "$dtb" / model 2>/dev/null || printf '<absent>')
  compatible=$(fdtget -t s "$dtb" / compatible 2>/dev/null || printf '<absent>')
  printf '%s\tmodel=%s\tcompatible=%s\n' \
    "$(basename -- "$dtb")" "$model" "$compatible" \
    >>"$metadata_dir/vendor-kernel-boot-dtbs.identity.txt"
done

for image in "$images_dir"/*.img; do
  name=$(basename -- "$image")
  {
    printf 'FILE=%s\n' "$name"
    printf 'BYTES=%s\n' "$(wc -c <"$image" | tr -d '[:space:]')"
    printf 'SHA256=%s\n' "$(sha256sum "$image" | awk '{print $1}')"
    printf 'TYPE=%s\n' "$(file -b "$image")"
    printf 'FIRST_16_BYTES='
    python3 - "$image" <<'PY'
import pathlib
import sys
print(pathlib.Path(sys.argv[1]).read_bytes()[:16].hex())
PY
  } >"$metadata_dir/$name.summary.env"

  if python3 "$avbtool" info_image --image "$image" \
      >"$metadata_dir/$name.avb.txt" 2>&1; then
    printf 'AVB_INFO_AVAILABLE=true\n' >>"$metadata_dir/$name.summary.env"
  else
    printf 'AVB_INFO_AVAILABLE=false\n' >>"$metadata_dir/$name.summary.env"
  fi
done

{
  printf 'LUMA_PIXEL9_STOCK_BOOT_AUDIT_VERSION=2\n'
  printf 'SCOPE=offline-stock-layout-read-only\n'
  printf 'FACTORY_FILENAME=%s\n' "$(basename -- "$factory_zip")"
  printf 'FACTORY_BYTES=%s\n' "$factory_bytes"
  printf 'FACTORY_SHA256=%s\n' "$factory_sha"
  printf 'NESTED_IMAGE=%s\n' "$nested_image"
  printf 'MKBOOTIMG_COMMIT=%s\n' "$PIXEL9_MKBOOTIMG_COMMIT"
  printf 'UNPACK_BOOTIMG_SHA256=%s\n' "$PIXEL9_UNPACK_BOOTIMG_SHA256"
  printf 'AVB_COMMIT=%s\n' "$PIXEL9_AVB_COMMIT"
  printf 'AVBTOOL_SHA256=%s\n' "$PIXEL9_AVBTOOL_SHA256"
  printf 'STOCK_KERNEL_ENTRY_CONTRACT=linux-arm64-pe32plus-efi-application\n'
  printf 'STOCK_KERNEL_ENTRY_CONTRACT_VERIFIED=true\n'
  printf 'FACTORY_FLASH_SCRIPTS_EXECUTED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'ANDROID_BOOT_IMAGE_ASSEMBLED=false\n'
  printf 'AVB_SIGNED=false\n'
  printf 'BOOT_AUTHORIZED=false\n'
  printf 'FLASH_AUTHORIZED=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

printf 'Pixel 9 offline stock boot audit: %s\n' "$output_dir"
