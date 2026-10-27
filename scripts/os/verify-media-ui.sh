#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Boot Atlas installer media the way a person does and require the
# interactive installer to render (never on hardware).
#
#   verify-media-ui.sh --iso ISO [--minutes N] [--keep-vm]
#
# The ISO boots in a disposable UEFI VM with no kickstart, from its own boot
# menu. The installer's web UI must come up and stay up: two screenshots at
# least 30 seconds apart, both past the first 90 seconds of boot, must each
# show at least 32 colours with no single colour covering 95% of the screen.
# A silent grey or black screen (the failure the Atlas watchdog exists for)
# has a handful of colours and one covering nearly all of it. Screenshots are
# read with scripts/os/lib/screen_image.py, whatever format libvirt writes.
#
# This proves the installer draws a real screen; it does not read what the
# screen says, so the watchdog's readable error screen would also pass. The
# evidence PNGs are kept for a person to look at.
#
# Evidence: $LUMA_OS_ROOT/media/verify-ui-<UTC time>/.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"
. "$(dirname -- "$0")/lib/gate-vm.sh"

iso=
minutes=10
keep=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) iso=$(realpath "${2:?}"); shift 2 ;;
    --minutes) minutes=${2:?}; shift 2 ;;
    --keep-vm) keep=1; shift ;;
    *) printf 'usage: %s --iso ISO [--minutes N] [--keep-vm]\n' "$0" >&2; exit 2 ;;
  esac
done
[ -f "$iso" ] && [ -f "$iso.release" ] || { printf 'ISO or its .release record is missing: %s\n' "$iso" >&2; exit 2; }
# Use the medium's recorded payload target, including retained x86 records
# that predate the explicit architecture field. Never choose the host CPU.
luma_os_load_env /dev/stdin <<<"$(sed 's/^\([a-z_]*\)=/MEDIA_\U\1=/' "$iso.release")"
media_architecture=${MEDIA_ARCHITECTURE:-$(printf '%s' "$MEDIA_REF" | cut -d/ -f3)}
luma_os_select_architecture "$media_architecture"
[ "$MEDIA_REF" = "$(luma_os_channel_ref "$MEDIA_CHANNEL")" ] ||
  luma_os_die 'media architecture/channel does not match its payload ref'
kvm_available=no
[ ! -r /dev/kvm ] || [ ! -w /dev/kvm ] || kvm_available=yes
luma_os_gate_vm_target "$LUMA_OS_ARCH" "$(uname -m)" "$kvm_available"
luma_os_require_root
luma_os_check_host
luma_os_vm_lock
luma_os_check_space 10

run=$(luma_os_media_vm_run "$(date -u +%Y%m%dT%H%M%SZ)")
out="$LUMA_OS_ROOT/media/verify-ui-$run"
vm="$LUMA_OS_ROOT/vm/media-ui-$run"
install -d -m 0755 "$out" "$vm"
exec > >(tee -a "$out/verify-ui.log") 2>&1
domain="luma-os-media-ui-$run"
image="$luma_os_repo_root/scripts/os/lib/screen_image.py"
fixture_helper="$luma_os_repo_root/scripts/os/lib/media_vm_iso.py"
fixture="$out/installer.iso"
fixture_proof="$out/media-input.json"

cleanup() {
  local status=$?
  set +e
  virsh destroy "$domain" >/dev/null 2>&1
  if [ -f "$fixture_proof" ]; then
    python3 "$fixture_helper" verify "$fixture_proof" || status=1
  fi
  if [ "$keep" -eq 0 ]; then
    virsh undefine "$domain" --nvram >/dev/null 2>&1
    rm -rf "$vm"
    rm -f -- "$fixture"
  fi
  exit "$status"
}
trap cleanup EXIT

luma_os_gate_vm_preflight "$out/libvirt-capabilities.xml"
# libvirt may chown/relabel even a failed launch. Isolate those changes on a
# disposable inode and fail before VM startup if the real reader cannot reach it.
python3 "$fixture_helper" prepare "$iso" "$fixture" "$fixture_proof" \
  --reader "${LUMA_OS_QEMU_USER:-qemu}"
qemu-img create -q -f qcow2 "$vm/disk.qcow2" 48G
luma_os_log "booting $(basename "$iso") interactively (no kickstart)"
virt-install --connect qemu:///system --name "$domain" \
  --arch "$LUMA_OS_ARCH" --virt-type "$gate_virt" \
  --memory 6144 --vcpus 4 --cpu "$gate_cpu" --machine "$gate_machine" --boot "$gate_boot" \
  --disk "path=$vm/disk.qcow2,format=qcow2,bus=virtio,cache=unsafe,discard=unmap" \
  --cdrom "$fixture" --network none \
  --graphics vnc,listen=127.0.0.1 --video virtio --rng /dev/urandom \
  --os-variant fedora-unknown --noautoconsole --wait 0 >"$out/virt-install.log" 2>&1 ||
  luma_os_die 'the installer VM did not start'

started=$SECONDS
deadline=$((SECONDS + minutes * 60))
good=0
last_good=-1
shot=0
result=fail
while [ "$SECONDS" -lt "$deadline" ]; do
  sleep 15
  shot=$((shot + 1))
  elapsed=$((SECONDS - started))
  python3 "$luma_os_repo_root/scripts/os/lib/screen_image.py" capture "$domain" "$vm/screen.img" >/dev/null 2>&1 || continue
  read -r colours share < <(python3 "$image" stats "$vm/screen.img" 2>/dev/null || echo "0 1")
  printf '%4ss  colours %-5s largest colour share %s\n' "$elapsed" "$colours" "$share"
  if [ "$elapsed" -ge 90 ] && [ "$colours" -ge 32 ] &&
     python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) < 0.95 else 1)' "$share"; then
    python3 "$image" to-png "$vm/screen.img" "$out/ui-$shot.png" 2>/dev/null || true
    if [ "$good" -ge 1 ] && [ $((elapsed - last_good)) -ge 30 ]; then
      result=pass
      break
    fi
    [ "$good" -ge 1 ] || last_good=$elapsed
    good=1
  else
    good=0
    last_good=-1
  fi
done
python3 "$image" to-png "$vm/screen.img" "$out/final.png" 2>/dev/null || true

if [ "$result" = pass ]; then
  printf 'PASS  the interactive installer rendered and stayed up (see %s)\n' "$out"
else
  printf 'FAIL  the interactive installer did not render a real screen within %s minutes (last screen: %s/final.png)\n' "$minutes" "$out"
  exit 1
fi
