#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
inputs_file="$repo_root/config/desktop/inputs.env"
if [ ! -r "$inputs_file" ]; then
  # Composition copies the immutable pin set alongside this standalone test.
  # A smoke payload must never infer product versions from the running image.
  inputs_file=/var/tmp/luma-desktop-inputs.env
fi
if [ -r "$inputs_file" ]; then
  # shellcheck disable=SC1090
  . "$inputs_file"
fi
: "${PRAIRIE_CORE_APPS_NEVRA:?desktop smoke requires the pinned desktop inputs}"
: "${LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA:?desktop smoke requires the pinned desktop inputs}"

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

check 'Luma GNOME Control Center package' \
  rpm -q "gnome-control-center-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.x86_64"
check 'Luma GNOME Control Center filesystem package' \
  rpm -q "gnome-control-center-filesystem-50.4-${GNOME_CONTROL_CENTER_LUMA_RELEASE}.fc44.noarch"
check 'Luma GNOME Shell package' \
  rpm -q "$GNOME_SHELL_NEVRA"
# Location remains owned by the standard permission frontend/Access backend.
# These are installation/API gates, not permission or hardware acceptance.
check 'Maps has its native location permission and provider dependencies' \
  rpm -q libportal libportal-gtk4 xdg-desktop-portal xdg-desktop-portal-gnome geoclue2
check 'Maps location portal namespaces are introspectable' python3 -c '
import gi
gi.require_version("Xdp", "1.0")
gi.require_version("XdpGtk4", "1.0")
from gi.repository import Xdp, XdpGtk4
assert hasattr(Xdp.Portal, "location_monitor_start")
assert hasattr(XdpGtk4, "parent_new_gtk")
'
check 'Luma GNOME Shell common package' \
  rpm -q "$GNOME_SHELL_COMMON_NEVRA"
check 'shared Luma shell-state package' \
  rpm -q "$LUMA_SHELL_STATE_NEVRA"
check 'shared Luma libhandy package' \
  rpm -q "$LIBHANDY_NEVRA"
check 'Luma update agent package' \
  rpm -q "$LUMA_UPDATE_NEVRA"
check 'update checks are scheduled' \
  systemctl is-enabled luma-updated.timer
check 'update agent answers on the system bus' \
  busctl --system get-property org.projectluma.Update1 /org/projectluma/Update1 org.projectluma.Update1 State
check 'update agent never asks rpm-ostree to reboot or apply live' \
  bash -c "! grep -REq '\"(reboot|apply-live)\", GLib.Variant\\(\"b\", True' /usr/lib/python3*/site-packages/luma_update"
# Luma Cast (ADR-034): one session service and its backends, started only by
# D-Bus activation when something asks for them, never at login.
if [ -n "${LUMA_CAST_NEVRA:-}" ]; then
  check 'Luma Cast service package' rpm -q "$LUMA_CAST_NEVRA"
  check 'Luma Cast shared backend helper package' rpm -q "$LUMA_CAST_BACKEND_COMMON_NEVRA"
  check 'Luma Cast Google Cast backend package' rpm -q "$LUMA_CAST_GOOGLECAST_NEVRA"
  check 'Luma Cast Miracast backend package' rpm -q "$LUMA_CAST_MIRACAST_NEVRA"
  check 'Luma Cast DLNA backend package' rpm -q "$LUMA_CAST_DLNA_NEVRA"
  check 'Luma Cast test backend is not shipped' bash -c '! rpm -q luma-cast-loopback >/dev/null 2>&1'
  check 'AirPlay casting is not shipped' bash -c '! rpm -q luma-cast-airplay >/dev/null 2>&1'
  check 'Luma Cast and its backends start only by D-Bus activation' bash -c '
    for name in org.projectluma.Cast1 org.projectluma.Cast1.Backend.GoogleCast \
                org.projectluma.Cast1.Backend.Miracast org.projectluma.Cast1.Backend.DLNA; do
      grep -Eq "^SystemdService=luma-cast[a-z-]*\.service$" "/usr/share/dbus-1/services/$name.service" || exit 1
    done
    ! grep -q "^\[Install\]" /usr/lib/systemd/user/luma-cast*.service &&
    ! compgen -G "/usr/lib/systemd/user/*.wants/luma-cast*" >/dev/null &&
    ! compgen -G "/etc/systemd/user/*.wants/luma-cast*" >/dev/null &&
    ! grep -rlsq luma-cast /etc/xdg/autostart /usr/share/gnome/autostart'
  check 'Luma Cast is not running in any session just because someone logged in' bash -c '
    for user in $(loginctl list-users --no-legend | awk "{ print \$2 }"); do
      [ "$(systemctl --user -M "$user@" is-active luma-cast.service 2>/dev/null)" != active ] || exit 1
    done'
  check 'SELinux denied nothing to Luma Cast this boot' bash -c \
    "! journalctl -b -q --no-pager _TRANSPORT=audit | grep -F 'avc:  denied' | grep -Eq 'comm=\"luma-cast|exe=\"/usr/libexec/luma-cast'"
  check 'GNOME Shell logged no JavaScript errors from Cast' bash -c \
    "! journalctl -b -q --no-pager -o cat _COMM=gnome-shell | grep -E 'JS ERROR|TypeError|ReferenceError|SyntaxError|Gjs-CRITICAL' -A6 | grep -Eq 'lumaCast(Dialogs|Sheet)?\\.js|status/cast\\.js'"
fi
check 'shared shell-state schema' test -f \
  /usr/share/glib-2.0/schemas/org.project_luma.shell-state.gschema.xml
check 'shared Luma GTK/Phosh presentation' test -f \
  /usr/share/themes/Luma/gtk-3.0/gtk.css
