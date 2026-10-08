#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install a BinderFS-only EFI candidate beside the proven FP6 ims1 boot while
# deliberately reusing the accepted kernel release identity and active module
# tree. No existing boot payload, BLS entry, or module is overwritten.

set -euo pipefail
umask 077

stage=/var/tmp/luma-fp6-stock-android-binderfs-compat1
bundle=$stage/bundle
release=7.1.2-luma-fp-ims1
candidate_efi_sha=8a97eed2c3a4f9d125f49a8d2c98f267900f805bd046215fe585edbb2c8c12b8
candidate_image_sha=d1476dbea625aeb22d5d2a27df3a928cd9a2a13bd72a80bfda8507d376df0153
candidate_config_sha=7eb084e3a201a11e69676aad4c93b9588e72f15dc9ef86d5d5c56ee13fc0f448
candidate_modules_sha=15e5c999e43cf65b9288de5d20d2eb7fe4efa7a00b26980b64ab964082bd19ca
candidate_manifest_sha=eaa613567a63a72d1c6b452a2e9ad1d74aa23e4ecba78a00d94ca5b802887117
proven_efi_sha=a69dbc085727ea4244cc0a3fce93553913e37dc6b48896c9969f7392f3346cb9
proven_image_sha=62556c8a87a0b8c345fdfd11770bffad7b112526e17cdb353600bec8648583ef
proven_initramfs_sha=3ed23188d9fb5ae8ed90b8d3b274e8d4d4432c3f60a25b14d718c33d9391a8a4
proven_dtb_sha=9a9d45187ef98874d84e1df9d06044a89e9aedb98d0b79b2b6d8bc32e7a164cf
proven_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446
candidate_entry=/boot/loader/entries/pmos-binderfs-ims1+3.conf

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
require_hash() {
  [[ -f $1 && ! -L $1 ]] || die "missing or linked file: $1"
  [[ $(hash "$1") == "$2" ]] || die "hash mismatch: $1"
}

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
model=$(tr -d '\0' </sys/firmware/devicetree/base/model)
[[ $model == 'The Fairphone (Gen. 6)' ]] || die 'device identity mismatch'
[[ $(uname -r) == "$release" ]] || die 'proven ims1 kernel is not running'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
zcat /proc/config.gz | grep -Fx '# CONFIG_ANDROID_BINDERFS is not set' >/dev/null ||
  die 'running kernel is not the proven non-BinderFS kernel'
zcat /proc/config.gz | grep -Fx '# CONFIG_MODULE_SIG_FORCE is not set' >/dev/null ||
  die 'module signature enforcement differs'
[[ -d /lib/modules/$release && ! -L /lib/modules/$release ]] || die 'proven module tree absent'
module_count=$(find "/lib/modules/$release" -type f -name '*.ko.zst' -printf . | wc -c)
[[ $module_count -eq 399 ]] || die "proven module count mismatch: $module_count"
[[ ! -e $candidate_entry && ! -e /boot/loader/entries/pmos-binderfs-ims1.conf ]] ||
  die 'compatibility candidate BLS entry already exists'
[[ ! -e /boot/linux-binderfs-ims1.efi && ! -e /boot/vmlinuz-binderfs-ims1 ]] ||
  die 'compatibility candidate payload already exists'
[[ $(find /boot/loader/entries -maxdepth 1 -type f -name 'pmos-container1*.conf' | wc -l) -eq 0 ]] ||
  die 'failed container1 candidate is still active'
grep -qx 'default pmos.conf' /boot/loader/loader.conf || die 'proven loader default is not restored'

require_hash "$bundle/linux.efi" "$candidate_efi_sha"
require_hash "$bundle/Image.gz" "$candidate_image_sha"
require_hash "$bundle/config" "$candidate_config_sha"
require_hash "$bundle/manifest.env" "$candidate_manifest_sha"
grep -qx 'KERNEL_RELEASE=7.1.2-luma-fp-ims1' "$bundle/manifest.env" || die 'release manifest differs'
grep -qx 'RELEASE_MODE=ims1-compatible' "$bundle/manifest.env" || die 'release mode differs'
grep -qx "MODULE_TREE_SHA256=$candidate_modules_sha" "$bundle/manifest.env" || die 'module manifest differs'
grep -qx 'ANDROID_BINDERFS_BUILTIN=true' "$bundle/manifest.env" || die 'BinderFS manifest gate failed'
grep -qx 'CONFIG_ANDROID_BINDERFS=y' "$bundle/config" || die 'BinderFS config gate failed'

require_hash /boot/linux.efi "$proven_efi_sha"
require_hash /boot/vmlinuz "$proven_image_sha"
require_hash /boot/initramfs "$proven_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$proven_dtb_sha"
require_hash /boot/loader/entries/pmos.conf "$proven_entry_sha"

install -m 0700 "$bundle/Image.gz" /boot/vmlinuz-binderfs-ims1.new
install -m 0700 "$bundle/linux.efi" /boot/linux-binderfs-ims1.efi.new
sync -f /boot/vmlinuz-binderfs-ims1.new
sync -f /boot/linux-binderfs-ims1.efi.new
mv /boot/vmlinuz-binderfs-ims1.new /boot/vmlinuz-binderfs-ims1
mv /boot/linux-binderfs-ims1.efi.new /boot/linux-binderfs-ims1.efi
require_hash /boot/vmlinuz-binderfs-ims1 "$candidate_image_sha"
require_hash /boot/linux-binderfs-ims1.efi "$candidate_efi_sha"

[[ $(grep -c '^title ' /boot/loader/entries/pmos.conf) -eq 1 ]] || die 'rollback title field differs'
[[ $(grep -c '^linux ' /boot/loader/entries/pmos.conf) -eq 1 ]] || die 'rollback Linux field differs'
sed \
  -e 's/^title .*/title Luma FP6 BinderFS compatibility candidate (three-attempt gate)/' \
  -e 's#^linux .*#linux linux-binderfs-ims1.efi#' \
  /boot/loader/entries/pmos.conf >"$candidate_entry.new"
chmod 0700 "$candidate_entry.new"
grep -qx 'linux linux-binderfs-ims1.efi' "$candidate_entry.new" || die 'candidate Linux field differs'
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
require_hash /boot/loader/entries/pmos.conf "$proven_entry_sha"
[[ $(find "/lib/modules/$release" -type f -name '*.ko.zst' -printf . | wc -c) -eq 399 ]] ||
  die 'active module tree changed'

printf 'READY_FOR_BINDERFS_COMPAT_REBOOT=true\n'
printf 'candidate_release=%s\n' "$release"
printf 'candidate_boot_attempts=3\n'
printf 'active_module_tree_unchanged=true\n'
printf 'rollback_entry=pmos.conf\n'
