#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Explicit recovery/validation backend; does not replace the Try Luma factory.
set -euo pipefail
if [[ $# -ne 2 || $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
  echo "usage (Apple Silicon): $0 EXISTING_QCOW2 STATE_DIRECTORY" >&2
  exit 2
fi
disk=$1
state=$2
[[ -f "$disk" && "$disk" = /* && "$state" = /* ]] || {
  echo "Use an existing disk and absolute paths" >&2; exit 2;
}
[[ "$disk$state" != *,* ]] || {
  echo "QEMU drive paths must not contain commas" >&2; exit 2;
}
for tool in qemu-system-aarch64 qemu-img; do
  command -v "$tool" >/dev/null
done
firmware=${LUMA_QEMU_FIRMWARE_DIR:-/opt/homebrew/share/qemu}
test -r "$firmware/edk2-aarch64-code.fd"
test -r "$firmware/edk2-arm-vars.fd"
# QEMU's normal disk lock refuses another writer. Never disable that lock.
qemu-img info "$disk" >/dev/null
mkdir -p "$state"
if [[ ! -e "$state/vars.fd" ]]; then
  cp -n "$firmware/edk2-arm-vars.fd" "$state/vars.fd"
fi
exec qemu-system-aarch64 \
  -name 'Luma compatibility recovery' \
  -machine virt,accel=hvf,highmem=on -cpu host -smp 2 -m 6144 \
  -drive "if=pflash,format=raw,readonly=on,file=$firmware/edk2-aarch64-code.fd" \
  -drive "if=pflash,format=raw,file=$state/vars.fd" \
  -drive "if=none,id=os,format=qcow2,cache=none,file=$disk" \
  -device virtio-blk-pci,drive=os,bootindex=1 \
  -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${LUMA_RECOVERY_SSH_PORT:-2235}-:22" \
  -device virtio-net-pci,netdev=net0,mac=52:54:00:23:99:66 \
  -device virtio-rng-pci -device virtio-gpu-pci,xres=1280,yres=800 \
  -audiodev coreaudio,id=audio0 -device virtio-sound-pci,audiodev=audio0 \
  -device qemu-xhci -device usb-kbd -device usb-tablet \
  -display cocoa -serial "file:$state/serial-$(date -u +%Y%m%dT%H%M%SZ).log"