check 'shared Luma dark GTK/Phosh presentation' test -f \
  /usr/share/themes/Luma-dark/gtk-3.0/gtk.css
check 'shared shell-state broker is session-bound' test -L \
  /usr/lib/systemd/user/graphical-session.target.wants/luma-shell-state.service
check 'Fedora first-login Tour is absent' \
  bash -c '! rpm -q gnome-tour >/dev/null 2>&1 && [ ! -e /usr/share/applications/org.gnome.Tour.desktop ]'
check 'Luma Files package' \
  rpm -q "$NAUTILUS_NEVRA"
check 'Luma Files extensions package' \
  rpm -q "$NAUTILUS_EXTENSIONS_NEVRA"
check 'native Luma Calculator package' rpm -q "$LUMA_CALCULATOR_NEVRA"
check 'native Luma Terminal package' rpm -q "$LUMA_TERMINAL_NEVRA"
check 'default Tide package' rpm -q "$LUMA_TIDE_NEVRA"
check 'default Tide owns a valid installed launcher' bash -c '
  test -s /usr/share/applications/org.projectluma.Tide.desktop &&
  desktop-file-validate /usr/share/applications/org.projectluma.Tide.desktop &&
  test "$(rpm -qf --qf "%{NAME}" /usr/share/applications/org.projectluma.Tide.desktop)" = luma-tide
'
check 'native Luma Disks package' rpm -q "$LUMA_DISKS_NEVRA"
# Prairie core apps own Calendar and Text Editor (config/desktop/
# system-flatpak-replacements.txt); the GNOME packages are not composed.
check 'Prairie core apps replace GNOME Calendar' \
  bash -c '! rpm -q gnome-calendar >/dev/null 2>&1'
check 'Luma Relay package' \
  rpm -q "$LUMA_RELAY_NEVRA"
check 'Relay Wine Explorer integration' \
  rpm -q wine-luma-relay-explorer-11.0-3.luma.2.fc44.x86_64
check 'Relay Windows engine' rpm -q wine
check 'Relay NTSYNC loader' rpm -q ntsync-autoload
check 'Relay Direct3D translation' rpm -q wine-dxvk
check 'Relay reviewed component helper' rpm -q winetricks
# The unified Luma application installer receives Windows, Android and Linux
# packages and hands Windows programs to Relay.
check 'the Luma application installer owns Windows executable files' \
  grep -Fq 'application/x-ms-dos-executable=org.projectluma.ApplicationInstaller.desktop' \
    /etc/xdg/mimeapps.list
check 'Relay has no always-on service' \
  bash -c '! systemctl list-unit-files --no-legend | grep -q luma-relay'
check 'legacy Dash to Dock is absent from desktop composition' \
  bash -c '! rpm -q gnome-shell-extension-dash-to-dock'
check 'Luma-patched Tiling Shell package' \
  rpm -q "$TILING_SHELL_NEVRA"
check 'Luma Tiling Quick Toggle package' \
  rpm -q "$LUMA_TILING_TOGGLE_NEVRA"
check 'Luma backgrounds package' \
  rpm -q "$LUMA_BACKGROUNDS_NEVRA"
check 'Luma graphical boot package' \
  rpm -q "$LUMA_BOOT_THEME_NEVRA"
check 'Plymouth script plugin' \
  rpm -q "$PLYMOUTH_PLUGIN_SCRIPT_NEVRA"
check 'Prairie light Plymouth details renderer' \
  rpm -q "$PLYMOUTH_NEVRA"
check 'Prairie Plymouth dependency family is release-aligned' \
  rpm -q "$PLYMOUTH_CORE_LIBS_NEVRA" "$PLYMOUTH_GRAPHICS_LIBS_NEVRA" "$PLYMOUTH_SCRIPTS_NEVRA" \
    "$PLYMOUTH_PLUGIN_LABEL_NEVRA" "$PLYMOUTH_PLUGIN_TWO_STEP_NEVRA" \
    "$PLYMOUTH_SYSTEM_THEME_NEVRA" "$PLYMOUTH_THEME_SPINNER_NEVRA"
check 'Plymouth script plugin can take over from the firmware logo' \
  grep -aqF SetFirmwareBackgroundOpacity /usr/lib64/plymouth/script.so
check 'Luma boot splash is installed with its prompt artwork' \
  bash -c 'cd /usr/share/plymouth/themes/luma-loading && test -s luma-loading.script && test -s luma-wordmark.png && test -s luma-loading-dot.png && test -s prompt-lock.png && test -s prompt-field.png && test -s prompt-bullet.png && test -s prompt-capslock.png'
check 'Luma boot splash hands off from the firmware logo' \
  grep -Fq 'Window.SetFirmwareBackgroundOpacity' /usr/share/plymouth/themes/luma-loading/luma-loading.script
check 'Luma firmware-logo presentation stays available to administrators' \
  bash -c 'cd /usr/share/plymouth/themes/luma-firmware && test -s luma-firmware.plymouth && test -s lock.png && test -s entry.png && test -s bullet.png && test -s watermark.png && test -s throbber-0001.png && test -s keymap-render.png'
check 'shared package carries the handheld presentation without selecting it' test -f \
  /usr/share/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script
check 'Luma Plymouth theme owns the canonical default selector' \
  test "$(readlink /usr/share/plymouth/themes/default.plymouth)" = \
    luma-loading/luma-loading.plymouth
# ADR-045: Luma selects its splash in /usr/share/plymouth/plymouthd.defaults,
# and /etc/plymouth/plymouthd.conf is an administrator template with no
# settings. The theme that counts is the one Plymouth resolves.
check 'Luma Plymouth theme is selected' \
  test "$(plymouth-set-default-theme)" = luma-loading
