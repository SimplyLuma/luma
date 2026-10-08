#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

image_root=/var/tmp/luma-stock-qrel1695-images
system_mount=/var/tmp/luma-stock-qrel1695-system
vendor_mount=/var/tmp/luma-stock-qrel1695-vendor
system_ext_mount=/var/tmp/luma-stock-qrel1695-system_ext
product_mount=/var/tmp/luma-stock-qrel1695-product
runtime_files=/var/tmp/luma-stock-qrel1695-runtime-apex-files
runtime_mount=/var/tmp/luma-stock-qrel1695-runtime-apex
waydroid_mount=/var/tmp/luma-waydroid-system-ro
waydroid_image=/etc/waydroid-extra/images/system.img

fail() {
  printf 'event=fp6_stock_mount_restore_failed reason=%s\n' "$1" >&2
  exit 1
}

[[ $(id -u) -eq 0 ]] || fail not_root
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
case $(uname -r) in
  7.1.2-luma-fp-cma1|7.1.2-luma-fp-ims1) ;;
  *) fail kernel ;;
esac

mount_image_ro() {
  local image=$1 target=$2
  [[ -f $image && ! -L $image ]] || fail "image_$(basename "$image")"
  mkdir -p "$target"
  if mountpoint -q "$target"; then
    umount "$target"
  fi
  mount -o loop,ro,nosuid,nodev "$image" "$target"
  findmnt -rn -T "$target" -o OPTIONS | tr ',' '\n' | grep -qx ro || fail "mount_$(basename "$target")"
}

mount_image_ro "$image_root/system_a.img" "$system_mount"
mount_image_ro "$image_root/vendor_a.img" "$vendor_mount"
mount_image_ro "$image_root/system_ext_a.img" "$system_ext_mount"
mount_image_ro "$image_root/product_a.img" "$product_mount"

runtime_apex=$system_mount/system/apex/com.android.runtime.apex
[[ -f $runtime_apex && ! -L $runtime_apex ]] || fail runtime_apex
mkdir -p "$runtime_files" "$runtime_mount"
if mountpoint -q "$runtime_mount"; then
  umount "$runtime_mount"
fi
unzip -p "$runtime_apex" apex_payload.img >"$runtime_files/apex_payload.img"
mount -o loop,ro,nosuid,nodev "$runtime_files/apex_payload.img" "$runtime_mount"
findmnt -rn -T "$runtime_mount" -o OPTIONS | tr ',' '\n' | grep -qx ro || fail runtime_mount

[[ -f $waydroid_image && ! -L $waydroid_image ]] || fail waydroid_image
mkdir -p "$waydroid_mount"
if mountpoint -q "$waydroid_mount"; then
  umount "$waydroid_mount"
fi
mount -o loop,ro,nosuid,nodev "$waydroid_image" "$waydroid_mount"
findmnt -rn -T "$waydroid_mount" -o OPTIONS | tr ',' '\n' | grep -qx ro || fail waydroid_mount

printf 'event=fp6_stock_mount_restore_complete mode=read_only partitions=4 runtime_apex=1 waydroid=1\n'
