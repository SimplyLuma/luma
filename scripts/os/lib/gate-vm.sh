#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Shared release checks run unchanged on native KVM or cross-architecture TCG.
luma_os_gate_vm_target() {
  local arch=$1 host=$2 kvm_available=$3
  case "$arch" in
    x86_64) gate_machine=q35; gate_console=ttyS0 ;;
    aarch64) gate_machine=virt; gate_console=ttyAMA0 ;;
    *) luma_os_die "unsupported gate architecture: $arch"; return 1 ;;
  esac
  gate_cpu=max
  gate_virt=qemu
  if [ "$host" = "$arch" ] && [ "$kvm_available" = yes ]; then
    gate_cpu=host-passthrough
    gate_virt=kvm
  fi
  gate_caps_args=(--arch "$arch" --machine "$gate_machine" --virttype "$gate_virt")
  gate_boot=uefi
  if [ -n "${LUMA_OS_GATE_EMULATOR:-}" ]; then
    [[ "$LUMA_OS_GATE_EMULATOR" = /* && "$LUMA_OS_GATE_EMULATOR" != *,* && -x "$LUMA_OS_GATE_EMULATOR" ]] ||
      { luma_os_die 'gate emulator must be an executable absolute path without commas'; return 1; }
    gate_caps_args+=(--emulatorbin "$LUMA_OS_GATE_EMULATOR")
    gate_boot+=",emulator=$LUMA_OS_GATE_EMULATOR"
  fi
  gate_explicit_firmware=no
  if [ -n "${LUMA_OS_GATE_UEFI_CODE:-}${LUMA_OS_GATE_UEFI_VARS:-}" ]; then
    local path
    for path in "${LUMA_OS_GATE_UEFI_CODE:-}" "${LUMA_OS_GATE_UEFI_VARS:-}"; do
      [[ "$path" = /* && "$path" != *,* && -f "$path" && -r "$path" ]] ||
        { luma_os_die 'gate UEFI code and variable template must both be readable absolute file paths without commas'; return 1; }
    done
    # A separate writable NVRAM is created by libvirt for every disposable VM.
    gate_boot="loader=$LUMA_OS_GATE_UEFI_CODE,loader.readonly=yes,loader.type=pflash,nvram.template=$LUMA_OS_GATE_UEFI_VARS${LUMA_OS_GATE_EMULATOR:+,emulator=$LUMA_OS_GATE_EMULATOR}"
    gate_explicit_firmware=yes
  fi
}

luma_os_gate_vm_preflight() {
  local output=$1
  # virt-install consults global host capabilities before its explicit emulator
  # option; successful per-emulator domcaps alone does not admit this host.
  virsh --connect qemu:///system capabilities >"$output.host.xml"
  python3 - "$output.host.xml" "$LUMA_OS_ARCH" "$gate_virt" <<'PYHOST' || return
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
if not any(guest.findtext('os_type') == 'hvm' and
           guest.find(f"arch[@name='{sys.argv[2]}']/domain[@type='{sys.argv[3]}']") is not None
           for guest in root.findall('guest')):
    raise SystemExit('libvirt host capabilities do not advertise selected architecture/accelerator; provision its native-host emulator package before using virt-install')
PYHOST
  virsh --connect qemu:///system domcapabilities "${gate_caps_args[@]}" >"$output" ||
    { luma_os_die "libvirt cannot run $LUMA_OS_ARCH/$gate_machine with $gate_virt; provision its emulator or set LUMA_OS_GATE_EMULATOR"; return 1; }
  python3 - "$output" "$LUMA_OS_ARCH" "$gate_virt" "$gate_explicit_firmware" <<'PY' || return
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
if root.findtext('arch') != sys.argv[2] or root.findtext('domain') != sys.argv[3]:
    raise SystemExit('gate libvirt capabilities do not match selected architecture/accelerator')
if sys.argv[4] != 'yes' and 'efi' not in [v.text for v in root.findall("os/enum[@name='firmware']/value")]:
    raise SystemExit('gate UEFI firmware is unavailable; provision architecture firmware or set both LUMA_OS_GATE_UEFI_CODE and LUMA_OS_GATE_UEFI_VARS')
PY
  local inputs=()
  [ -z "${LUMA_OS_GATE_EMULATOR:-}" ] || inputs+=("$LUMA_OS_GATE_EMULATOR")
  [ "$gate_explicit_firmware" != yes ] || inputs+=("$LUMA_OS_GATE_UEFI_CODE" "$LUMA_OS_GATE_UEFI_VARS")
  [ "${#inputs[@]}" -eq 0 ] || sha256sum -- "${inputs[@]}" >"$output.inputs.sha256"
}

# Give an admitted media qualification its own names and disk tree. The optional
# scope is a release build ID, never an arbitrary path supplied by a caller.
luma_os_media_vm_run() {
  local timestamp=$1 scope=${LUMA_MEDIA_VM_SCOPE:-}
  [[ "$timestamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { luma_os_die 'invalid media VM timestamp'; return 1; }
  if [ -n "$scope" ]; then
    [[ "$scope" =~ ^[0-9]{8}\.[0-9]+$ ]] || { luma_os_die 'invalid media VM scope'; return 1; }
    printf '%s-%s\n' "$scope" "$timestamp"
  else
    printf '%s\n' "$timestamp"
  fi
}
