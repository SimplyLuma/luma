#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
[ "$#" = 2 ]
target=$1
build_id=$2
case "$target" in mobile) device_class=handheld ;; desktop) device_class=desktop ;; *) exit 2 ;; esac
root=/var/tmp/luma-tester-build/source
install -Dm755 "$root/scripts/mobile/luma-shell-session" /usr/local/bin/luma-shell-session
install -Dm644 "$root/config/mobile/fp6-physical/overlay/usr/lib/systemd/user/luma-shell-session.target" /usr/lib/systemd/user/luma-shell-session.target
install -Dm644 "$root/config/desktop/dconf/profile/user" /etc/dconf/profile/user
install -Dm644 "$root/config/desktop/dconf/db/luma.d/00-luma-desktop" /etc/dconf/db/luma.d/00-luma-desktop
if [ "$target" = mobile ]; then
  install -Dm644 "$root/config/mobile/luma-shell-handheld.dconf" /etc/dconf/db/luma.d/10-luma-handheld
  install -d /usr/share/gnome-shell/extensions/handheld@project-luma.local
  cp -a "$root/extensions/luma-handheld/." /usr/share/gnome-shell/extensions/handheld@project-luma.local/
  if [ -d /usr/share/gnome-shell/extensions/handheld@project-luma.local/schemas ]; then
    glib-compile-schemas /usr/share/gnome-shell/extensions/handheld@project-luma.local/schemas
  fi
fi
printf '%s\n' "$device_class" >/etc/luma-device-class
install -d /etc/environment.d /etc/luma /etc/greetd /etc/systemd/system/greetd.service.d
cat >/etc/environment.d/90-luma-tester.conf <<ENV
LUMA_DEVICE_CLASS=$device_class
LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
ENV
cat >/usr/local/bin/luma-tester-session <<SESSION
#!/bin/sh
export LUMA_DEVICE_CLASS=$device_class
export LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
exec /usr/local/bin/luma-shell-session
SESSION
chmod 755 /usr/local/bin/luma-tester-session
cat >/etc/greetd/config.toml <<'GREETD'
[terminal]
vt = 1
[initial_session]
command = "/usr/local/bin/luma-tester-session"
user = "luma"
[default_session]
command = "agreety --cmd /usr/local/bin/luma-tester-session"
user = "greetd"
GREETD
# The new seed must create/authorize luma before graphical autologin starts.
cat >/etc/systemd/system/greetd.service.d/cloud-init.conf <<'UNIT'
[Unit]
After=cloud-final.service
Wants=cloud-final.service
UNIT
cat >/etc/luma/tester-image <<IDENTITY
LUMA_TESTER_IMAGE_VERSION=1
LUMA_TESTER_TARGET=$target
LUMA_TESTER_BUILD=$build_id
LUMA_HARDWARE_BACKEND=virtual
LUMA_PHYSICAL_DEVICE=false
IDENTITY
cp /var/tmp/luma-tester-build/source-identity.json /etc/luma/tester-source.json
rpm -qa --qf '%{NEVRA}\n' | sort >/etc/luma/tester-packages.txt
# The preview stays awake/unlocked; it contains only a disposable developer profile.
cat >/etc/dconf/db/luma.d/90-luma-tester <<'DCONF'
[org/gnome/desktop/session]
idle-delay=uint32 0
[org/gnome/desktop/screensaver]
lock-enabled=false
[org/gnome/settings-daemon/plugins/power]
sleep-inactive-ac-type='nothing'
DCONF
dconf update
systemctl enable sshd.service greetd.service
systemctl set-default graphical.target
# Remove the obsolete cloud web console instead of exposing another admin service.
systemctl disable cockpit.socket 2>/dev/null || true
