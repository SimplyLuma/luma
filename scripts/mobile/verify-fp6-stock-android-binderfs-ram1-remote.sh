#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Physical C0 gate for the RAM-only FP6 BinderFS boot.  It proves that the
# accepted boot_b remains unchanged and that a private mount can allocate
# exactly binder, hwbinder, and vndbinder without creating global host nodes.

set -Eeuo pipefail
umask 077

release=7.1.2-luma-fp-ims1
boot_b_sha=2eb1c71fe073c8624ed6f1a4f1267744cd7c8281b52c4f64eca65b9f9050a1e6
candidate=/var/tmp/luma-fp6-stock-android-binderfs-boot1/boot-fp6-luma-binderfs-ram1.img
candidate_sha=b4598536337e1b557a1f6d1d923ff61c1d2baa25adffa5d0c57a2fd8ccbc7086
hook=/var/tmp/luma-stock-android-container-build-inputs/fp6-stock-android-binderfs-lxc-hook.py
hook_sha=0b4b7ba7517a71b5894407e61ca5eaf9385a56b310f37b48c4e721e4fa2ffdca
test_root=

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
cleanup() {
  [[ -z ${test_root:-} || ! -d $test_root ]] || find "$test_root" -depth -delete
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not running slot b'
[[ $(uname -r) == "$release" ]] || die 'accepted release identity differs'
zcat /proc/config.gz | grep -Fx 'CONFIG_ANDROID_BINDERFS=y' >/dev/null ||
  die 'RAM-only BinderFS kernel is not running'
zcat /proc/config.gz | grep -Fx '# CONFIG_MODULE_SIG_FORCE is not set' >/dev/null ||
  die 'module signature enforcement differs'
grep -w binder /proc/filesystems >/dev/null || die 'BinderFS is not registered'
[[ $(find "/lib/modules/$release" -type f -name '*.ko.zst' -printf . | wc -c) -eq 399 ]] ||
  die 'accepted module tree differs'
[[ $(hash /dev/disk/by-partlabel/boot_b) == "$boot_b_sha" ]] || die 'boot_b changed'
[[ -f $candidate && ! -L $candidate && $(hash "$candidate") == "$candidate_sha" ]] ||
  die 'RAM-only candidate evidence differs'
[[ -f $hook && ! -L $hook && $(hash "$hook") == "$hook_sha" ]] ||
  die 'BinderFS hook differs'
[[ -z $(findmnt -rn -t binder) ]] || die 'host already has a BinderFS mount'
for node in /dev/binder /dev/hwbinder /dev/vndbinder; do
  [[ ! -e $node && ! -L $node ]] || die "global Binder node exists: $node"
done
for process in qcrilNrd imsdaemon ims-dataservice-daemon ims_rtp_daemon; do
  ! pgrep -f "(^|/)$process([[:space:]]|$)" >/dev/null ||
    die "stock process is already running: $process"
done

test_root=$(mktemp -d /run/luma-binderfs-ram1.XXXXXXXX)
mkdir -m 0700 "$test_root/dev" "$test_root/dev/binderfs"
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
  device_count=$(find "$root/dev/binderfs" -maxdepth 1 -type c ! -name binder-control -printf . | wc -c)
  [[ $device_count -eq 3 ]]
  umount "$root/dev/binderfs"
' binderfs-ram1 "$test_root" "$hook"
cleanup
test_root=

[[ -z $(findmnt -rn -t binder) ]] || die 'private BinderFS mount leaked to host'
for node in /dev/binder /dev/hwbinder /dev/vndbinder; do
  [[ ! -e $node && ! -L $node ]] || die "private Binder node leaked to host: $node"
done
if dmesg --raw | grep -Ei \
  'Kernel panic|Oops:|BUG: unable to handle|hangcheck detected gpu lockup|GMU.*(timeout|fault)|QSEE.*(fault|panic)|TEE.*(fault|panic)' \
  >/dev/null; then
  die 'critical kernel, TEE, GPU, or GMU fault found'
fi

printf 'BINDERFS_RAM1_PHYSICAL_GATE=true\n'
printf 'running_release=%s\n' "$release"
printf 'boot_b_unchanged=true\n'
printf 'private_binderfs_mount_verified=true\n'
printf 'private_binder_device_count=3\n'
printf 'global_binder_nodes=0\n'
printf 'stock_hardware_processes=0\n'
printf 'critical_faults=0\n'
printf 'partition_writes=0\n'