check 'Luma selects its splash in the Plymouth defaults' \
  grep -Fxq 'Theme=luma-loading' /usr/share/plymouth/plymouthd.defaults
check 'Luma boot splash setting is the packaged desktop default' \
  cmp -s /etc/plymouth/plymouthd.conf /usr/share/luma/boot/plymouthd.conf
check 'Luma boot splash migration is wanted at boot' \
  test -L /usr/lib/systemd/system/sysinit.target.wants/luma-boot-splash-migrate.service
check 'locally built initramfs images follow the boot splash migration' \
  test -x /usr/lib/dracut/modules.d/46luma-boot-splash/module-setup.sh
check 'initramfs carries the selected Luma boot splash' \
  bash -c 'lsinitrd "/usr/lib/modules/$(uname -r)/initramfs.img" 2>/dev/null | grep -Fq usr/share/plymouth/themes/luma-loading/prompt-field.png || lsinitrd 2>/dev/null | grep -Fq usr/share/plymouth/themes/luma-loading/prompt-field.png'
check 'GRUB shows a five-second graphical startup surface' \
  bash -c "grub2-editenv list | grep -Fxq 'timeout_style=menu' && grub2-editenv list | grep -Fxq 'timeout=5'"
check 'Atomic GRUB configuration consumes its persistent policy' \
  grep -Fq 'load_env' /boot/grub2/grub.cfg
check 'Atomic GRUB configuration sources the Luma advanced surface' \
  grep -Fq 'source $prefix/luma.cfg' /boot/grub2/grub.cfg
check 'Luma GRUB theme is installed on the boot filesystem' \
  test -s /boot/grub2/themes/luma/theme.txt
check 'Luma GRUB font is installed on the boot filesystem' \
  test -s /boot/grub2/themes/luma/prairie.pf2
check 'graphical quiet kernel arguments are active' \
  bash -c "grep -qw rhgb /proc/cmdline && grep -qw quiet /proc/cmdline"
check 'Prairie icon-theme package' \
  rpm -q "$PRAIRIE_ICON_THEME_NEVRA"

prairie_status_symbols_are_installed() {
  local icon
  for icon in \
    network-wireless-signal-excellent-symbolic.svg \
    network-wireless-signal-good-symbolic.svg \
    network-wireless-signal-ok-symbolic.svg \
    network-wireless-signal-weak-symbolic.svg \
    network-wireless-signal-none-symbolic.svg \
    network-wired-symbolic.svg \
    audio-volume-muted-symbolic.svg \
    audio-volume-low-symbolic.svg \
    audio-volume-medium-symbolic.svg \
    audio-volume-high-symbolic.svg \
    system-shutdown-symbolic.svg; do
    test -f "/usr/share/icons/Prairie/symbolic/status/$icon" || return 1
  done
}

check 'Prairie status symbol family' prairie_status_symbols_are_installed

prairie_window_control_symbols_are_installed() {
  local icon
  for icon in \
    window-minimize-symbolic.svg \
    window-maximize-symbolic.svg \
    window-restore-symbolic.svg \
    window-close-symbolic.svg; do
    test -f "/usr/share/icons/Prairie/symbolic/actions/$icon" || return 1
  done
}

check 'Prairie window-control symbol family' \
  prairie_window_control_symbols_are_installed
check 'Figtree system font package' \
  rpm -q "$FIGTREE_NEVRA"
check 'Caveat handwriting font package' \
  rpm -q "$CAVEAT_NEVRA"
check 'Caveat does not take over the generic sans family' \
  bash -c 'test "$(fc-match --format="%{family[0]}" sans-serif)" != Caveat'
check 'Luma GTK window-material package' \
  rpm -q "$GTK4_NEVRA"
check 'Luma GTK 3 compatibility design-system package' \
  rpm -q "$GTK3_NEVRA"
check 'Luma libadwaita design-system package' \
  rpm -q "$LIBADWAITA_NEVRA"
check 'Prairie core apps replace GNOME Text Editor' \
  bash -c '! rpm -q gnome-text-editor >/dev/null 2>&1'
check 'bundled Text Editor does not retain the Fedora system Flatpak runtime' \
  bash -c '! flatpak info --system org.gnome.TextEditor >/dev/null 2>&1'
check 'Characters is not installed (Super+. picker replaces it)' \
  bash -c '! rpm -q gnome-characters >/dev/null 2>&1'
check 'Characters is not installed as a system Flatpak' \
  bash -c '! flatpak info --system org.gnome.Characters >/dev/null 2>&1'
check 'unsupported GNOME Extensions frontend is absent' bash -c '! rpm -q gnome-extensions-app >/dev/null 2>&1'
check 'Extensions does not retain the Fedora system Flatpak runtime' \
  bash -c '! flatpak info --system org.gnome.Extensions >/dev/null 2>&1'
check 'compiled Luma dconf database' test -f /etc/dconf/db/luma
check 'compiled Prairie GDM dconf database' test -f /etc/dconf/db/gdm

libadwaita_selected_sidebar_hover_is_invariant() {
  LC_ALL=C grep -aFq \
    '.navigation-sidebar > row:selected:hover, .navigation-sidebar > row:selected.has-open-popup { background-color: color-mix(in srgb, currentColor 13%, transparent); }' \
    /usr/lib64/libadwaita-1.so.0
}

check 'selected sidebar rows keep the Prairie selection tint on hover' \
  libadwaita_selected_sidebar_hover_is_invariant

