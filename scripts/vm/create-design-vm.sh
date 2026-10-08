#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s /absolute/path/to/baseline.qcow2\n' "$0" >&2
  exit 2
fi

artifact=$1
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
vm_name=${LUMA_DESIGN_VM_NAME:-luma-design}
delivery_track=${LUMA_DELIVERY_TRACK:-A}
vm_dir=${LUMA_VM_DIR:-/var/lib/libvirt/images/luma}
base_disk="$vm_dir/baseline.qcow2"
design_disk="$vm_dir/design.qcow2"

for tool in qemu-img sha256sum sudo virt-install virsh; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ ! -f "$artifact" ]; then
  printf 'error: baseline artifact does not exist: %s\n' "$artifact" >&2
  exit 1
fi

artifact=$(realpath "$artifact")
if sudo virsh dominfo "$vm_name" >/dev/null 2>&1; then
  printf 'error: VM already exists: %s (refusing to replace it)\n' "$vm_name" >&2
  exit 1
fi

sudo install -d -m 0755 "$vm_dir"
sudo install -m 0444 "$artifact" "$base_disk"
sudo qemu-img create -q -f qcow2 -F qcow2 -b "$base_disk" "$design_disk"
case "$delivery_track" in
  A) "$repo_root/scripts/vm/enable-track-a-lab-ssh.sh" "$design_disk" ;;
  B) : ;;
  *) printf 'error: LUMA_DELIVERY_TRACK must be A or B\n' >&2; exit 2 ;;
esac
# Relabel only the two files created above. Other VMs in this shared directory
# may be running with dynamic sVirt categories that must remain untouched.
sudo restorecon -F "$base_disk" "$design_disk" >/dev/null 2>&1 || true

sudo virt-install \
  --connect qemu:///system \
  --name "$vm_name" \
  --description 'Project Luma persistent design lane; live edits are never canonical' \
  --machine q35 \
  --cpu host-passthrough \
  --vcpus 4,sockets=1,cores=4,threads=1 \
  --memory 8192 \
  --import \
  --disk "path=$design_disk,format=qcow2,bus=virtio,cache=none,discard=unmap" \
  --network network=default,model=virtio \
  --boot uefi \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
  --rng /dev/urandom \
  --graphics type=spice,listens0.type=socket,listens0.socket=/run/libvirt/qemu/luma-viewer/spice.sock,gl.enable=yes,gl.rendernode=/dev/dri/renderD128 \
  --video model.type=virtio,model.acceleration.accel3d=yes \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --console pty,target.type=serial \
  --os-variant fedora-unknown \
  --autostart \
  --noautoconsole

printf 'Design VM created and started: %s\n' "$vm_name"
printf 'Open Cockpit Machines for its graphical console.\n'
