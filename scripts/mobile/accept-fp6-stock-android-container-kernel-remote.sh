#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Mark the already booted BinderFS candidate good only after its exact runtime
# identity and BinderFS support have been observed.

set -euo pipefail
umask 077

release=7.1.2-luma-fp-ims-container1
candidate_efi_sha=c87c25a24041a0541ac87aed3b346d9edb3673b2888e86243c0f2279f472fc8a
candidate_image_sha=b38f57c13532333f40d0318a20db94e2fff65e0d09e14184c987fa9196e6cb45
current_efi_sha=a69dbc085727ea4244cc0a3fce93553913e37dc6b48896c9969f7392f3346cb9
current_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
model=$(tr -d '\0' </sys/firmware/devicetree/base/model)
[[ $model == 'The Fairphone (Gen. 6)' ]] || die 'device identity mismatch'
[[ $(uname -r) == "$release" ]] || die 'candidate kernel is not running'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
zcat /proc/config.gz | grep -Fx 'CONFIG_ANDROID_BINDERFS=y' >/dev/null || die 'BinderFS is not enabled'
grep -qw binder /proc/filesystems || die 'BinderFS filesystem is not registered'
[[ -d /lib/modules/$release && ! -L /lib/modules/$release ]] || die 'matching modules absent'
[[ $(hash /boot/linux-container1.efi) == "$candidate_efi_sha" ]] || die 'candidate EFI differs'
[[ $(hash /boot/vmlinuz-container1) == "$candidate_image_sha" ]] || die 'candidate image differs'
[[ $(hash /boot/linux.efi) == "$current_efi_sha" ]] || die 'rollback EFI changed'
[[ $(hash /boot/loader/entries/pmos.conf) == "$current_entry_sha" ]] || die 'rollback entry changed'

mapfile -t pending < <(find /boot/loader/entries -maxdepth 1 -type f \
  -name 'pmos-container1+*.conf' -print)
[[ ${#pending[@]} -eq 1 ]] || die 'expected exactly one pending candidate entry'
[[ ! -e /boot/loader/entries/pmos-container1.conf ]] || die 'accepted entry already exists'
mv "${pending[0]}" /boot/loader/entries/pmos-container1.conf
install -m 0700 /dev/stdin /boot/loader/loader.conf.new <<'EOF'
default pmos-container1.conf
timeout 2
editor no
auto-entries no
auto-firmware no
EOF
sync -f /boot/loader/entries/pmos-container1.conf
sync -f /boot/loader/loader.conf.new
mv /boot/loader/loader.conf.new /boot/loader/loader.conf
sync -f /boot/loader
printf 'BINDERFS_CANDIDATE_ACCEPTED=true\n'
printf 'rollback_entry=pmos.conf\n'