libadwaita_generic_headerbar_contract_is_installed() {
  LC_ALL=C grep -aFq \
    'headerbar { min-height: 42px' \
    /usr/lib64/libadwaita-1.so.0 &&
  ! LC_ALL=C grep -aFq \
    'windowtitle .title, .luma-title-label' \
    /usr/lib64/libadwaita-1.so.0 &&
  LC_ALL=C grep -aFq \
    'windowtitle .title { min-height: 16px; }' \
    /usr/lib64/libadwaita-1.so.0 &&
  LC_ALL=C grep -aFq \
    'headerbar button.image-button > image, headerbar menubutton > button > image { -gtk-icon-size: 14px; color: var(--headerbar-fg-color); }' \
    /usr/lib64/libadwaita-1.so.0
}

check 'generic libadwaita title bars use intrinsic text and Prairie bar metrics' \
  libadwaita_generic_headerbar_contract_is_installed

check 'retired Layouts app is not installed' bash -c '! rpm -q luma-layouts >/dev/null 2>&1'
# Office, Creative and Studio are optional Depot collections. Query package
# names so any accidentally included release fails, including instrument data.
collection_package_absent() {
  ! rpm -q "$1" >/dev/null 2>&1
}
for package in luma-write luma-grid-preview luma-stage-preview \
               luma-canvas-preview luma-reel luma-darkroom luma-session \
               luma-session-instruments sfizz; do
  check "optional Depot collection package $package is absent" \
    collection_package_absent "$package"
done

collection_launcher_absent() {
  [ ! -e "$1" ] && [ ! -L "$1" ]
}
for launcher in org.projectluma.Write io.luma.Grid io.luma.Stage \
                org.projectluma.Canvas org.projectluma.Reel \
                org.projectluma.Darkroom org.projectluma.Session; do
  check "optional Depot collection launcher $launcher is absent" \
    collection_launcher_absent "/usr/share/applications/$launcher.desktop"
done
# Owner-requested baseline exclusions are checked by package name and exact
# launchers. GIO verifies hidden provider metadata without concealing duplicates.
for package in gnome-disk-utility gnome-calculator ptyxis gnome-terminal \
               gnome-console yelp firefox gnome-extensions-app openemu-linux luma-imager; do
  check "excluded baseline application $package is absent" collection_package_absent "$package"
done
for launcher in org.gnome.DiskUtility org.gnome.Calculator org.gnome.Ptyxis \
                org.gnome.Terminal org.gnome.Console org.gnome.Extensions \
                firefox yelp org.projectluma.Imager org.openemu.OpenEmuLinux.Development; do
  check "excluded baseline launcher $launcher is absent" \
    collection_launcher_absent "/usr/share/applications/$launcher.desktop"
done
check 'GIO hides secondary Displays and Mods, retaining Luma native roles' \
  env XDG_DATA_DIRS=/usr/share/luma/desktop-overrides:/usr/local/share:/usr/share python3 - <<'PYGIO'
import ctypes
import ctypes.util
from pathlib import Path
lib = ctypes.CDLL(ctypes.util.find_library('gio-2.0'))
lib.g_desktop_app_info_new.argtypes = [ctypes.c_char_p]
lib.g_desktop_app_info_new.restype = ctypes.c_void_p
lib.g_app_info_should_show.argtypes = [ctypes.c_void_p]
lib.g_app_info_should_show.restype = ctypes.c_int
lib.g_desktop_app_info_get_filename.argtypes = [ctypes.c_void_p]
lib.g_desktop_app_info_get_filename.restype = ctypes.c_char_p
for desktop in ('org.projectluma.Displays', 'org.projectluma.Mods'):
    app = lib.g_desktop_app_info_new((desktop + '.desktop').encode())
    assert app and not lib.g_app_info_should_show(app), desktop
    expected = '/usr/share/luma/desktop-overrides/applications/' + desktop + '.desktop'
    assert lib.g_desktop_app_info_get_filename(app).decode() == expected, desktop
for desktop in ('org.projectluma.Calculator', 'org.projectluma.Terminal', 'org.projectluma.Disks.Preview', 'org.projectluma.Tide'):
    app = lib.g_desktop_app_info_new((desktop + '.desktop').encode())
    assert app and lib.g_app_info_should_show(app), desktop
print('PASS: 2 hidden providers, 4 visible native applications')
PYGIO
check 'exact shared Search package installed' rpm -q "$LUMA_SEARCH_NEVRA"

check 'Prism desktop payload hash' \
  bash -c "printf '%s  %s\n' \
    $LUMA_PRISM_SHA256 \
    /usr/share/backgrounds/luma/prism.png | sha256sum --check --status"
check 'Prism handheld payload hash' \
  bash -c "printf '%s  %s\n' \
    $LUMA_PRISM_HANDHELD_SHA256 \
    /usr/share/backgrounds/luma/luma-prism.png | sha256sum --check --status"
check 'Prism appears in GNOME background metadata' \
  grep -Fq '<name>Prism</name>' \
    /usr/share/gnome-background-properties/luma-backgrounds.xml

