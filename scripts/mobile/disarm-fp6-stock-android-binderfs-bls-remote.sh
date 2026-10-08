#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Disarm the BLS-only BinderFS selector after proving that the FP6 Android
# boot chain loads boot_b directly.  Candidate payloads are retained as
# evidence; only the ignored selector entry is archived.

set -euo pipefail
umask 077

entry=/boot/loader/entries/pmos-binderfs-ims1+3.conf
archive=/var/lib/luma/fp6-stock-android-container-kernel/ignored-bls-compat1
proven_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446
candidate_efi_sha=8a97eed2c3a4f9d125f49a8d2c98f267900f805bd046215fe585edbb2c8c12b8
candidate_image_sha=d1476dbea625aeb22d5d2a27df3a928cd9a2a13bd72a80bfda8507d376df0153

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
zcat /proc/config.gz | grep -Fx '# CONFIG_ANDROID_BINDERFS is not set' >/dev/null ||
  die 'proven kernel is not running'
[[ -f $entry && ! -L $entry ]] || die 'expected pending BLS entry is absent'
[[ $(hash /boot/loader/entries/pmos.conf) == "$proven_entry_sha" ]] || die 'rollback entry differs'
[[ $(hash /boot/linux-binderfs-ims1.efi) == "$candidate_efi_sha" ]] || die 'candidate EFI differs'
[[ $(hash /boot/vmlinuz-binderfs-ims1) == "$candidate_image_sha" ]] || die 'candidate image differs'

install -d -m 0700 "$archive"
archive_entry=$archive/$(basename "$entry")
if [[ -e $archive_entry ]]; then
  [[ -f $archive_entry && ! -L $archive_entry ]] || die 'archive target is not a regular file'
  [[ $(hash "$archive_entry") == "$(hash "$entry")" ]] || die 'archived selector differs'
  # A prior, identical ignored selector is already recoverably archived.
  rm -f -- "$entry"
else
  mv "$entry" "$archive_entry"
fi
install -m 0700 /dev/stdin /boot/loader/loader.conf.new <<'EOF'
default pmos.conf
timeout 2
editor no
auto-entries no
auto-firmware no
EOF
sync -f "$archive_entry"
sync -f /boot/loader/loader.conf.new
mv /boot/loader/loader.conf.new /boot/loader/loader.conf
sync -f /boot/loader

printf 'IGNORED_BINDERFS_BLS_DISARMED=true\n'
printf 'loader_default=pmos.conf\n'
printf 'candidate_payloads_retained=true\n'
printf 'running_kernel_unchanged=true\n'
