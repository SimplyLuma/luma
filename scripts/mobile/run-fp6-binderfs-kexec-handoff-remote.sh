#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Direct, RAM-only handoff from the proven non-BinderFS FP6 kernel to the
# already accepted same-release BinderFS image. This intentionally bypasses
# systemd-shutdown, whose loop-device finalizer hangs on the mounted read-only
# Android construction inputs. No boot file, partition, or slot is modified.

set -Eeuo pipefail
umask 077

release=7.1.2-luma-fp-ims1
image=/boot/vmlinuz-binderfs-ims1
initrd=/boot/initramfs
dtb=/boot/milos-fairphone-fp6.dtb
kexec_bin=/usr/sbin/kexec
evidence=/var/lib/luma/stock-android-container1/state/logs/binderfs-kexec-handoff.env
image_sha=d1476dbea625aeb22d5d2a27df3a928cd9a2a13bd72a80bfda8507d376df0153
initrd_sha=3ed23188d9fb5ae8ed90b8d3b274e8d4d4432c3f60a25b14d718c33d9391a8a4
dtb_sha=9a9d45187ef98874d84e1df9d06044a89e9aedb98d0b79b2b6d8bc32e7a164cf
kexec_sha=553f3a8743d6e0f082b53532c81bce47596ff9ab8a4600b40cc7fb01f10d6965

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
[[ $(uname -r) == "$release" ]] || die 'running release differs'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not on slot b'
zgrep -Fx '# CONFIG_ANDROID_BINDERFS is not set' /proc/config.gz >/dev/null ||
  die 'source kernel is not the proven non-BinderFS kernel'
zgrep -qx 'CONFIG_KEXEC=y' /proc/config.gz || die 'KEXEC is unavailable'
[[ $(lxc-info -n luma-stock-android-c1 -sH 2>/dev/null || printf STOPPED) == STOPPED ]] ||
  die 'Android container is active'
! grep -Eq '^(qcomtee|qseecomtee|qseecom) ' /proc/modules ||
  die 'a secure transport module is loaded'
[[ $(ps -e -o stat= | grep -c '^D' || true) -eq 0 ]] || die 'a task is in uninterruptible sleep'
[[ -f $image && ! -L $image && $(hash "$image") == "$image_sha" ]] || die 'kernel hash mismatch'
[[ -f $initrd && ! -L $initrd && $(hash "$initrd") == "$initrd_sha" ]] || die 'initramfs hash mismatch'
[[ -f $dtb && ! -L $dtb && $(hash "$dtb") == "$dtb_sha" ]] || die 'DTB hash mismatch'
[[ -x $kexec_bin && ! -L $kexec_bin && $(hash "$kexec_bin") == "$kexec_sha" ]] ||
  die 'kexec loader hash mismatch'
[[ $(rpm -q --qf '%{NAME}-%{EVR}.%{ARCH}' kexec-tools) == \
   kexec-tools-2.0.32-3.fc44.aarch64 ]] || die 'kexec package identity mismatch'
[[ $(cat /sys/kernel/kexec_loaded) == 0 ]] || die 'another kexec image is already loaded'

install -d -o root -g root -m 0700 "$(dirname "$evidence")"
cat >"$evidence" <<EOF
state=preflight_passed
source_boot_id=$(cat /proc/sys/kernel/random/boot_id)
source_kernel=$release
source_binderfs=false
target_kernel=$release
target_binderfs=true
target_image_sha256=$image_sha
initramfs_sha256=$initrd_sha
dtb_sha256=$dtb_sha
kexec_sha256=$kexec_sha
partition_writes=0
slot_changes=0
EOF
chmod 0600 "$evidence"

"$kexec_bin" --load "$image" --initrd="$initrd" --dtb="$dtb" --reuse-cmdline
[[ $(cat /sys/kernel/kexec_loaded) == 1 ]] || die 'target kernel did not load'
printf 'state=target_loaded\n' >>"$evidence"
sync

# Quiesce the hardware-owning user space explicitly. This service is launched
# by PID 1, so terminating the graphical user and network does not cancel it.
systemctl stop luma-fp6-imsd luma-fp6-cellular-link ModemManager greetd 2>/dev/null || true
loginctl terminate-user luma 2>/dev/null || true
systemctl stop NetworkManager 2>/dev/null || true
sync

[[ $(cat /sys/kernel/kexec_loaded) == 1 ]] || die 'loaded target was lost during quiesce'
printf 'state=executing_direct_handoff\n' >>"$evidence"
sync
exec "$kexec_bin" -e
