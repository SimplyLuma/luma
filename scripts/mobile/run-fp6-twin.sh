#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-twin/inputs.env"

build_dir=${LUMA_TWIN_BUILD_DIR:-$repo_root/build/mobile/fp6-twin}
disk="$build_dir/luma-fp6-twin.qcow2"
seed="$build_dir/luma-fp6-twin-seed.iso"
qmp_socket="$build_dir/qmp.sock"
ssh_port=${LUMA_TWIN_SSH_PORT_OVERRIDE:-$LUMA_TWIN_SSH_PORT}
vnc_display=${LUMA_TWIN_VNC_DISPLAY_OVERRIDE:-$LUMA_TWIN_VNC_DISPLAY}
headless=${LUMA_TWIN_HEADLESS:-0}

if [ "${1:-}" = '--headless' ]; then
  headless=1
  shift
fi
if [ "$#" -ne 0 ]; then
  printf 'usage: %s [--headless]\n' "$0" >&2
  exit 2
fi

for tool in qemu-img qemu-system-aarch64; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required FP6 twin runtime tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

for path in "$disk" "$seed"; do
  [ -f "$path" ] || {
    printf 'error: missing prepared twin artifact: %s\n' "$path" >&2
    printf 'Run scripts/mobile/prepare-fp6-twin.sh first.\n' >&2
    exit 1
  }
done

find_uefi() {
  for candidate in \
    "${LUMA_AARCH64_UEFI_CODE:-}" \
    /usr/share/edk2/aarch64/QEMU_EFI.fd \
    /usr/share/AAVMF/AAVMF_CODE.fd \
    /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
    /usr/local/share/qemu/edk2-aarch64-code.fd; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

uefi=$(find_uefi) || {
  printf 'error: no AArch64 UEFI firmware found\n' >&2
  printf 'Set LUMA_AARCH64_UEFI_CODE to the absolute firmware path.\n' >&2
  exit 1
}

host_arch=$(uname -m)
host_os=$(uname -s)
if [ "$host_os" = Darwin ] && [ "$host_arch" = arm64 ]; then
  accelerator=hvf
  cpu=host
elif [ "$host_os" = Linux ] && [ "$host_arch" = aarch64 ] && [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
  accelerator=kvm
  cpu=host
else
  accelerator=tcg,thread=multi
  cpu=max
fi

if [ -e "$qmp_socket" ]; then
  printf 'error: QMP socket already exists: %s\n' "$qmp_socket" >&2
  printf 'Confirm the prior twin is stopped, then remove only that stale socket.\n' >&2
  exit 1
fi

display_args=()
if [ "$headless" -eq 1 ]; then
  display_args=(-display none -vnc "127.0.0.1:$vnc_display")
elif [ "$host_os" = Darwin ]; then
  display_args=(-display cocoa,zoom-to-fit=on)
elif [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
  display_args=(-display gtk,zoom-to-fit=on)
else
  display_args=(-display none -vnc "127.0.0.1:$vnc_display")
fi

printf 'Starting Luma FP6 behavioral twin (%s acceleration).\n' "$accelerator"
printf 'SSH will listen on 127.0.0.1:%s after cloud-init completes.\n' "$ssh_port"
if [ "${display_args[0]}" = -display ] && [ "${display_args[1]}" = none ]; then
  printf 'VNC will listen on 127.0.0.1:%s.\n' "$((5900 + vnc_display))"
fi

exec qemu-system-aarch64 \
  -name luma-fp6-twin \
  -machine "$LUMA_TWIN_MACHINE",gic-version=3,highmem=on \
  -accel "$accelerator" \
  -cpu "$cpu" \
  -smp "$LUMA_TWIN_CPU_COUNT" \
  -m "$LUMA_TWIN_MEMORY_MIB" \
  -bios "$uefi" \
  -drive "if=none,id=os,file=$disk,format=qcow2,cache=none,discard=unmap" \
  -device virtio-blk-pci,drive=os \
  -drive "if=none,id=seed,file=$seed,format=raw,readonly=on" \
  -device virtio-blk-pci,drive=seed \
  -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$ssh_port-:22" \
  -device virtio-net-pci,netdev=net0 \
  -device virtio-rng-pci \
  -device virtio-gpu-pci,edid=on,xres="$FP6_DISPLAY_WIDTH",yres="$FP6_DISPLAY_HEIGHT",max_outputs=1 \
  -device virtio-keyboard-pci \
  -device virtio-tablet-pci \
  -qmp "unix:$qmp_socket,server=on,wait=off" \
  -serial mon:stdio \
  "${display_args[@]}"
