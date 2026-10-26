#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
[ "$#" = 2 ]
target=$1
build_id=$2
case "$target" in
  desktop) device_class=desktop ;;
  *) printf 'error: this producer is qualified for desktop only; Mobile requires the phone shell profile\n' >&2; exit 2 ;;
esac
root=/var/tmp/luma-tester-build/source
# Give this virtual display an honest, local Luma identity and a roomy default.
# Kernel firmware override keeps the identity consistent in Settings and apps;
# the physical OS profiles and the bundled QEMU implementation stay untouched.
install -d /usr/lib/firmware/luma /etc/dracut.conf.d /etc/udev/hwdb.d
python3 "$root/scripts/tester/desktop-edid.py" /usr/lib/firmware/luma/tester-desktop.bin
cat >/etc/udev/hwdb.d/90-luma-tester-display.hwdb <<'HWDB'
# Local virtual-device identity, scoped to this disposable developer image.
acpi:LMA:*
 ID_VENDOR_FROM_DATABASE=Luma
HWDB
systemd-hwdb update
printf '%s\n' 'install_items+=" /usr/lib/firmware/luma/tester-desktop.bin "' >/etc/dracut.conf.d/90-luma-tester-display.conf
grubby --update-kernel=ALL --args='drm.edid_firmware=Virtual-1:luma/tester-desktop.bin'
dracut --force --regenerate-all
# Retire the prototype from earlier tester images as well as fresh exports.
rm -rf /usr/share/gnome-shell/extensions/handheld@project-luma.local
rm -f /usr/share/luma-tester/profiles/mobile.dconf \
  /usr/share/applications/org.project_luma.MobileInputSettings.desktop \
  /usr/libexec/luma-mobile-input-settings \
  /usr/share/glib-2.0/schemas/org.project_luma.handheld.gschema.xml
install -Dm755 "$root/scripts/mobile/luma-shell-session" /usr/local/bin/luma-shell-session
install -Dm644 "$root/config/mobile/fp6-physical/overlay/usr/lib/systemd/user/luma-shell-session.target" /usr/lib/systemd/user/luma-shell-session.target
install -Dm644 "$root/config/desktop/dconf/profile/user" /etc/dconf/profile/user
install -Dm644 "$root/config/desktop/dconf/db/luma.d/00-luma-desktop" /usr/share/luma-tester/profiles/desktop.dconf
install -d /usr/share/gnome-shell/extensions/tester@project-luma.local
cp -a "$root/extensions/luma-tester/." /usr/share/gnome-shell/extensions/tester@project-luma.local/
# Source archives are extracted under a private builder umask. Installed Shell
# extensions must be traversable and readable by the graphical user.
chmod -R a+rX /usr/share/gnome-shell/extensions/tester@project-luma.local
glib-compile-schemas /usr/share/glib-2.0/schemas
install -Dm755 "$root/scripts/tester/apply-profile" /usr/libexec/luma-tester-apply-profile
install -Dm644 "$root/config/tester/luma-tester-profile.service" /usr/lib/systemd/system/luma-tester-profile.service
install -d /etc/environment.d /etc/luma /etc/greetd /etc/systemd/system/greetd.service.d
printf '%s\n' "$target" >/etc/luma-tester-target
cat >/usr/local/bin/luma-tester-session <<'SESSION'
#!/bin/sh
IFS= read -r LUMA_DEVICE_CLASS </etc/luma-device-class
export LUMA_DEVICE_CLASS
# This disposable preview deliberately disables locking in its profile. Record
# that configured state before Shell starts, avoiding its first-login GDM hint.
preview_shell_data=${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell
mkdir -p "$preview_shell_data"
: >"$preview_shell_data/lock-warning-shown"
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
install -m644 /var/tmp/luma-tester-build/source-identity.json /etc/luma/tester-source.json
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
