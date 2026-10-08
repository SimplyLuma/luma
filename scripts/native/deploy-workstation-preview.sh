#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install a coherent Luma desktop package bundle on a mutable Fedora 44
# Workstation host. This is a physical preview lane, not the release image
# composition path; release media remains Fedora Silverblue/OSTree based.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: sudo %s /absolute/path/to/luma-preview-bundle\n' "$0" >&2
  exit 2
fi

bundle=$(realpath "$1")
rpm_dir="$bundle/rpms"
config_dir="$bundle/config"
state_dir=/var/lib/luma-preview
stamp=$(date -u +%Y%m%dT%H%M%SZ)

[[ $EUID -eq 0 ]] || {
  printf 'error: run this deployment as root\n' >&2
  exit 1
}
[[ -d $rpm_dir ]] || {
  printf 'error: missing RPM directory: %s\n' "$rpm_dir" >&2
  exit 1
}
[[ -f $config_dir/dconf-profile-user ]] || {
  printf 'error: incomplete Luma configuration bundle\n' >&2
  exit 1
}
[[ $(. /etc/os-release; printf '%s' "$ID:$VERSION_ID") == fedora:44 ]] || {
  printf 'error: the mutable preview lane requires Fedora 44\n' >&2
  exit 1
}
findmnt -no OPTIONS / | grep -qw rw || {
  printf 'error: this helper is only for a writable Workstation preview host\n' >&2
  exit 1
}

mapfile -t rpms < <(find "$rpm_dir" -maxdepth 1 -type f -name '*.rpm' -print | sort)
[[ ${#rpms[@]} -gt 0 ]] || {
  printf 'error: the preview bundle contains no RPMs\n' >&2
  exit 1
}

install -d -m 0755 "$state_dir/history/$stamp"
rpm -qa | sort >"$state_dir/history/$stamp/rpm-before.txt"
dnf history list >"$state_dir/history/$stamp/dnf-history-before.txt"
cp -a "$bundle/manifest.txt" "$state_dir/history/$stamp/manifest.txt"

# DNF keeps this as one recorded transaction. --allow-downgrade is required
# because several deliberately patched Luma packages track a pinned Fedora
# source release whose numeric RPM release may be below a later Fedora update.
dnf -y install --refresh --allow-downgrade --allowerasing \
  "${rpms[@]}" waydroid wine ntsync-autoload wine-dxvk winetricks

install -D -m 0644 "$config_dir/dconf-profile-user" /etc/dconf/profile/user
install -D -m 0644 "$config_dir/dconf-luma" \
  /etc/dconf/db/luma.d/00-luma-desktop
install -D -m 0644 "$config_dir/dconf-gdm" \
  /etc/dconf/db/gdm.d/00-prairie-login
install -D -m 0644 "$config_dir/plymouthd.conf" \
  /etc/plymouth/plymouthd.conf
dconf update

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -qtf /usr/share/icons/Prairie
fi
if command -v update-mime-database >/dev/null 2>&1; then
  update-mime-database /usr/share/mime
fi

# A mutable Workstation preview owns a Fedora-generated GRUB configuration,
# not the Atomic/OSTree configuration for which Luma's advanced wrapper was
# designed. Preserve that generated file byte-for-byte. The native preview
# still receives Luma's quiet kernel arguments and Plymouth presentation.
if grep -Fq 'source $prefix/luma.cfg' /boot/grub2/grub.cfg; then
  printf 'error: refusing to continue with an Atomic Luma wrapper in mutable GRUB\n' >&2
  exit 1
fi

grubby --update-kernel=ALL --args='rhgb quiet'
# The Luma package owns the canonical selector and the configuration was
# installed above. `plymouth-set-default-theme` removes that package-owned
# selector on Fedora 44, so verify it and rebuild every installed kernel
# directly instead of mutating package state or refreshing only one kernel.
[[ $(readlink /usr/share/plymouth/themes/default.plymouth) == \
   luma-loading/luma-loading.plymouth ]] || {
  printf 'error: Luma Plymouth package does not own the default selector\n' >&2
  exit 1
}
dracut --regenerate-all -f

systemctl enable gdm.service NetworkManager.service sshd.service
systemctl set-default graphical.target
install -d -m 0755 /etc/luma
printf '%s\n' 'fedora-workstation-44-native-preview' > /etc/luma/installed-base-ref
printf '%s\n' "$stamp" > /etc/luma/preview-deployed-utc

# The account is intentionally fresh and disposable. Remove only the keys for
# which Luma supplies unlocked system defaults; personal files and unrelated
# application settings are untouched.
if [[ -S /run/user/1000/bus ]]; then
  user_bus=unix:path=/run/user/1000/bus
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/shell/
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/desktop/interface/
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/desktop/background/
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/desktop/wm/preferences/
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/desktop/peripherals/mouse/
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/desktop/peripherals/touchpad/
  runuser -u nick -- env DBUS_SESSION_BUS_ADDRESS="$user_bus" \
    dconf reset -f /org/gnome/mutter/
fi

rpm -qa | sort >"$state_dir/history/$stamp/rpm-after.txt"
dnf history list >"$state_dir/history/$stamp/dnf-history-after.txt"
printf 'Luma mutable preview deployment complete: %s\n' "$stamp"
