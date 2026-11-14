#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the barbet boot image: the kernel and DTB from scripts/kernel/build-barbet.sh,
# the FP6 Alpha 1 ramdisk as the base, and a barbet initramfs overlay with the
# shared OSTree resolver and the device platform. Android boot v2, 4096-byte
# pages, AVB hash footer NONE and the boot_a policy. Never flashes.
#
# usage: build-barbet-boot-image.sh --kernel FILE --dtb FILE --alpha-boot FILE
#                                   --output FILE
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
initramfs="$repo_root/config/mobile/barbet-initramfs"
manifest="$repo_root/config/mobile/barbet-initramfs.manifest"
shared="$repo_root/scripts/mobile/common/initramfs"
fail() { printf 'build-barbet-boot-image: %s\n' "$*" >&2; exit 1; }
usage() { sed -n 's/^# usage: /usage: /p' "$0" >&2; exit 2; }

kernel="" dtb="" alpha="" output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --kernel) kernel=$(realpath -e "${2:?}"); shift 2 ;;
    --dtb) dtb=$(realpath -e "${2:?}"); shift 2 ;;
    --alpha-boot) alpha=$(realpath -e "${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$kernel" ] && [ -n "$dtb" ] && [ -n "$alpha" ] && [ -n "$output" ] || usage
for f in "$shared/luma-ostree-boot.sh" "$shared/hooks-extra/50-luma-ostree-default.sh" \
         "$initramfs/init_functions.sh" "$initramfs/init_2nd.sh" "$manifest"; do
  [ -f "$f" ] || fail "missing $f"
done

root_uuid=03797c05-cf87-4eda-adc4-6ce111baf2d3
boot_uuid=032D-57AD
firmware_store=/var/lib/luma/device-firmware-barbet
cmdline="earlycon=qcom_geni,0x888000 keep_bootcon console=ttyMSM0,115200n8 console=tty0 ignore_loglevel loglevel=8 panic=15 rdinit=/init pmos_rootfsopts=defaults rootwait ro init=/init pmos_root_uuid=$root_uuid pmos_boot_uuid=$boot_uuid ostree=/ostree/root.a firmware_class.path=$firmware_store"

mkbootimg=${MKBOOTIMG:-mkbootimg}
unpack=${UNPACK_BOOTIMG:-unpack_bootimg}
avbtool=${AVBTOOL:-avbtool}
lz4=${LZ4:-lz4}
partition_size=100663296
rollback_index=1783209600

work=$(mktemp -d "${TMPDIR:-/tmp}/barbet-boot.XXXXXX")
trap 'rm -rf "$work"' EXIT
umask 022

python3 - "$kernel" "$work/kernel" <<'PY'
import struct, sys
src, dst = sys.argv[1], sys.argv[2]
data = bytearray(open(src, "rb").read())
text_offset, image_size = struct.unpack_from("<QQ", data, 8)
assert text_offset == 0, f"expected text_offset 0, got 0x{text_offset:x}"
assert image_size >= len(data), f"image_size 0x{image_size:x} < file {len(data)}"
struct.pack_into("<Q", data, 8, 0x80000)
open(dst, "wb").write(data)
print(f"kernel text_offset 0 -> 0x80000, image_size 0x{image_size:x}, {len(data)} bytes")
PY

"$lz4" -9 -c "$work/kernel" >"$work/kernel.lz4"
python3 -c "d=open('$work/kernel.lz4','rb').read(4); assert d==bytes.fromhex('04224d18'), d"

"$unpack" --boot_img "$alpha" --out "$work/alpha" --format mkbootimg >"$work/alpha.args"

o="$work/overlay"
install -D -m 0644 "$initramfs/init_functions.sh" "$o/init_functions.sh"
install -D -m 0755 "$initramfs/init_2nd.sh" "$o/init_2nd.sh"
install -D -m 0644 "$shared/luma-ostree-boot.sh" "$o/usr/lib/luma/ostree-boot.sh"
install -D -m 0755 "$shared/hooks-extra/50-luma-ostree-default.sh" "$o/hooks-extra/50-luma-ostree-default.sh"
cp -a "$initramfs/luma-barbet" "$o/luma-barbet"
cp -a "$initramfs/hooks-cleanup" "$o/hooks-cleanup"
find "$o" -type d -exec chmod 00755 {} +
find "$o" -exec touch -h -d @0 {} +
(cd "$o" && find . -mindepth 1 | LC_ALL=C sort | cpio --quiet --reproducible -o -H newc -R 0:0) | gzip -9n >"$work/overlay.cpio.gz"
cat "$work/alpha/ramdisk" "$work/overlay.cpio.gz" >"$work/ramdisk"

"$mkbootimg" --header_version 2 --pagesize 4096 --base 0 \
  --kernel_offset 0x00008000 --ramdisk_offset 0x10000000 --second_offset 0 \
  --tags_offset 0x00000100 --dtb_offset 0x18000000 \
  --kernel "$work/kernel.lz4" --ramdisk "$work/ramdisk" --dtb "$dtb" \
  --cmdline "$cmdline" --output "$work/boot.img"

[ "$(stat -c %s "$work/boot.img")" -le "$partition_size" ] ||
  fail "boot image exceeds the 96 MiB boot partition"
salt=$(sha256sum "$work/boot.img" | cut -d' ' -f1)
"$avbtool" add_hash_footer --image "$work/boot.img" \
  --partition_name boot --partition_size "$partition_size" \
  --algorithm NONE --hash_algorithm sha256 \
  --rollback_index "$rollback_index" --salt "$salt"
"$avbtool" verify_image --image "$work/boot.img" >/dev/null
[ "$(stat -c %s "$work/boot.img")" = "$partition_size" ] ||
  fail "AVB footer did not pad to the partition size"

install -D -m 0644 "$work/boot.img" "$output"
printf 'CMDLINE=%s\nBOOT=%s\nSHA256=%s\nOVERLAY_SHA256=%s\n' "$cmdline" "$output" \
  "$(sha256sum "$output" | cut -d' ' -f1)" "$(sha256sum "$work/overlay.cpio.gz" | cut -d' ' -f1)"
