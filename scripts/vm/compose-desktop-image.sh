#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [ "$#" -ne 2 ]; then
  printf 'usage: %s /absolute/path/to/base.qcow2 /absolute/path/to/output.qcow2\n' "$0" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

base_image=$(realpath "$1")
output_image=$2
case "$output_image" in
  /*) : ;;
  *) output_image="$PWD/$output_image" ;;
esac
output_dir=$(dirname -- "$output_image")
vm_name="luma-compose-$(date -u +%Y%m%dT%H%M%SZ)"
vm_dir=${LUMA_VM_DIR:-/var/lib/libvirt/images/luma}
qemu_user=${LUMA_LIBVIRT_QEMU_USER:-qemu}
qemu_group=${LUMA_LIBVIRT_QEMU_GROUP:-qemu}
work_disk="$vm_dir/$vm_name.qcow2"
guest_key="$repo_root/build/provisioning/luma-m0-guest"
guest_password_file="$repo_root/build/provisioning/luma-m0-password"
gnome_shell_rpm="$repo_root/build/packages/gnome-shell/RPMS/x86_64/$GNOME_SHELL_NEVRA.rpm"
gnome_shell_common_rpm="$repo_root/build/packages/gnome-shell/RPMS/noarch/$GNOME_SHELL_COMMON_NEVRA.rpm"
gcc_rpm="$repo_root/build/packages/gnome-control-center/RPMS/x86_64/gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.x86_64.rpm"
gcc_filesystem_rpm="$repo_root/build/packages/gnome-control-center/RPMS/noarch/gnome-control-center-filesystem-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.noarch.rpm"
gtk4_rpm="$repo_root/build/packages/gtk4/RPMS/x86_64/$GTK4_NEVRA.rpm"
gtk3_rpm="$repo_root/build/packages/gtk3/RPMS/x86_64/$GTK3_NEVRA.rpm"
libhandy_rpm="$repo_root/build/packages/libhandy/RPMS/x86_64/$LIBHANDY_NEVRA.rpm"
libadwaita_rpm="$repo_root/build/packages/libadwaita/RPMS/x86_64/$LIBADWAITA_NEVRA.rpm"
nautilus_rpm="$repo_root/build/packages/nautilus/RPMS/x86_64/$NAUTILUS_NEVRA.rpm"
nautilus_extensions_rpm="$repo_root/build/packages/nautilus/RPMS/x86_64/$NAUTILUS_EXTENSIONS_NEVRA.rpm"
terminal_rpm="$repo_root/build/packages/luma-terminal/RPMS/noarch/$LUMA_TERMINAL_NEVRA.rpm"
calculator_rpm="$repo_root/build/packages/luma-calculator/RPMS/noarch/$LUMA_CALCULATOR_NEVRA.rpm"
disks_rpm="$repo_root/build/packages/luma-disks/RPMS/noarch/$LUMA_DISKS_NEVRA.rpm"
tiling_shell_rpm="$repo_root/build/packages/tiling/RPMS/noarch/$TILING_SHELL_NEVRA.rpm"
tiling_toggle_rpm="$repo_root/build/packages/tiling/RPMS/noarch/$LUMA_TILING_TOGGLE_NEVRA.rpm"
backgrounds_rpm="$repo_root/build/packages/backgrounds/RPMS/noarch/$LUMA_BACKGROUNDS_NEVRA.rpm"
boot_theme_rpm="$repo_root/build/packages/boot-theme/RPMS/noarch/$LUMA_BOOT_THEME_NEVRA.rpm"
plymouth_rpm_dir="$repo_root/build/packages/plymouth/RPMS/x86_64"
prairie_icon_theme_rpm="$repo_root/build/packages/prairie-icon-theme/RPMS/noarch/$PRAIRIE_ICON_THEME_NEVRA.rpm"
launcher_policy_rpm="$repo_root/build/packages/luma-desktop-launcher-policy/RPMS/noarch/$LUMA_DESKTOP_LAUNCHER_POLICY_NEVRA.rpm"
homebrew_rpm="$repo_root/build/packages/luma-homebrew/RPMS/noarch/$LUMA_HOMEBREW_NEVRA.rpm"
figtree_rpm="$repo_root/build/packages/figtree-fonts/RPMS/noarch/$FIGTREE_NEVRA.rpm"
luma_developer_platform_rpm="$repo_root/build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
prairie_core_apps_rpm="$repo_root/build/packages/prairie-core-apps/RPMS/noarch/$PRAIRIE_CORE_APPS_NEVRA.rpm"
messages_e2ee_rpm="$repo_root/build/packages/luma-messages-e2ee/RPMS/x86_64/$LUMA_MESSAGES_E2EE_NEVRA.rpm"
luma_developer_platform_rpm="$repo_root/build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm"
luma_quick_view_rpm="$repo_root/build/packages/luma-quick-view/RPMS/x86_64/$LUMA_QUICK_VIEW_NEVRA.rpm"
luma_monitor_rpm="$repo_root/build/packages/luma-monitor/RPMS/noarch/$LUMA_MONITOR_NEVRA.rpm"
luma_tide_rpm="$repo_root/build/packages/luma-tide/RPMS/noarch/$LUMA_TIDE_NEVRA.rpm"
luma_displays_rpm="$repo_root/build/packages/luma-displays/RPMS/noarch/$LUMA_DISPLAYS_NEVRA.rpm"
luma_ari_rpm="$repo_root/build/packages/luma-ari/RPMS/noarch/$LUMA_ARI_NEVRA.rpm"
luma_ari_runtime_rpm="$repo_root/build/packages/luma-ari-runtime/x86_64/RPMS/$LUMA_ARI_RUNTIME_NEVRA.rpm"
luma_darkroom_rpm="$repo_root/build/packages/luma-darkroom/RPMS/noarch/$LUMA_DARKROOM_NEVRA.rpm"
shell_state_rpm="$repo_root/build/packages/luma-shell-state/RPMS/noarch/$LUMA_SHELL_STATE_NEVRA.rpm"
android_runtime_rpm="$repo_root/build/packages/luma-android-runtime/RPMS/noarch/$LUMA_ANDROID_RUNTIME_NEVRA.rpm"
shell_state_rpm="$repo_root/build/packages/luma-shell-state/RPMS/noarch/$LUMA_SHELL_STATE_NEVRA.rpm"
search_rpm="$repo_root/build/packages/luma-search/RPMS/noarch/$LUMA_SEARCH_NEVRA.rpm"
luma_mods_rpm="$repo_root/build/packages/luma-mods/RPMS/noarch/$LUMA_MODS_NEVRA.rpm"
luma_update_rpm="$repo_root/build/packages/luma-update/RPMS/noarch/$LUMA_UPDATE_NEVRA.rpm"
relay_rpm="$repo_root/build/packages/luma-relay/RPMS/noarch/$LUMA_RELAY_NEVRA.rpm"
application_installer_rpm="$repo_root/build/packages/luma-application-installer/RPMS/noarch/$LUMA_APPLICATION_INSTALLER_NEVRA.rpm"
apk_metadata_rpm="$repo_root/build/packages/luma-apk-metadata/x86_64/$LUMA_APK_METADATA_X86_64_NEVRA.rpm"
wine_relay_explorer_rpm="$repo_root/build/packages/wine-relay-integration/RPMS/x86_64/$WINE_RELAY_EXPLORER_NEVRA.rpm"
defined=0
passed=0

luma_repo_dir="$repo_root/build/luma-package-repository"
luma_repo_archive="$repo_root/build/luma-package-repository.tar"
luma_repo_definition="$repo_root/config/desktop/luma-desktop.repo"

for tool in getent python3 scp sha256sum ssh sudo tar virt-install virsh; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required desktop-composition tool is missing: %s\n' "$tool" >&2
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

for path in "$base_image" "$guest_key" "$guest_password_file" \
  "$gnome_shell_rpm" "$gnome_shell_common_rpm" \
  "$gcc_rpm" "$gcc_filesystem_rpm" "$gtk4_rpm" "$gtk3_rpm" "$libhandy_rpm" "$libadwaita_rpm" "$nautilus_rpm" \
  "$nautilus_extensions_rpm" "$calculator_rpm" "$terminal_rpm" "$disks_rpm" \
  "$tiling_shell_rpm" \
  "$tiling_toggle_rpm" "$backgrounds_rpm" "$prairie_icon_theme_rpm" "$launcher_policy_rpm" "$homebrew_rpm" \
  "$boot_theme_rpm" "$figtree_rpm" "$luma_developer_platform_rpm" \
  "$prairie_core_apps_rpm" "$messages_e2ee_rpm" "$luma_tide_rpm" "$luma_displays_rpm" "$luma_ari_rpm" "$luma_ari_runtime_rpm" "$luma_monitor_rpm" "$luma_quick_view_rpm" "$luma_darkroom_rpm" "$search_rpm" "$shell_state_rpm" "$luma_mods_rpm" "$relay_rpm" \
  "$application_installer_rpm" \
  "$apk_metadata_rpm" \
  "$luma_update_rpm" \
  "$luma_repo_definition" \
  "$wine_relay_explorer_rpm"; do
  if [ ! -f "$path" ]; then
    printf 'error: required desktop-composition input is missing: %s\n' "$path" >&2
    exit 1
  fi
done

if [ ! -f "$android_runtime_rpm" ]; then
  printf 'error: required desktop-composition input is missing: %s\n' \
    "$android_runtime_rpm" >&2
  exit 1
fi

if [ -e "$output_image" ]; then
  printf 'error: refusing to replace existing output image: %s\n' "$output_image" >&2
  exit 1
fi

# Luma packages are installed from an rpm-md repository rather than from local
# file paths, so the composed base commit records their origin and they stay
# replaceable after the image ships. Build it before anything touches libvirt:
# a pin that has not been built must fail here, not inside the guest.
# See docs/design/luma-package-repository.md.
"$repo_root/scripts/vm/build-luma-package-repository.sh" "$luma_repo_dir"
rm -f -- "$luma_repo_archive"
tar -cf "$luma_repo_archive" -C "$luma_repo_dir" .

mkdir -p "$output_dir"
sudo install -d -m 0755 "$vm_dir"
sudo cp --reflink=auto --sparse=always "$base_image" "$work_disk"

cleanup() {
  if [ "$defined" -eq 1 ]; then
    sudo virsh destroy "$vm_name" >/dev/null 2>&1 || true
    sudo virsh undefine "$vm_name" --nvram --tpm >/dev/null 2>&1 ||
      sudo virsh undefine "$vm_name" --nvram >/dev/null 2>&1 || true
  fi
  if [ "$passed" -ne 1 ]; then
    rm -f -- "$output_image.deployment.json"
    printf 'Desktop composition failed; diagnostic disk retained: %s\n' \
      "$work_disk" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

wait_for_shutoff() {
  wait_seconds=$1
  deadline=$((SECONDS + wait_seconds))
  state=
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(sudo virsh domstate "$vm_name" 2>/dev/null || true)
    if [ "$state" = 'shut off' ]; then
      return 0
    fi
    sleep 2
  done
  printf 'error: composition guest did not power off cleanly\n' >&2
  return 1
}

wait_for_guest_agent() {
  wait_seconds=$1
  deadline=$((SECONDS + wait_seconds))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if sudo virsh qemu-agent-command "$vm_name" \
      '{"execute":"guest-ping"}' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  printf 'error: QEMU guest agent did not become ready\n' >&2
  return 1
}

# SSH is injected only into this writable build disk to reach the first boot.
# It is disabled again before the disk becomes an artifact.
"$repo_root/scripts/vm/enable-track-a-lab-ssh.sh" "$work_disk"
sudo chown "$qemu_user:$qemu_group" "$work_disk"
sudo chmod 0600 "$work_disk"
sudo restorecon -F "$work_disk" >/dev/null 2>&1 || true

sudo virt-install \
  --connect qemu:///system \
  --name "$vm_name" \
  --description 'Project Luma deterministic desktop image composition worker' \
  --machine q35 \
  --cpu host-passthrough \
  --vcpus 4,sockets=1,cores=4,threads=1 \
  --memory 8192 \
  --import \
  --disk "path=$work_disk,format=qcow2,bus=virtio,cache=none,discard=unmap" \
  --network network=default,model=virtio \
  --boot uefi \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
  --rng /dev/urandom \
  --graphics vnc,listen=127.0.0.1 \
  --video virtio \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --console pty,target.type=serial \
  --os-variant fedora-unknown \
  --noautoconsole
defined=1

LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" true

guest_ip=$(sudo virsh domifaddr "$vm_name" --source lease |
  awk '/ipv4/ {sub("/.*", "", $4); print $4; exit}')
if [ -z "$guest_ip" ]; then
  printf 'error: composition guest has no libvirt lease\n' >&2
  exit 1
fi

scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$gnome_shell_rpm" "$gnome_shell_common_rpm" \
  "$gcc_rpm" "$gcc_filesystem_rpm" "$gtk4_rpm" "$gtk3_rpm" "$libhandy_rpm" "$libadwaita_rpm" "$nautilus_rpm" \
  "$nautilus_extensions_rpm" "$calculator_rpm" "$terminal_rpm" "$disks_rpm" \
  "$tiling_shell_rpm" \
  "$tiling_toggle_rpm" "$backgrounds_rpm" "$prairie_icon_theme_rpm" "$launcher_policy_rpm" "$homebrew_rpm" \
  "$figtree_rpm" "$luma_developer_platform_rpm" "$prairie_core_apps_rpm" "$messages_e2ee_rpm" \
  "$shell_state_rpm" "$search_rpm" "$luma_tide_rpm" "$luma_displays_rpm" "$luma_ari_rpm" "$luma_ari_runtime_rpm" "$luma_monitor_rpm" "$luma_quick_view_rpm" "$luma_darkroom_rpm" \
  "$luma_mods_rpm" \
  "$luma_update_rpm" \
  "$android_runtime_rpm" \
  "$relay_rpm" \
  "$application_installer_rpm" \
  "$apk_metadata_rpm" \
  "$wine_relay_explorer_rpm" \
  "$boot_theme_rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_CORE_LIBS_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_GRAPHICS_LIBS_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_PLUGIN_SCRIPT_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_SCRIPTS_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_PLUGIN_LABEL_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_PLUGIN_TWO_STEP_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_SYSTEM_THEME_NEVRA.rpm" \
  "$plymouth_rpm_dir/$PLYMOUTH_THEME_SPINNER_NEVRA.rpm" \
  "$repo_root/config/desktop/dconf/profile/user" \
  "$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop" \
  "$repo_root/config/desktop/dconf/db/gdm.d/00-prairie-login" \
  "$repo_root/config/shared/application-packages.txt" \
  "$repo_root/config/shared/excluded-background-packages.txt" \
  "$repo_root/config/os/removed-packages.txt" \
  "$repo_root/config/desktop/system-flatpak-replacements.txt" \
  "$repo_root/config/desktop/inputs.env" \
  "$luma_repo_definition" \
  "$repo_root/config/desktop/useradd" \
  "$repo_root/config/boot/plymouthd.conf" \
  "$repo_root/config/desktop/fprintd-no-idle-exit.conf" \
  "$repo_root/config/desktop/waydroid-container-ordering.conf" \
  "$repo_root/config/desktop/wireplumber/60-luma-bluetooth-roles.conf" \
  "$repo_root/scripts/vm/provision-desktop-image.sh" \
  "luma@$guest_ip:/tmp/"

# Desktop-only application roles travel under a distinct name so they cannot be
# confused with the shared role list that mobile composition also consumes.
scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$repo_root/config/desktop/application-packages.txt" \
  "luma@$guest_ip:/tmp/desktop-application-packages.txt"

# Both files are named settings.ini in the tree (mirroring their /etc
# destinations), so each needs its own rename on the way to a flat /tmp.
scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$repo_root/config/desktop/gtk-3.0/settings.ini" \
  "luma@$guest_ip:/tmp/gtk-3.0-settings.ini"
scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$repo_root/config/desktop/gtk-4.0/settings.ini" \
  "luma@$guest_ip:/tmp/gtk-4.0-settings.ini"

# /tmp is cleared during the first boot into the staged rpm-ostree deployment.
# Keep the one post-boot helper in /var/tmp, whose defined purpose includes
# retaining temporary build inputs across a reboot.
scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$repo_root/config/boot/apply-grub-policy.sh" \
  "luma@$guest_ip:/var/tmp/apply-grub-policy.sh"

# The package repository is too large for the guest's tmpfs /tmp and must land
# on disk: the provisioner unpacks it into /var/lib/luma/package-repository,
# where it stays as part of the composed image.
scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$luma_repo_archive" \
  "luma@$guest_ip:/var/tmp/luma-package-repository.tar"

guest_password=$(<"$guest_password_file")
printf '%s\n' "$guest_password" |
  LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    sudo -S -- bash /tmp/provision-desktop-image.sh

sudo virsh shutdown "$vm_name" >/dev/null
wait_for_shutoff 180
sudo virsh start "$vm_name" >/dev/null

# The first boot finalizes rpm-ostree's staged deployment. Use Fedora's
# existing guest agent as the completion gate; SSH remains a lab concern and
# is not made part of the product deployment.
wait_for_guest_agent 300
sudo virsh shutdown "$vm_name" --mode agent >/dev/null
wait_for_shutoff 180
"$repo_root/scripts/vm/enable-track-a-lab-ssh.sh" "$work_disk"
sudo restorecon -F "$work_disk" >/dev/null 2>&1 || true
sudo virsh start "$vm_name" >/dev/null

LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" LUMA_GUEST_TIMEOUT=420 \
  "$repo_root/scripts/vm/guest-ssh.sh" true

# The boot-theme payload becomes visible only after the staged rpm-ostree
# deployment has booted. Apply its boot-filesystem policy from that deployment,
# never from the pre-reboot base tree.
printf '%s\n' "$guest_password" |
  LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    sudo -S -- bash /var/tmp/apply-grub-policy.sh

LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    rm -f -- /var/tmp/apply-grub-policy.sh

scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$repo_root/tests/smoke/desktop.sh" \
  "luma@$guest_ip:/var/tmp/luma-desktop-smoke.sh"
scp -q \
  -i "$guest_key" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$repo_root/config/desktop/inputs.env" \
  "luma@$guest_ip:/var/tmp/luma-desktop-inputs.env"

# The acceptance suite inspects the EFI/boot policy as well as ordinary
# product files. Run the complete, immutable test payload with the authority
# required to read those root-owned boot artifacts.
printf '%s\n' "$guest_password" |
  LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    sudo -S -- bash /var/tmp/luma-desktop-smoke.sh

# Bind the distributable disk to the one deployment that actually passed the
# post-reboot acceptance suite. The publisher refuses an image without this
# sidecar and never guesses from directory ordering inside an offline disk.
LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    rpm-ostree status --json >"$output_image.deployment.json"
python3 - "$output_image.deployment.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
booted = [item for item in data.get("deployments", []) if item.get("booted")]
if len(booted) != 1:
    raise SystemExit("accepted image must report exactly one booted deployment")
if not re.fullmatch(r"[0-9a-f]{64}", booted[0].get("checksum", "")):
    raise SystemExit("accepted image reports an invalid booted deployment checksum")
PY

LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    rm -f -- /var/tmp/luma-desktop-smoke.sh /var/tmp/luma-desktop-inputs.env

printf '%s\n' "$guest_password" |
  LUMA_VM_NAME="$vm_name" LUMA_GUEST_KEY="$guest_key" \
  "$repo_root/scripts/vm/guest-ssh.sh" \
    sudo -S -- rpm-ostree cleanup -r

sudo virsh shutdown "$vm_name" >/dev/null
wait_for_shutoff 180
"$repo_root/scripts/vm/disable-track-a-lab-ssh.sh" "$work_disk"

sudo virsh undefine "$vm_name" --nvram --tpm >/dev/null 2>&1 ||
  sudo virsh undefine "$vm_name" --nvram >/dev/null
defined=0
sudo mv "$work_disk" "$output_image"
sudo chown "$(id -u):$(id -g)" "$output_image"
sha256sum "$output_image" >"$output_image.sha256"
passed=1
printf 'Luma desktop image composed: %s\n' "$output_image"
