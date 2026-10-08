#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
build_user=${LUMA_BUILD_USER:-luma-build}
protected_vm=${LUMA_PROTECTED_VM:-viola-windows-builder}
storage_uuid=${LUMA_SECONDARY_STORAGE_UUID:-1136b935-7013-4f46-971b-0f0c876b83a7}
mount_root=${LUMA_SECONDARY_MOUNT_ROOT:-/mnt/luma-secondary}
offload_root=${LUMA_OFFLOAD_ROOT:-$mount_root/luma-offload}
fstab_path=${LUMA_FSTAB_PATH:-/etc/fstab}
fstab_options=${LUMA_SECONDARY_MOUNT_OPTIONS:-nofail,nodev,nosuid,noexec}
change_log="$build_root/bootstrap/system-changes.log"
bootstrap_copy="$build_root/bootstrap/bootstrap-secondary-offload-storage.sh"
environment_copy="$build_root/bootstrap/secondary-offload-storage.env"

if [ "$(id -u)" -ne 0 ]; then
  printf 'error: bootstrap must run as root\n' >&2
  exit 1
fi

for required_tool in blkid findmnt getent mount mountpoint readlink tune2fs virsh; do
  command -v "$required_tool" >/dev/null 2>&1 || {
    printf 'error: required tool is missing: %s\n' "$required_tool" >&2
    exit 1
  }
done

[ -r "$fstab_path" ] || {
  printf 'error: fstab is unavailable: %s\n' "$fstab_path" >&2
  exit 1
}
getent passwd "$build_user" >/dev/null || {
  printf 'error: dedicated build user is unavailable: %s\n' "$build_user" >&2
  exit 1
}
[ "$build_root" = /srv/luma-build ] || {
  printf 'error: this reviewed bootstrap is bound to /srv/luma-build\n' >&2
  exit 1
}
[ "$mount_root" = /mnt/luma-secondary ] || {
  printf 'error: this reviewed bootstrap is bound to /mnt/luma-secondary\n' >&2
  exit 1
}
[ "$offload_root" = "$mount_root/luma-offload" ] || {
  printf 'error: offload storage must remain below the reviewed mount root\n' >&2
  exit 1
}

vm_state=$(virsh domstate "$protected_vm" 2>/dev/null | tr -d '\r' || true)
case "$vm_state" in
  running|shut\ off) ;;
  *)
    printf 'error: protected VM %s is %s; stop and investigate without modifying it\n' \
      "$protected_vm" "${vm_state:-unavailable}" >&2
    exit 75
    ;;
esac

storage_device=$(blkid -U "$storage_uuid" 2>/dev/null || true)
[ -n "$storage_device" ] || {
  printf 'error: storage UUID is unavailable: %s\n' "$storage_uuid" >&2
  exit 1
}
storage_device=$(readlink -f "$storage_device")
[ "$(blkid -s TYPE -o value "$storage_device")" = ext4 ] || {
  printf 'error: secondary storage is not the reviewed ext4 filesystem\n' >&2
  exit 1
}

mounted_target=$(findmnt -rn -S "$storage_device" -o TARGET | head -n 1 || true)
if [ -n "$mounted_target" ] && [ "$mounted_target" != "$mount_root" ]; then
  printf 'error: reviewed secondary storage is already mounted at %s\n' \
    "$mounted_target" >&2
  exit 1
fi

if [ -z "$mounted_target" ]; then
  filesystem_state=$(tune2fs -l "$storage_device" | sed -n 's/^Filesystem state:[[:space:]]*//p')
  [ "$filesystem_state" = clean ] || {
    printf 'error: secondary filesystem state is %s; no repair was attempted\n' \
      "${filesystem_state:-unknown}" >&2
    exit 1
  }
fi

install -d -o root -g root -m 0755 "$build_root/bootstrap" "$mount_root"
touch "$change_log"
chmod 0644 "$change_log"
log_change() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$change_log"
}

