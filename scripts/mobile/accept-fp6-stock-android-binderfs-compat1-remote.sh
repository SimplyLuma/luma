#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Accept the same-release BinderFS candidate only after the running config,
# filesystem registration, private mount behavior, ownership boundaries, and
# boot fault window have all passed on the physical FP6.

set -euo pipefail
umask 077

release=7.1.2-luma-fp-ims1
candidate_efi_sha=8a97eed2c3a4f9d125f49a8d2c98f267900f805bd046215fe585edbb2c8c12b8
candidate_image_sha=d1476dbea625aeb22d5d2a27df3a928cd9a2a13bd72a80bfda8507d376df0153
proven_efi_sha=a69dbc085727ea4244cc0a3fce93553913e37dc6b48896c9969f7392f3346cb9
proven_entry_sha=20efc7d275f0e3784226cd9b43d80a4222240db744d4f2be7d54ee5888563446
hook=/var/tmp/luma-stock-android-container-build-inputs/fp6-stock-android-binderfs-lxc-hook.py
hook_sha=0b4b7ba7517a71b5894407e61ca5eaf9385a56b310f37b48c4e721e4fa2ffdca

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
model=$(tr -d '\0' </sys/firmware/devicetree/base/model)
[[ $model == 'The Fairphone (Gen. 6)' ]] || die 'device identity mismatch'
[[ $(uname -r) == "$release" ]] || die 'ims1 release is not running'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
zcat /proc/config.gz | grep -Fx 'CONFIG_ANDROID_BINDERFS=y' >/dev/null || die 'BinderFS candidate is not running'
grep -qw binder /proc/filesystems || die 'BinderFS is not registered'
[[ $(hash /boot/linux-binderfs-ims1.efi) == "$candidate_efi_sha" ]] || die 'candidate EFI differs'
[[ $(hash /boot/vmlinuz-binderfs-ims1) == "$candidate_image_sha" ]] || die 'candidate image differs'
[[ $(hash /boot/linux.efi) == "$proven_efi_sha" ]] || die 'rollback EFI changed'
[[ $(hash /boot/loader/entries/pmos.conf) == "$proven_entry_sha" ]] || die 'rollback entry changed'
[[ -f $hook && ! -L $hook && $(hash "$hook") == "$hook_sha" ]] || die 'private BinderFS hook differs'

for service in ModemManager luma-fp6-cellular-link luma-fp6-imsd; do
  systemctl is-active --quiet "$service" || die "Luma owner is inactive: $service"
done
for process in qcrilNrd imsdaemon ims-dataservice-daemon ims_rtp_daemon; do
  ! pgrep -f "(^|/)$process([[:space:]]|$)" >/dev/null ||
    die "stock process is unexpectedly running: $process"
done

test_root=$(mktemp -d /run/luma-binderfs-accept.XXXXXXXX)
mkdir -m 0700 "$test_root/dev" "$test_root/dev/binderfs"
cleanup() {
  rm -f -- "$test_root/dev/binder" "$test_root/dev/hwbinder" "$test_root/dev/vndbinder"
  rmdir "$test_root/dev/binderfs" "$test_root/dev" "$test_root" >/dev/null 2>&1 || true
}
trap cleanup EXIT
unshare --mount --ipc --fork --propagation private -- /bin/bash -euo pipefail -c '
  root=$1
  hook=$2
  mount -t binder binder -o max=3 "$root/dev/binderfs"
  findmnt -rn -T "$root/dev/binderfs" -o FSTYPE,OPTIONS |
    awk '\''$1 == "binder" && $2 ~ /(^|,)rw(,|$)/ { found=1 } END { exit !found }'\''
  env LXC_NAME=luma-stock-android-c1 LXC_ROOTFS_MOUNT="$root" "$hook"
  [[ $(readlink "$root/dev/binder") == binderfs/binder ]]
  [[ $(readlink "$root/dev/hwbinder") == binderfs/hwbinder ]]
  [[ $(readlink "$root/dev/vndbinder") == binderfs/vndbinder ]]
  umount "$root/dev/binderfs"
' binderfs-accept "$test_root" "$hook"
cleanup
trap - EXIT

if dmesg --raw | grep -Eiq 'Kernel panic|Oops:|BUG: unable to handle|hangcheck detected gpu lockup|GMU.*(timeout|fault)|QSEE.*(fault|panic)|TEE.*(fault|panic)'; then
  die 'critical boot fault found in kernel log'
fi

mapfile -t pending < <(
  find /boot/loader/entries -maxdepth 1 -type f -name 'pmos-binderfs-ims1+*.conf' -print
)
[[ ${#pending[@]} -eq 1 ]] || die 'expected exactly one pending BinderFS compatibility entry'
[[ ! -e /boot/loader/entries/pmos-binderfs-ims1.conf ]] || die 'accepted entry already exists'
mv "${pending[0]}" /boot/loader/entries/pmos-binderfs-ims1.conf
install -m 0700 /dev/stdin /boot/loader/loader.conf.new <<'EOF'
default pmos-binderfs-ims1.conf
timeout 2
editor no
auto-entries no
auto-firmware no
EOF
sync -f /boot/loader/entries/pmos-binderfs-ims1.conf
sync -f /boot/loader/loader.conf.new
mv /boot/loader/loader.conf.new /boot/loader/loader.conf
sync -f /boot/loader

printf 'BINDERFS_COMPAT_CANDIDATE_ACCEPTED=true\n'
printf 'private_binderfs_mount_verified=true\n'
printf 'luma_modem_owners_unchanged=true\n'
printf 'rollback_entry=pmos.conf\n'