gtk_compatibility_identity_contract_is_installed() {
  LC_ALL=C grep -aFq 'gtk-header-bar-luma-identity-owner' \
    /usr/lib64/libgtk-4.so.1 &&
  LC_ALL=C grep -aFq 'luma-no-automatic-identity' \
    /usr/lib64/libgtk-4.so.1 &&
  LC_ALL=C grep -aFq 'luma-identity-display' \
    /usr/lib64/libgtk-4.so.1 &&
  LC_ALL=C grep -aFq 'notify::application' \
    /usr/lib64/libgtk-4.so.1 &&
  LC_ALL=C grep -aFq 'gtk3-header-bar-luma-identity-owner' \
    /usr/lib64/libgtk-3.so.0 &&
  LC_ALL=C grep -aFq 'luma-identity-display' \
    /usr/lib64/libgtk-3.so.0 &&
  LC_ALL=C grep -aFq 'luma-automatic-identity' \
    /usr/lib64/libgtk-3.so.0 &&
  LC_ALL=C grep -aFq 'notify::application' \
    /usr/lib64/libgtk-3.so.0 &&
  LC_ALL=C grep -aFq 'hdy-header-bar-luma-identity-owner' \
    /usr/lib64/libhandy-1.so.0 &&
  LC_ALL=C grep -aFq 'luma-automatic-identity' \
    /usr/lib64/libhandy-1.so.0 &&
  LC_ALL=C grep -aFq 'notify::application' \
    /usr/lib64/libhandy-1.so.0 &&
  LC_ALL=C grep -aFq 'notify::application' \
    /usr/lib64/libadwaita-1.so.0
}

check 'GTK 3 and GTK 4 own the shared compatibility identity contract' \
  gtk_compatibility_identity_contract_is_installed

native_application_surface_contract_is_installed() {
  LC_ALL=C grep -aFq 'luma-native-window' \
    /usr/lib64/libgtk-4.so.1 &&
  LC_ALL=C grep -aFq 'luma-native-work-surface' \
    /usr/lib64/libgtk-4.so.1 &&
  LC_ALL=C grep -aFq 'luma-native-window' \
    /usr/lib64/libgtk-3.so.0 &&
  LC_ALL=C grep -aFq 'luma-native-work-surface' \
    /usr/lib64/libgtk-3.so.0 &&
  LC_ALL=C grep -aFq 'luma-hdy-native-window' \
    /usr/lib64/libhandy-1.so.0 &&
  LC_ALL=C grep -aFq 'luma-native-work-surface' \
    /usr/lib64/libhandy-1.so.0 &&
  LC_ALL=C grep -aFq 'luma-adw-native-window' \
    /usr/lib64/libadwaita-1.so.0 &&
  LC_ALL=C grep -aFq 'luma-window-toolbar-view' \
    /usr/lib64/libadwaita-1.so.0 &&
  LC_ALL=C grep -aFq 'luma-native-work-surface' \
    /usr/lib64/libadwaita-1.so.0 &&
  grep -Fq 'window.luma-native-window:not(.luma-no-native-surfaces)' \
    /usr/share/themes/Luma/gtk-3.0/luma-common.css &&
  grep -Fq 'window.luma-hdy-native-window:not(.luma-no-native-surfaces) .luma-native-work-surface' \
    /usr/share/themes/Luma/gtk-3.0/luma-common.css
}

check 'native GTK application windows own the shared Luma surface contract' \
  native_application_surface_contract_is_installed

check 'Prairie inherits complete upstream icon themes' \
  grep -Fxq 'Inherits=Adwaita,hicolor' \
    /usr/share/icons/Prairie/index.theme
check 'Prairie ships the Filer full-color icon' \
  test -f /usr/share/icons/Prairie/scalable/apps/org.gnome.Nautilus.svg
check 'Prairie ships the Filer symbolic icon' \
  test -f /usr/share/icons/Prairie/symbolic/apps/org.gnome.Nautilus-symbolic.svg
check 'Prairie ships the Calculator full-color icon' \
  test -f /usr/share/icons/Prairie/scalable/apps/org.gnome.Calculator.svg
check 'Prairie ships the Calculator symbolic icon' \
  test -f /usr/share/icons/Prairie/symbolic/apps/org.gnome.Calculator-symbolic.svg
check 'native Luma Calculator owns its current icon lookup identity' \
  grep -Fxq 'Icon=luma-v3-calculator' \
    /usr/share/applications/org.projectluma.Calculator.desktop
check 'Prairie preserves the upstream RPM Firefox artwork' \
  test ! -e /usr/share/icons/Prairie/scalable/apps/firefox.svg
check 'Prairie preserves the upstream Flatpak Firefox artwork' \
  test ! -e /usr/share/icons/Prairie/scalable/apps/org.mozilla.firefox.svg
check 'Prairie ships both canonical Trash states' \
  test -f /usr/share/icons/Prairie/scalable/places/user-trash.svg
check 'Prairie ships the full Trash state' \
  test -f /usr/share/icons/Prairie/scalable/places/user-trash-full.svg
check 'Prairie ships byte-identical Trash aliases for the dock context' \
  cmp /usr/share/icons/Prairie/scalable/places/user-trash-full.svg \
    /usr/share/icons/Prairie/scalable/apps/user-trash-full.svg
check 'Filer keeps the upstream icon lookup identity' \
  grep -Fxq 'Icon=org.gnome.Nautilus' \
    /usr/share/applications/org.gnome.Nautilus.desktop
check 'Figtree is the preferred generic sans family' \
  bash -c 'test "$(fc-match --format="%{family[0]}" sans-serif)" = Figtree'
check 'GTK embeds the Luma native light-mode window elevation' \
  grep -aFq '0 54px 110px -28px rgba(25, 27, 31, 0.56)' /usr/lib64/libgtk-4.so.1
check 'GTK embeds the Luma native dark-mode window elevation' \
  grep -aFq '0 64px 120px -30px rgba(9, 11, 14, 0.82)' /usr/lib64/libgtk-4.so.1

check 'shared Luma core applications package is installed' \
  rpm -q "$PRAIRIE_CORE_APPS_NEVRA"
check 'shared Luma Developer Platform package is installed' \
  rpm -q "$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA"
check 'Luma Semantics introspection is installed' test -f \
  /usr/lib64/girepository-1.0/LumaSemantics-1.typelib