fstab_line="UUID=$storage_uuid $mount_root ext4 $fstab_options 0 2"
if grep -Eq "^[^#].*[[:space:]]$mount_root[[:space:]]" "$fstab_path" && \
   ! grep -Fqx "$fstab_line" "$fstab_path"; then
  printf 'error: %s already has a different fstab entry\n' "$mount_root" >&2
  exit 1
fi
if grep -Eq "^[^#]*UUID=$storage_uuid[[:space:]]" "$fstab_path" && \
   ! grep -Fqx "$fstab_line" "$fstab_path"; then
  printf 'error: reviewed storage UUID already has a different fstab entry\n' >&2
  exit 1
fi

if ! grep -Fqx "$fstab_line" "$fstab_path"; then
  fstab_backup="$build_root/bootstrap/fstab.before-secondary-offload.$(date -u +%Y%m%dT%H%M%SZ)"
  install -o root -g root -m 0644 "$fstab_path" "$fstab_backup"
  printf '\n# Project Luma isolated secondary build archive\n%s\n' \
    "$fstab_line" >>"$fstab_path"
  systemctl daemon-reload
  log_change "added isolated secondary storage mount to $fstab_path; prior file retained at $fstab_backup"
fi

if ! mountpoint -q "$mount_root"; then
  mount "$mount_root"
  log_change "mounted UUID=$storage_uuid at $mount_root with $fstab_options"
fi

actual_source=$(findmnt -rn -T "$mount_root" -o SOURCE)
actual_uuid=$(blkid -s UUID -o value "$(readlink -f "$actual_source")" 2>/dev/null || true)
[ "$actual_uuid" = "$storage_uuid" ] || {
  printf 'error: mount verification failed for %s\n' "$mount_root" >&2
  exit 1
}

# These pre-existing roots prove that this is the reviewed filesystem. They are
# intentionally never chmodded, chowned, moved, scanned recursively, or removed.
for protected_path in \
  "$mount_root/viola-chromium-build" \
  "$mount_root/viola-chromium-152" \
  "$mount_root/EMERGENCY-BACKUP-2026-08-19"; do
  [ -e "$protected_path" ] || {
    printf 'error: reviewed protected path is missing: %s\n' "$protected_path" >&2
    exit 1
  }
done

if [ ! -d "$offload_root" ]; then
  install -d -o "$build_user" -g "$build_user" -m 0750 "$offload_root"
  log_change "created isolated Luma offload root $offload_root"
else
  [ "$(stat -c %U "$offload_root")" = "$build_user" ] && \
  [ "$(stat -c %G "$offload_root")" = "$build_user" ] || {
    printf 'error: existing offload root has unexpected ownership\n' >&2
    exit 1
  }
fi
install -d -o "$build_user" -g "$build_user" -m 0750 \
  "$offload_root/mac-build-archives" \
  "$offload_root/manifests"

if [ "$(readlink -f "$0")" != "$(readlink -m "$bootstrap_copy")" ]; then
  install -o root -g root -m 0755 "$0" "$bootstrap_copy"
else
  chown root:root "$bootstrap_copy"
  chmod 0755 "$bootstrap_copy"
fi
environment_temp=$(mktemp)
trap 'rm -f "$environment_temp"' EXIT
{
  printf 'LUMA_SECONDARY_STORAGE_UUID=%q\n' "$storage_uuid"
  printf 'LUMA_SECONDARY_MOUNT_ROOT=%q\n' "$mount_root"
  printf 'LUMA_OFFLOAD_ROOT=%q\n' "$offload_root"
  printf 'LUMA_SECONDARY_MOUNT_OPTIONS=%q\n' "$fstab_options"
} >"$environment_temp"
install -o root -g root -m 0644 "$environment_temp" "$environment_copy"

printf 'secondary storage ready\n'
printf '  source:  UUID=%s\n' "$storage_uuid"
printf '  mount:   %s\n' "$mount_root"
printf '  offload: %s\n' "$offload_root"
printf '  free:    %s\n' "$(df -h --output=avail "$offload_root" | tail -n 1 | xargs)"
printf '  Viola:   %s (unchanged)\n' "$vm_state"
