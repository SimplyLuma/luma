#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Deploy the persistent preview artifacts to the VM without touching /usr or
# the rpm-ostree deployment, then launch them in the logged-in Wayland session.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
preview_root="$repo_root/build/dev/files-preview"
guest=${LUMA_PREVIEW_GUEST:-luma@192.168.122.250}
guest_key=${LUMA_PREVIEW_GUEST_KEY:-$repo_root/build/provisioning/luma-m0-guest}
guest_root=/var/home/luma/.local/share/project-luma/files-preview

libadwaita="$preview_root/builds/libadwaita/src/libadwaita-1.so.0"
nautilus="$preview_root/builds/nautilus/src/nautilus"
gtk4="$repo_root/build/dev/gtk4-preview/builds/gtk4/gtk/libgtk-4.so.1.2200.4"

test -f "$libadwaita" || {
  printf 'error: preview libadwaita has not been built\n' >&2
  exit 1
}
test -x "$nautilus" || {
  printf 'error: preview Files binary has not been built\n' >&2
  exit 1
}
test -f "$guest_key" || {
  printf 'error: preview guest key is missing: %s\n' "$guest_key" >&2
  exit 1
}

ssh_options=(-i "$guest_key" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

ssh "${ssh_options[@]}" "$guest" \
  "install -d -m 0755 '$guest_root/lib' '$guest_root/bin'"
# Upload alongside the running files. Replacing the active executable in place
# can fail with ETXTBSY and rewriting a mapped shared library is unsafe.
scp "${ssh_options[@]}" "$libadwaita" "$guest:$guest_root/lib/libadwaita-1.so.0.next"
scp "${ssh_options[@]}" "$nautilus" "$guest:$guest_root/bin/nautilus.next"
if [ -f "$gtk4" ]; then
  scp "${ssh_options[@]}" "$gtk4" "$guest:$guest_root/lib/libgtk-4.so.1.next"
fi

gtk4_activate=
if [ -f "$gtk4" ]; then
  gtk4_activate="mv '$guest_root/lib/libgtk-4.so.1.next' '$guest_root/lib/libgtk-4.so.1'"
fi

ssh "${ssh_options[@]}" "$guest" "
  export XDG_RUNTIME_DIR=/run/user/1000
  export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
  nautilus -q || true
  systemctl --user stop luma-files-preview.service >/dev/null 2>&1 || true
  mv '$guest_root/lib/libadwaita-1.so.0.next' '$guest_root/lib/libadwaita-1.so.0'
  $gtk4_activate
  mv '$guest_root/bin/nautilus.next' '$guest_root/bin/nautilus'
  chmod 0755 '$guest_root/bin/nautilus'
  gdbus call --session --dest org.gnome.ScreenSaver \
    --object-path /org/gnome/ScreenSaver \
    --method org.gnome.ScreenSaver.SetActive false >/dev/null
  systemd-run --user --unit=luma-files-preview --collect --no-block --quiet \
    --setenv=XDG_RUNTIME_DIR=/run/user/1000 \
    --setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    --setenv=XDG_SESSION_TYPE=wayland \
    --setenv=WAYLAND_DISPLAY=wayland-0 \
    --setenv=LD_LIBRARY_PATH='$guest_root/lib' \
    '$guest_root/bin/nautilus' --new-window /var/home/luma/Documents
"

printf 'Development-only Files preview launched from %s\n' "$guest_root"
