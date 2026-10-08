#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
protected_vm=${LUMA_PROTECTED_VM:-viola-windows-builder}
vm_name=${LUMA_PREVIEW_VM_NAME:-luma-preview}
memory_mib=${LUMA_PREVIEW_MEMORY_MIB:-8192}
vcpus=${LUMA_PREVIEW_VCPUS:-4}
qemu_user=${LUMA_LIBVIRT_QEMU_USER:-qemu}
qemu_group=${LUMA_LIBVIRT_QEMU_GROUP:-qemu}

if [ "$(id -u)" -ne 0 ]; then
  printf 'error: publish-preview-vm must run as root\n' >&2
  exit 1
fi
if [ "$#" -ne 1 ]; then
  printf 'usage: %s /srv/luma-build/path/to/standalone.qcow2\n' "$0" >&2
  exit 2
fi

for tool in getent jq qemu-img restorecon semanage sha256sum virt-install virsh; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required preview tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done
getent passwd "$qemu_user" >/dev/null || {
  printf 'error: libvirt QEMU user does not exist: %s\n' "$qemu_user" >&2
  exit 1
}
getent group "$qemu_group" >/dev/null || {
  printf 'error: libvirt QEMU group does not exist: %s\n' "$qemu_group" >&2
  exit 1
}

source_image=$(realpath "$1")
case "$source_image" in
  "$build_root"/*) ;;
  *)
    printf 'error: preview source must live below %s: %s\n' \
      "$build_root" "$source_image" >&2
    exit 1
    ;;
esac
if [ ! -f "$source_image" ]; then
  printf 'error: preview source does not exist: %s\n' "$source_image" >&2
  exit 1
fi
if qemu-img info --output=json "$source_image" |
  jq -e 'has("backing-filename")' >/dev/null; then
  printf 'error: preview source must be standalone; flatten its backing chain first\n' >&2
  exit 1
fi

protected_state=$(virsh domstate "$protected_vm" 2>/dev/null | tr -d '\r' || true)
if [ "$protected_state" != running ]; then
  printf 'error: protected VM %s is %s; refusing preview publication\n' \
    "$protected_vm" "${protected_state:-unavailable}" >&2
  exit 75
fi
available_mib=$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)
minimum_mib=$((memory_mib + 2048))
if [ "${available_mib:-0}" -lt "$minimum_mib" ]; then
  printf 'error: preview requires %s MiB available; host has %s MiB\n' \
    "$minimum_mib" "${available_mib:-0}" >&2
  exit 75
fi
if virsh dominfo "$vm_name" >/dev/null 2>&1; then
  printf 'error: VM already exists: %s; publication never replaces it implicitly\n' \
    "$vm_name" >&2
  exit 1
fi

vm_dir="$build_root/vm/$vm_name"
target_image="$vm_dir/$vm_name.qcow2"
# The build account owns preview bookkeeping while QEMU's dedicated group
# receives traversal only. The disk itself remains qemu:qemu mode 0600.
install -d -o luma-build -g "$qemu_group" -m 0750 "$vm_dir"
if [ -e "$target_image" ]; then
  printf 'error: preview target already exists: %s\n' "$target_image" >&2
  exit 1
fi
cp --reflink=auto --sparse=always "$source_image" "$target_image"
chown "$qemu_user:$qemu_group" "$target_image"
chmod 0600 "$target_image"

vm_fcontext="$build_root/vm(/.*)?"
if ! semanage fcontext -l |
  awk -v pattern="$vm_fcontext" \
    '$1 == pattern && $NF ~ /:virt_image_t:/ { found=1 } END { exit !found }'; then
  semanage fcontext -a -t virt_image_t "$vm_fcontext"
fi
restorecon -RF "$build_root/vm"

virt-install \
  --connect qemu:///system \
  --name "$vm_name" \
  --description 'Project Luma current Stage web preview; isolated from production builds' \
  --machine q35 \
  --cpu host-passthrough \
  --vcpus "$vcpus",sockets=1,cores="$vcpus",threads=1 \
  --memory "$memory_mib" \
  --import \
  --disk "path=$target_image,format=qcow2,bus=virtio,cache=none,discard=unmap" \
  --network network=default,model=virtio \
  --boot uefi \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
  --rng /dev/urandom \
  --graphics vnc,listen=127.0.0.1 \
  --video virtio \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --console pty,target.type=serial \
  --osinfo fedora-unknown \
  --autostart \
  --noautoconsole

sha256sum "$source_image" >"$vm_dir/source.sha256"
{
  printf 'published_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source=%s\n' "$source_image"
  printf 'vm=%s\n' "$vm_name"
  printf 'vcpus=%s\n' "$vcpus"
  printf 'memory_mib=%s\n' "$memory_mib"
} >"$vm_dir/publication.env"
chmod 0644 "$vm_dir/source.sha256" "$vm_dir/publication.env"

printf 'Preview VM published: %s\n' "$vm_name"
printf 'Cockpit Machines: https://HOST:9090/machines\n'
