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
install -Dm644 "$root/config/desktop/dconf/db/luma.d/00-luma-desktop" /usr/share/luma-tester/profiles/desktop.dconf
install -Dm644 "$root/config/mobile/luma-shell-handheld.dconf" /usr/share/luma-tester/profiles/mobile.dconf
install -d /usr/share/gnome-shell/extensions/handheld@project-luma.local
cp -a "$root/extensions/luma-handheld/." /usr/share/gnome-shell/extensions/handheld@project-luma.local/
if [ -d /usr/share/gnome-shell/extensions/handheld@project-luma.local/schemas ]; then
  glib-compile-schemas /usr/share/gnome-shell/extensions/handheld@project-luma.local/schemas
fi
install -Dm755 "$root/scripts/tester/apply-profile" /usr/libexec/luma-tester-apply-profile
install -Dm644 "$root/config/tester/luma-tester-profile.service" /usr/lib/systemd/system/luma-tester-profile.service
install -d /etc/environment.d /etc/luma /etc/greetd /etc/systemd/system/greetd.service.d
printf '%s\n' "$target" >/etc/luma-tester-target
cat >/usr/local/bin/luma-tester-session <<'SESSION'
#!/bin/sh
IFS= read -r LUMA_DEVICE_CLASS </etc/luma-device-class
export LUMA_DEVICE_CLASS
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
After=cloud-final.service luma-tester-profile.service
Wants=cloud-final.service
Requires=luma-tester-profile.service
UNIT
cat >/etc/luma/tester-image <<IDENTITY
LUMA_TESTER_IMAGE_VERSION=1
LUMA_TESTER_TARGET=$target
LUMA_TESTER_BUILD=$build_id
LUMA_HARDWARE_BACKEND=virtual
LUMA_PHYSICAL_DEVICE=false
IDENTITY
cp /var/tmp/luma-tester-build/source-identity.json /etc/luma/tester-source.json
# An explicit developer identity; retain Fedora's vendor file and release RPM.
sed "s/@BUILD_ID@/$build_id/g" "$root/config/tester/os-release.in" >/etc/os-release.luma-tester
mv /etc/os-release.luma-tester /etc/os-release
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
/usr/libexec/luma-tester-apply-profile "$target"
systemctl disable gdm.service 2>/dev/null || true
systemctl enable --force sshd.service greetd.service luma-tester-profile.service
systemctl set-default graphical.target
# Remove the obsolete cloud web console instead of exposing another admin service.
systemctl disable cockpit.socket 2>/dev/null || true
