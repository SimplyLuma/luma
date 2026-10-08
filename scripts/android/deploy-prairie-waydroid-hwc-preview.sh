#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install one rebuilt Waydroid hardware-composer module into Waydroid's
# documented vendor overlay. This is the fast visual-acceptance lane; release
# images still receive the same source through the coherent image build.

set -euo pipefail

source_module=${1:-}
expected_sha=${2:-}
overlay_root=${LUMA_WAYDROID_OVERLAY_ROOT:-/var/lib/waydroid/overlay}
destination=$overlay_root/vendor/lib64/hw/hwcomposer.waydroid.so
backup_root=/var/lib/waydroid/luma-preview-backups
result_file=${LUMA_WAYDROID_PREVIEW_RESULT:-/tmp/luma-hwc-preview.result}

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'error: preview deployment must run as root\n' >&2
  exit 1
fi
if [[ -z $source_module || -z $expected_sha || ! -f $source_module ]]; then
  printf 'usage: %s MODULE EXPECTED_SHA256\n' "$0" >&2
  exit 2
fi
if [[ $(sha256sum "$source_module" | awk '{print $1}') != "$expected_sha" ]]; then
  printf 'error: source module does not match the expected digest\n' >&2
  exit 2
fi

install -d -m 0755 "$(dirname -- "$destination")" "$backup_root"
backup=
if [[ -f $destination ]]; then
  backup=$backup_root/hwcomposer.waydroid.$(date -u +%Y%m%dT%H%M%SZ).so
  install -m 0755 "$destination" "$backup"
fi

rollback() {
  printf 'FAILED\n' >"$result_file"
  systemctl stop waydroid-container.service >/dev/null 2>&1 || true
  if [[ -n $backup ]]; then
    install -m 0755 "$backup" "$destination"
  else
    rm -f -- "$destination"
  fi
  restorecon -F "$destination" >/dev/null 2>&1 || true
  systemctl start waydroid-container.service >/dev/null 2>&1 || true
}
trap rollback ERR INT TERM

systemctl stop waydroid-container.service
install -m 0755 "$source_module" "$destination"
restorecon -F "$destination" >/dev/null 2>&1 || true
sync -f "$destination"
systemctl start waydroid-container.service

for _ in {1..20}; do
  if [[ $(lxc-info -P /var/lib/waydroid/lxc -n waydroid -sH 2>/dev/null || true) == RUNNING ]]; then
    break
  fi
  sleep 0.25
done
container_state=$(lxc-info -P /var/lib/waydroid/lxc -n waydroid -sH 2>/dev/null || true)
active_sha=PENDING_SESSION
input_policy=PENDING_SESSION
if [[ $container_state == RUNNING ]]; then
  active_sha=$(lxc-attach -P /var/lib/waydroid/lxc -n waydroid --clear-env -- \
    /system/bin/sha256sum /vendor/lib64/hw/hwcomposer.waydroid.so | awk '{print $1}')
  if [[ $active_sha != "$expected_sha" ]]; then
    printf 'error: running Android container did not mount the preview module\n' >&2
    false
  fi

  # Desktop/tablet keep Mutter as the pointer and input-method owner. Handheld
  # compositions intentionally retain Android's touch keyboard policy.
  device_class=$(cat /etc/luma-device-class 2>/dev/null || printf desktop)
  if [[ $device_class == desktop ]]; then
    lxc-attach -P /var/lib/waydroid/lxc -n waydroid --clear-env -- \
      /system/bin/settings put secure show_ime_with_hard_keyboard 0
  fi
  input_policy=$(lxc-attach -P /var/lib/waydroid/lxc -n waydroid --clear-env -- \
    /system/bin/settings get secure show_ime_with_hard_keyboard)
fi

trap - ERR INT TERM
printf 'Prairie Waydroid preview active: %s\n' "$active_sha"
printf 'Android input policy: %s\n' "$input_policy"
printf 'ACTIVE_SHA256=%s\nINPUT_POLICY=%s\n' \
  "$active_sha" "$input_policy" >"$result_file"
