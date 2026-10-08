#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install the BinderFS-only FP6 kernel beside the proven ims1 boot, with a
# systemd-boot three-attempt gate. The existing kernel, initramfs, DTB, and
# rollback BLS entry remain byte-exact and immediately selectable.

set -euo pipefail
umask 077

stage=/var/tmp/luma-fp6-stock-android-container-kernel1
bundle=$stage/bundle
release=7.1.2-luma-fp-ims-container1
candidate_efi_sha=c87c25a24041a0541ac87aed3b346d9edb3673b2888e86243c0f2279f472fc8a
candidate_image_sha=b38f57c13532333f40d0318a20db94e2fff65e0d09e14184c987fa9196e6cb45
candidate_config_sha=7eb084e3a201a11e69676aad4c93b9588e72f15dc9ef86d5d5c56ee13fc0f448
candidate_modules_sha=2e8ec46457aa25e39888a89228bcc4d4aaab8bdae542c6f085d6200b8a5ea150
candidate_manifest_sha=306952c6b0f602394734a931b4c92915c3e215d8c348d67c53d5bdde59b3a4aa
current_release=7.1.2-luma-fp-ims1
current_efi_sha=a69dbc085727ea4244cc0a3fce93553913e37dc6b48896c9969f7392f3346cb9
current_image_sha=62556c8a87a0b8c345fdfd11770bffad7b112526e17cdb353600bec8648583ef
current_initramfs_sha=3ed23188d9fb5ae8ed90b8d3b274e8d4d4432c3f60a25b14d718c33d9391a8a4
current_dtb_sha=9a9d45187ef98874d84e1df9d06044a89e9aedb98d0b79b2b6d8bc32e7a164cf
current_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446
rollback_root=/var/lib/luma/fp6-stock-android-container-kernel/rollback-ims1
candidate_entry=/boot/loader/entries/pmos-container1+3.conf
accepted_entry=/boot/loader/entries/pmos-container1.conf

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
require_hash() {
  [[ -f $1 && ! -L $1 ]] || die "missing or linked file: $1"
  [[ $(hash "$1") == "$2" ]] || die "hash mismatch: $1"
}

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
model=$(tr -d '\0' </sys/firmware/devicetree/base/model)
[[ $model == 'The Fairphone (Gen. 6)' ]] || die 'device identity mismatch'
[[ $(uname -r) == "$current_release" ]] || die 'running kernel is not the proven ims1 release'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
[[ ! -e $candidate_entry && ! -e $accepted_entry ]] || die 'candidate BLS entry already exists'
[[ ! -e /boot/linux-container1.efi ]] || die 'candidate EFI path already exists'
[[ ! -e /boot/vmlinuz-container1 ]] || die 'candidate image path already exists'
[[ ! -e /lib/modules/$release ]] || die 'candidate module tree already exists'

require_hash "$bundle/linux.efi" "$candidate_efi_sha"
require_hash "$bundle/Image.gz" "$candidate_image_sha"
require_hash "$bundle/config" "$candidate_config_sha"
require_hash "$bundle/modules-$release.tar.zst" "$candidate_modules_sha"
require_hash "$bundle/manifest.env" "$candidate_manifest_sha"
require_hash /boot/linux.efi "$current_efi_sha"
require_hash /boot/vmlinuz "$current_image_sha"
require_hash /boot/initramfs "$current_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$current_dtb_sha"
require_hash /boot/loader/entries/pmos.conf "$current_entry_sha"
[[ -d /lib/modules/$current_release && ! -L /lib/modules/$current_release ]] ||
  die 'proven module tree absent'
grep -qx 'CONFIG_ANDROID_BINDERFS=y' "$bundle/config" || die 'BinderFS config gate failed'
grep -qx 'ANDROID_BINDERFS_BUILTIN=true' "$bundle/manifest.env" ||
  die 'candidate manifest lacks BinderFS declaration'

install -d -m 0700 "$rollback_root"
if [[ ! -e $rollback_root/linux.efi ]]; then
  install -m 0600 /boot/linux.efi "$rollback_root/linux.efi"
  install -m 0600 /boot/vmlinuz "$rollback_root/vmlinuz"
  install -m 0600 /boot/initramfs "$rollback_root/initramfs"
  install -m 0600 /boot/milos-fairphone-fp6.dtb "$rollback_root/milos-fairphone-fp6.dtb"
  install -m 0600 /boot/loader/entries/pmos.conf "$rollback_root/pmos.conf"
fi
require_hash "$rollback_root/linux.efi" "$current_efi_sha"
require_hash "$rollback_root/vmlinuz" "$current_image_sha"
require_hash "$rollback_root/initramfs" "$current_initramfs_sha"
require_hash "$rollback_root/milos-fairphone-fp6.dtb" "$current_dtb_sha"
require_hash "$rollback_root/pmos.conf" "$current_entry_sha"

module_stage=$(mktemp -d /var/tmp/luma-container1-modules.XXXXXXXX)
tar --use-compress-program=unzstd -C "$module_stage" -xf "$bundle/modules-$release.tar.zst"
[[ -d $module_stage/lib/modules/$release && ! -L $module_stage/lib/modules/$release ]] ||
  die 'module archive did not produce the expected release'
module_count=$(find "$module_stage/lib/modules/$release" -type f -name '*.ko.zst' -printf . | wc -c)
[[ $module_count -eq 399 ]] || die "module count mismatch: $module_count"
mv "$module_stage/lib/modules/$release" "/lib/modules/$release"
sync -f "/lib/modules/$release"

install -m 0700 "$bundle/Image.gz" /boot/vmlinuz-container1.new
install -m 0700 "$bundle/linux.efi" /boot/linux-container1.efi.new
sync -f /boot/vmlinuz-container1.new
sync -f /boot/linux-container1.efi.new
mv /boot/vmlinuz-container1.new /boot/vmlinuz-container1
mv /boot/linux-container1.efi.new /boot/linux-container1.efi
require_hash /boot/vmlinuz-container1 "$candidate_image_sha"
require_hash /boot/linux-container1.efi "$candidate_efi_sha"

install -m 0700 /dev/stdin "$candidate_entry" <<'EOF'
title Luma FP6 BinderFS candidate (three-attempt gate)
sort-key zz-luma-container1
linux linux-container1.efi

initrd initramfs
options quiet splash plymouth.ignore-serial-consoles plymouth.prefer-fbcon console=tty0 pmos_boot_uuid=DA38-1C4C pmos_root_uuid=b6795375-59e7-4bbb-8226-1f1f1597ef1a pmos_rootfsopts=defaults
devicetree milos-fairphone-fp6.dtb
EOF
install -m 0700 /dev/stdin /boot/loader/loader.conf.new <<'EOF'
default pmos-container1*
timeout 2
editor no
auto-entries no
auto-firmware no
EOF
sync -f "$candidate_entry"
sync -f /boot/loader/loader.conf.new
mv /boot/loader/loader.conf.new /boot/loader/loader.conf
sync -f /boot/loader

require_hash /boot/linux.efi "$current_efi_sha"
require_hash /boot/vmlinuz "$current_image_sha"
require_hash /boot/initramfs "$current_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$current_dtb_sha"
require_hash /boot/loader/entries/pmos.conf "$current_entry_sha"
printf 'READY_FOR_BINDERFS_CANDIDATE_REBOOT=true\n'
printf 'candidate_release=%s\n' "$release"
printf 'candidate_boot_attempts=3\n'
printf 'rollback_entry=pmos.conf\n'
printf 'existing_boot_files_unchanged=true\n'