check 'Luma UI introspection is installed' test -f \
  /usr/lib64/girepository-1.0/LumaUI-1.typelib

for app_id in Messages Phone Contacts Calendar Weather Tasks VoiceMemos Notes Photos Clock Camera; do
  check "Luma ${app_id} launcher is visible on desktop" \
    test -f "/usr/share/applications/org.projectluma.${app_id}.desktop"
  check "Luma ${app_id} launcher is not hidden on desktop" \
    bash -c "! grep -Eq '^(NoDisplay=true|OnlyShowIn=.*Mobile)' '/usr/share/applications/org.projectluma.${app_id}.desktop'"
done

check 'Prairie Messages owns SMS links on desktop' \
  bash -c "test \"$(xdg-mime query default x-scheme-handler/sms)\" = org.projectluma.Messages.desktop"
check 'the Luma application installer owns Windows application packages on desktop' \
  bash -c "test \"$(xdg-mime query default application/vnd.microsoft.portable-executable)\" = org.projectluma.ApplicationInstaller.desktop"
check 'Prairie Phone owns telephone links on desktop' \
  bash -c "test \"$(xdg-mime query default x-scheme-handler/tel)\" = org.projectluma.Phone.desktop"

control_center_contains() {
  LC_ALL=C grep -aFq "$1" /usr/bin/gnome-control-center
}

tiling_shell_modes_are_safe() {
  extension_dir=/usr/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com
  bad_path=$(find "$extension_dir" \
    \( -type d ! -perm 0755 -o -type f ! -perm 0644 \) \
    -print -quit)
  test -z "$bad_path"
}

tiling_shell_drag_layout_is_default() {
  extension_dir=/usr/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com
  actual=$(GSETTINGS_SCHEMA_DIR="$extension_dir/schemas" \
    gsettings get org.gnome.shell.extensions.tilingshell \
    show-layout-while-dragging)
  test "$actual" = true
}

tiling_shell_control_escapes_drag() {
  extension_dir=/usr/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com
  actual=$(GSETTINGS_SCHEMA_DIR="$extension_dir/schemas" \
    gsettings get org.gnome.shell.extensions.tilingshell \
    tiling-system-deactivation-key)
  test "$actual" = "['0']" &&
    grep -Fq ')) && !isTilingSystemDeactivated' \
      "$extension_dir/components/tilingsystem/tilingManager.js"
}

tiling_shell_uses_quick_settings_only() {
  extension_dir=/usr/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com
  test "$(GSETTINGS_SCHEMA_DIR="$extension_dir/schemas" \
    gsettings get org.gnome.shell.extensions.tilingshell show-indicator)" = false &&
    grep -Fq '<method name="newLayout"' "$extension_dir/dbus.js" &&
    grep -Fq '<method name="selectLayout">' "$extension_dir/dbus.js" &&
    grep -Fq 'function createLayoutPreview' \
      /usr/share/gnome-shell/extensions/tiling-toggle@project-luma.local/extension.js &&
    grep -Fq 'SettingsSchemaSource.new_from_directory' \
      /usr/share/gnome-shell/extensions/tiling-toggle@project-luma.local/extension.js
}

nautilus_has_luma_ui_markers() {
  grep -aFq 'slot.search-global' /usr/bin/nautilus &&
    grep -aFq 'sidebar_resize_handle' /usr/bin/nautilus &&
    grep -aFq 'luma-titlebar' /usr/bin/nautilus &&
    grep -aFq 'luma-secondary-toolbar' /usr/bin/nautilus &&
    grep -aFq 'transform: translate(0, 1px)' /usr/bin/nautilus &&
    grep -aFq 'luma-path-menu' /usr/bin/nautilus &&
    grep -aFq 'gtk_scrolled_window_set_propagate_natural_width' /usr/bin/nautilus &&
    grep -aFq '%s is empty' /usr/bin/nautilus &&
    grep -aFq 'prairie-folder-empty' /usr/bin/nautilus &&
    ! grep -aFq 'luma_files_window_bg' /usr/bin/nautilus
}

nautilus_sidebar_default_is_178() {
  test "$(gsettings get org.gnome.nautilus.window-state sidebar-width)" = 178
}

# LumaUI Filer (patch 0080): the grid opens at upstream's medium 96px step,
# which with two 14px margins is v70's 124px tile.
nautilus_grid_default_is_v70_medium() {
  test "$(gsettings get org.gnome.nautilus.icon-view default-zoom-level)" = "'medium'"
}

gnome_shell_has_luma_shelf() {
  shell_library=/usr/lib64/gnome-shell/libshell-18.so
  grep -aFq "left: ['dateMenu']" "$shell_library" &&
    grep -aFq "center: ['liveExtensions']" "$shell_library" &&
    ! grep -aFq 'luma-panel-background-blur' "$shell_library" &&
    grep -aFq 'luma-clock-time' "$shell_library" &&
    grep -aFq 'luma-secondary-status-text' "$shell_library" &&
    grep -aFq "dateFormat = N_('%a, %b %-d')" "$shell_library" &&
    grep -aFq 'new St.Widget({width: 9})' "$shell_library" &&
    grep -aFq 'const edgeInset = 14 * scaleFactor' "$shell_library" &&
    grep -aFq 'this._timeDisplay.translation_y' "$shell_library" &&
    grep -aFq 'class PrairieLoginClock' "$shell_library" &&
    grep -aFq 'class PrairieLoginPowerButton' "$shell_library" &&
    grep -aFq 'login-dialog-prompt-entry prairie-login-entry' "$shell_library" &&
    grep -aFq 'else if (!Main.sessionMode.hasOverview)' "$shell_library" &&
    grep -aFq 'Keep the completed Prairie authentication surface stable' "$shell_library" &&
    grep -aFq 'call_start_session_when_ready_sync(serviceName, true, null)' "$shell_library" &&
    grep -aFq 'class LiveExtensionsIndicator' "$shell_library" &&
    grep -aFq 'class Shelf extends St.Widget' "$shell_library" &&
    grep -aFq 'org.project_luma.shell-state' "$shell_library" &&
    grep -aFq 'vfunc_get_preferred_width(forHeight)' "$shell_library" &&
    grep -aFq 'LEGACY_DESKTOP_DOCK_UUID' "$shell_library" &&
    grep -aFq 'SHELF_STYLESHEET_URI' "$shell_library" &&
    grep -aFq 'load_stylesheet(this._stylesheet)' "$shell_library" &&
    grep -aFq "_settings.get_string('shelf-surface-mode')" "$shell_library" &&
    grep -aFq 'recursiveUnpack' "$shell_library" &&
    grep -aFq '_isValidExtension' "$shell_library"
}

