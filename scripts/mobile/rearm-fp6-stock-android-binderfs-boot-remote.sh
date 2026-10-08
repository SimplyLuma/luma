#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Re-arm the already accepted, already installed BinderFS EFI payload for a
# bounded three-attempt boot. The proven pmos payload and entry are verified
# and left untouched. This script does not reboot the phone.

set -Eeuo pipefail
umask 077

release=7.1.2-luma-fp-ims1
candidate_efi=/boot/linux-binderfs-ims1.efi
candidate_image=/boot/vmlinuz-binderfs-ims1
candidate_efi_sha=8a97eed2c3a4f9d125f49a8d2c98f267900f805bd046215fe585edbb2c8c12b8
candidate_image_sha=d1476dbea625aeb22d5d2a27df3a928cd9a2a13bd72a80bfda8507d376df0153
proven_efi_sha=a69dbc085727ea4244cc0a3fce93553913e37dc6b48896c9969f7392f3346cb9
proven_image_sha=62556c8a87a0b8c345fdfd11770bffad7b112526e17cdb353600bec8648583ef
proven_initramfs_sha=3ed23188d9fb5ae8ed90b8d3b274e8d4d4432c3f60a25b14d718c33d9391a8a4
proven_dtb_sha=9a9d45187ef98874d84e1df9d06044a89e9aedb98d0b79b2b6d8bc32e7a164cf
proven_entry=/boot/loader/entries/pmos.conf
proven_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446
candidate_entry=/boot/loader/entries/pmos-binderfs-ims1+3.conf

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
require_hash() {
  [[ -f $1 && ! -L $1 ]] || die "missing or linked file: $1"
  [[ $(hash "$1") == "$2" ]] || die "hash mismatch: $1"
}

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
[[ $(uname -r) == "$release" ]] || die 'unexpected running kernel release'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not on slot b'
require_hash "$candidate_efi" "$candidate_efi_sha"
require_hash "$candidate_image" "$candidate_image_sha"
require_hash /boot/linux.efi "$proven_efi_sha"
require_hash /boot/vmlinuz "$proven_image_sha"
require_hash /boot/initramfs "$proven_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$proven_dtb_sha"
require_hash "$proven_entry" "$proven_entry_sha"
grep -qx 'default pmos.conf' /boot/loader/loader.conf || die 'proven default is not active'
[[ $(find /boot/loader/entries -maxdepth 1 -type f -name 'pmos-binderfs-ims1*.conf' | wc -l) -eq 0 ]] ||
  die 'a BinderFS candidate entry already exists'

sed \
  -e 's/^title .*/title Luma FP6 BinderFS compatibility candidate (three-attempt gate)/' \
  -e 's#^linux .*#linux linux-binderfs-ims1.efi#' \
  "$proven_entry" >"$candidate_entry.new"
chmod 0700 "$candidate_entry.new"
grep -qx 'linux linux-binderfs-ims1.efi' "$candidate_entry.new" ||
  die 'candidate Linux field differs'
sync -f "$candidate_entry.new"
mv "$candidate_entry.new" "$candidate_entry"

install -m 0700 /dev/stdin /boot/loader/loader.conf.new <<'EOF'
default pmos-binderfs-ims1*
timeout 2
editor no
auto-entries no
auto-firmware no
EOF
sync -f "$candidate_entry"
sync -f /boot/loader/loader.conf.new
mv /boot/loader/loader.conf.new /boot/loader/loader.conf
sync -f /boot/loader

require_hash /boot/linux.efi "$proven_efi_sha"
require_hash /boot/vmlinuz "$proven_image_sha"
require_hash /boot/initramfs "$proven_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$proven_dtb_sha"
require_hash "$proven_entry" "$proven_entry_sha"

printf 'BINDERFS_BOOT_REARMED=true\n'
printf 'candidate_release=%s\n' "$release"
printf 'candidate_boot_attempts=3\n'
printf 'rollback_entry=pmos.conf\n'
printf 'reboot_performed=false\n'
