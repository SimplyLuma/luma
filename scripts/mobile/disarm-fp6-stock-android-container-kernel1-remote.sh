#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Disarm the first BinderFS boot candidate after a failed physical gate. Keep
# every candidate artifact for diagnosis; only restore the proven pmos.conf as
# the loader default and move pending/accepted candidate entries out of the
# active systemd-boot entry directory.

set -euo pipefail
umask 077

proven_release=7.1.2-luma-fp-ims1
candidate_release=7.1.2-luma-fp-ims-container1
proven_efi_sha=a69dbc085727ea4244cc0a3fce93553913e37dc6b48896c9969f7392f3346cb9
proven_image_sha=62556c8a87a0b8c345fdfd11770bffad7b112526e17cdb353600bec8648583ef
proven_initramfs_sha=3ed23188d9fb5ae8ed90b8d3b274e8d4d4432c3f60a25b14d718c33d9391a8a4
proven_dtb_sha=9a9d45187ef98874d84e1df9d06044a89e9aedb98d0b79b2b6d8bc32e7a164cf
proven_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446
candidate_efi_sha=c87c25a24041a0541ac87aed3b346d9edb3673b2888e86243c0f2279f472fc8a
candidate_image_sha=b38f57c13532333f40d0318a20db94e2fff65e0d09e14184c987fa9196e6cb45
archive_root=/var/lib/luma/fp6-stock-android-container-kernel/failed-container1

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
require_hash() {
  [[ -f $1 && ! -L $1 ]] || die "missing or linked file: $1"
  [[ $(hash "$1") == "$2" ]] || die "hash mismatch: $1"
}

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
model=$(tr -d '\0' </sys/firmware/devicetree/base/model)
[[ $model == 'The Fairphone (Gen. 6)' ]] || die 'device identity mismatch'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
case $(uname -r) in
  "$proven_release"|"$candidate_release") ;;
  *) die 'running kernel is neither the proven boot nor the failed candidate' ;;
esac

require_hash /boot/linux.efi "$proven_efi_sha"
require_hash /boot/vmlinuz "$proven_image_sha"
require_hash /boot/initramfs "$proven_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$proven_dtb_sha"
require_hash /boot/loader/entries/pmos.conf "$proven_entry_sha"
require_hash /boot/linux-container1.efi "$candidate_efi_sha"
require_hash /boot/vmlinuz-container1 "$candidate_image_sha"

mapfile -t candidate_entries < <(
  find /boot/loader/entries -maxdepth 1 -type f -name 'pmos-container1*.conf' -print | sort
)
[[ ${#candidate_entries[@]} -eq 1 ]] ||
  die "expected exactly one active container1 entry, found ${#candidate_entries[@]}"

install -d -m 0700 "$archive_root"
entry_name=$(basename "${candidate_entries[0]}")
[[ ! -e $archive_root/$entry_name ]] || die 'archive entry already exists'
mv "${candidate_entries[0]}" "$archive_root/$entry_name"
sync -f "$archive_root/$entry_name"

install -m 0700 /dev/stdin /boot/loader/loader.conf.new <<'EOF'
default pmos.conf
timeout 2
editor no
auto-entries no
auto-firmware no
EOF
sync -f /boot/loader/loader.conf.new
mv /boot/loader/loader.conf.new /boot/loader/loader.conf
sync -f /boot/loader

require_hash /boot/linux.efi "$proven_efi_sha"
require_hash /boot/vmlinuz "$proven_image_sha"
require_hash /boot/initramfs "$proven_initramfs_sha"
require_hash /boot/milos-fairphone-fp6.dtb "$proven_dtb_sha"
require_hash /boot/loader/entries/pmos.conf "$proven_entry_sha"
[[ ! -e /boot/loader/entries/$entry_name ]] || die 'failed entry remains active'
[[ -f $archive_root/$entry_name && ! -L $archive_root/$entry_name ]] ||
  die 'failed entry was not archived'

printf 'FAILED_BINDERFS_CANDIDATE_DISARMED=true\n'
printf 'running_release=%s\n' "$(uname -r)"
printf 'loader_default=pmos.conf\n'
printf 'archived_entry=%s/%s\n' "$archive_root" "$entry_name"
printf 'candidate_boot_payloads_retained=true\n'