check 'Touchpad Settings describes three-finger Activities gesture' \
  control_center_contains 'Swipe Up with Three Fingers'
check 'GNOME Shell owns the Luma Shelf, clock, and native dock lifecycle' \
  gnome_shell_has_luma_shelf
check 'Touchpad Settings describes three-finger workspace gesture' \
  control_center_contains 'Swipe Left or Right with Three Fingers'
check 'Tiling Shell payload uses safe normalized modes' \
  tiling_shell_modes_are_safe
check 'Tiling Shell reveals layout while dragging by default' \
  tiling_shell_drag_layout_is_default
check 'Control suppresses both drag zones and the final tile assignment' \
  tiling_shell_control_escapes_drag
check 'Tiling layouts live in Quick Settings without a default panel indicator' \
  tiling_shell_uses_quick_settings_only
check 'Tiling Shell ships Luma Split as a fresh-profile layout' \
  grep -Fq 'Luma Split' \
    /usr/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com/settings/settings.js
check 'Files ships the global Search and resize-handle UI' \
  nautilus_has_luma_ui_markers
check 'Files sidebar is narrow and resizable by default' \
  nautilus_sidebar_default_is_178
check 'Filer grid starts at the v70 tile size (upstream medium)' \
  nautilus_grid_default_is_v70_medium

empty_config=$(mktemp -d)
trap 'rm -rf "$empty_config"' EXIT
read_default() {
  XDG_CONFIG_HOME="$empty_config" dconf read "$1"
}

check_value() {
  description=$1
  key=$2
  expected=$3
  actual=$(read_default "$key")
  if [ "$actual" = "$expected" ]; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s (expected %s, got %s)\n' \
      "$description" "$expected" "${actual:-<empty>}"
    failures=$((failures + 1))
  fi
}

check_schema_default() {
  description=$1
  schema=$2
  key=$3
  expected=$4
  actual=$(XDG_CONFIG_HOME="$empty_config" gsettings get "$schema" "$key")
  if [ "$actual" = "$expected" ]; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s (expected %s, got %s)\n' \
      "$description" "$expected" "${actual:-<empty>}"
    failures=$((failures + 1))
  fi
}

check_schema_default 'desktop Filer retains the full storage view by default' \
  org.gnome.nautilus.preferences personal-storage-only false
check_value 'Prairie Calendar is the desktop calendar command' \
  /org/gnome/desktop/default-applications/office/calendar/exec "'prairie-calendar'"
check 'Luma Android integration package' \
  rpm -q "$LUMA_ANDROID_RUNTIME_NEVRA"
check 'Fedora Waydroid engine' rpm --quiet --query waydroid
check 'APK MIME integration' grep -Fq application/vnd.android.package-archive \
  /usr/share/mime/packages/luma-android.xml
check 'APK installer broker' test -f \
  /usr/share/dbus-1/services/org.projectluma.ApplicationInstaller1.service
check_value '12-hour clock default' \
  /org/gnome/desktop/interface/clock-format "'12h'"

gdm_clock_format=$(DCONF_PROFILE=gdm XDG_CONFIG_HOME="$empty_config" \
  dconf read /org/gnome/desktop/interface/clock-format)
if [ "$gdm_clock_format" = "'12h'" ]; then
  printf 'PASS  Prairie GDM 12-hour clock default\n'
else
  printf 'FAIL  Prairie GDM 12-hour clock default (expected %s, got %s)\n' \
    "'12h'" "${gdm_clock_format:-<empty>}"
  failures=$((failures + 1))
fi

gdm_audible_bell=$(DCONF_PROFILE=gdm XDG_CONFIG_HOME="$empty_config" \
  dconf read /org/gnome/desktop/wm/preferences/audible-bell)
if [ "$gdm_audible_bell" = "false" ]; then
  printf 'PASS  GDM window manager bell makes no sound by default\n'
else
  printf 'FAIL  GDM window manager bell makes no sound by default (expected %s, got %s)\n' \
    "false" "${gdm_audible_bell:-<empty>}"
  failures=$((failures + 1))
fi
check_value 'weekday is shown by default' \
  /org/gnome/desktop/interface/clock-show-weekday true
check_value 'date is shown by default' \
  /org/gnome/desktop/interface/clock-show-date true
check_value 'dark appearance is the default' \
  /org/gnome/desktop/interface/color-scheme "'prefer-dark'"
check_value 'Luma GTK/Phosh presentation is shared by default' \
  /org/gnome/desktop/interface/gtk-theme "'Luma'"
check_schema_default 'Prairie is the unlocked icon-theme default' \
  org.gnome.desktop.interface icon-theme "'Prairie'"
