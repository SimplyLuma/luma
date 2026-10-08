#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s /absolute/path/to/baseline.qcow2\n' "$0" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"
artifact=$(realpath "$1")
guest_key="$repo_root/build/provisioning/luma-m0-guest"
delivery_track=${LUMA_DELIVERY_TRACK:-A}
run_id=$(date -u +%Y%m%dT%H%M%SZ)
vm_name="luma-clean-$run_id"
vm_dir=${LUMA_VM_DIR:-/var/lib/libvirt/images/luma}
base_disk="$vm_dir/acceptance-base.qcow2"
overlay="$vm_dir/$vm_name.qcow2"
report_dir="$repo_root/build/tests/$run_id"
report="$report_dir/baseline-smoke.txt"
desktop_report="$report_dir/desktop-smoke.txt"
keep_failed=${LUMA_KEEP_FAILED_VM:-0}
passed=0

mkdir -p "$report_dir"

cleanup() {
  if [ "$passed" -eq 1 ] || [ "$keep_failed" -ne 1 ]; then
    sudo virsh destroy "$vm_name" >/dev/null 2>&1 || true
    sudo virsh undefine "$vm_name" --nvram --tpm >/dev/null 2>&1 ||
      sudo virsh undefine "$vm_name" --nvram >/dev/null 2>&1 || true
    sudo rm -f "$overlay"
  else
    printf 'Failed VM retained for diagnosis: %s\n' "$vm_name" >&2
  fi
}
trap cleanup EXIT INT TERM

sudo install -d -m 0755 "$vm_dir"
sudo install -m 0444 "$artifact" "$base_disk"
sudo qemu-img create -q -f qcow2 -F qcow2 -b "$base_disk" "$overlay"
case "$delivery_track" in
  A) "$repo_root/scripts/vm/enable-track-a-lab-ssh.sh" "$overlay" ;;
  B) : ;;
  *) printf 'error: LUMA_DELIVERY_TRACK must be A or B\n' >&2; exit 2 ;;
esac
# Never recursively relabel the shared VM directory: that would strip the
# dynamic sVirt category from any running design VM disk and force it read-only.
sudo restorecon -F "$base_disk" "$overlay" >/dev/null 2>&1 || true

sudo virt-install \
  --connect qemu:///system \
  --name "$vm_name" \
  --description 'Project Luma disposable clean acceptance lane' \
  --machine q35 \
  --cpu host-passthrough \
  --vcpus 4,sockets=1,cores=4,threads=1 \
  --memory 8192 \
  --import \
  --disk "path=$overlay,format=qcow2,bus=virtio,cache=none,discard=unmap" \
  --network network=default,model=virtio \
  --boot uefi \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
  --rng /dev/urandom \
  --graphics vnc,listen=127.0.0.1 \
  --video virtio \
  --console pty,target.type=serial \
  --os-variant fedora-unknown \
  --noautoconsole

LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" 'bash -s' \
  <"$repo_root/tests/smoke/baseline.sh" | tee "$report"
grep -qx 'Baseline smoke test: PASS' "$report"

if [ "${LUMA_DESKTOP_EXPECTED:-0}" -eq 1 ]; then
  LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
    "$repo_root/scripts/vm/guest-ssh.sh" \
      "PRAIRIE_CORE_APPS_NEVRA='$PRAIRIE_CORE_APPS_NEVRA' LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA='$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA' bash -s" \
    <"$repo_root/tests/smoke/desktop.sh" | tee "$desktop_report"
  grep -qx 'Desktop smoke test: PASS' "$desktop_report"
fi

sudo virsh screenshot "$vm_name" "$report_dir/console.ppm" --screen 0 >/dev/null
sudo chown -R "$(id -u):$(id -g)" "$report_dir"
sha256sum "$artifact" >"$report_dir/artifact.sha256"
passed=1
printf 'Clean-lane smoke test passed; evidence: %s\n' "$report_dir"