check_value 'Figtree is the interface font default' \
  /org/gnome/desktop/interface/font-name "'Figtree 11'"
check_value 'Figtree is the proportional document font default' \
  /org/gnome/desktop/interface/document-font-name "'Figtree 11'"
check_value 'Night Light Color Temperature starts at its coolest, far-left setting' \
  /org/gnome/settings-daemon/plugins/color/night-light-temperature 'uint32 4700'
check_value 'light appearance uses Prism' \
  /org/gnome/desktop/background/picture-uri \
  "'file:///usr/share/backgrounds/luma/prism.png'"
check_value 'dark appearance uses Prism' \
  /org/gnome/desktop/background/picture-uri-dark \
  "'file:///usr/share/backgrounds/luma/prism.png'"
check_value 'Prism uses native zoom placement' \
  /org/gnome/desktop/background/picture-options "'zoom'"
check_value 'window controls use the Luma right-side order' \
  /org/gnome/desktop/wm/preferences/button-layout \
  "':minimize,maximize,close'"
check_value 'window manager bell makes no sound by default' \
  /org/gnome/desktop/wm/preferences/audible-bell false
gtk_settings_say_no_error_bell() {
  for settings in /etc/gtk-3.0/settings.ini /etc/gtk-4.0/settings.ini; do
    [ -f "$settings" ] || return 1
    grep -Fxq 'gtk-error-bell=0' "$settings" || return 1
  done
}
check 'GTK 3 and GTK 4 error bell is off by default' \
  gtk_settings_say_no_error_bell
check_value 'mouse natural scrolling default' \
  /org/gnome/desktop/peripherals/mouse/natural-scroll true
check_value 'adaptive mouse acceleration default' \
  /org/gnome/desktop/peripherals/mouse/accel-profile "'adaptive'"
check_value 'touchpad natural scrolling default' \
  /org/gnome/desktop/peripherals/touchpad/natural-scroll true
check_value 'touchpad tapping default' \
  /org/gnome/desktop/peripherals/touchpad/tap-to-click true
check_value 'two-finger tap uses right click' \
  /org/gnome/desktop/peripherals/touchpad/tap-button-map "'lrm'"
check_value 'bottom-right physical corner click uses right click' \
  /org/gnome/desktop/peripherals/touchpad/click-method "'areas'"
check_value 'Shelf contract is versioned' \
  /org/project-luma/shell-state/shelf-layout-version 'uint32 1'
check_value 'Shelf defaults to the bottom edge' \
  /org/project-luma/shell-state/shelf-edge "'bottom'"
check_value 'Shelf uses the centered combined layout' \
  /org/project-luma/shell-state/shelf-group-layout "'luma'"
check_value 'Shelf islands are separate by default' \
  /org/project-luma/shell-state/shelf-surface-mode "'separate'"
check_value 'Shelf defaults to the production dark material' \
  /org/project-luma/shell-state/shelf-material "'dark'"
check_value 'Shelf is primary-monitor scoped by default' \
  /org/project-luma/shell-state/shelf-monitor-mode "'primary'"
check_value 'Shelf overflow scrolls instead of shrinking icons' \
  /org/project-luma/shell-state/shelf-overflow-mode "'scroll'"
check_value 'Shelf reserves ordinary window work area' \
  /org/project-luma/shell-state/shelf-reserve-work-area true
check_value 'Shelf v1 never auto-hides' \
  /org/project-luma/shell-state/shelf-auto-hide false
check_value 'native edge tiling is disabled' \
  /org/gnome/mutter/edge-tiling false
check_value 'native left tiling shortcut is released' \
  /org/gnome/mutter/keybindings/toggle-tiled-left '@as []'
check_value 'native right tiling shortcut is released' \
  /org/gnome/mutter/keybindings/toggle-tiled-right '@as []'
# Expected defaults are config/desktop/dconf/db/luma.d/00-luma-desktop.
check_value 'normal desktop loads no dock extension (tiling toggle and tray host only)' \
  /org/gnome/shell/enabled-extensions \
  "['tiling-toggle@project-luma.local', 'appindicatorsupport@rgcjonas.gmail.com']"
# The immutable OS role selects the canonical signed launcher. Invalid role
# metadata must fail; the running dock never supplies its own expectation.
expected_viola_launcher=$(python3 -c '
from luma_installer.native_app_roles import required
print("com.rhyme.viola.desktop" if required("com.rhyme.viola") else "viola-browser.desktop")
')
check_value 'default dock has Filer first, Viola second, Ari third and the complete shipping app order' \
  /org/gnome/shell/favorite-apps \
  "['org.gnome.Nautilus.desktop', '$expected_viola_launcher', 'org.projectluma.Ari.desktop', 'org.projectluma.Notes.desktop', 'org.projectluma.Photos.desktop', 'org.projectluma.Tide.desktop', 'org.projectluma.Calendar.desktop', 'org.projectluma.Tasks.desktop', 'org.projectluma.Contacts.desktop', 'org.projectluma.Messages.desktop', 'org.projectluma.Phone.desktop', 'org.projectluma.Charlie.desktop', 'org.projectluma.Maps.desktop', 'org.projectluma.Weather.desktop', 'org.projectluma.Clock.desktop', 'org.projectluma.VoiceMemos.desktop', 'org.projectluma.Camera.desktop', 'io.luma.Monitor.desktop', 'org.projectluma.Leaf.desktop', 'org.projectluma.Depot.desktop', 'org.projectluma.Terminal.desktop', 'org.projectluma.Viewer.desktop', 'org.gnome.Settings.desktop']"

if [ "$failures" -ne 0 ]; then
  printf '\nDesktop smoke test: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nDesktop smoke test: PASS\n'
