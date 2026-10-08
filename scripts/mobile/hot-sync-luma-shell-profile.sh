#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  printf 'usage: %s SHARED_DESKTOP_DCONF HANDHELD_DCONF\n' "$0" >&2
  exit 2
}

[ "$#" -eq 2 ] || usage
shared_profile=$1
handheld_profile=$2

for tool in dconf gsettings grep id; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required profile-sync tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(id -u)" -ne 0 ] || {
  printf 'error: run the profile sync as the graphical Luma user, not root\n' >&2
  exit 1
}

for profile in "$shared_profile" "$handheld_profile"; do
  [ -f "$profile" ] || {
    printf 'error: profile does not exist: %s\n' "$profile" >&2
    exit 1
  }
done

# Refuse arbitrary dconf payloads: this tool is intentionally coupled to the
# visible Luma identity and the minimal touch-only overlay.
grep -Fq "enabled-extensions=['dash-to-dock@micxgx.gmail.com', 'tiling-toggle@project-luma.local']" \
  "$shared_profile"
grep -Fq "color-scheme='prefer-light'" "$shared_profile"
grep -Fq "gtk-theme='Luma'" "$shared_profile"
grep -Fq "picture-uri='file:///usr/share/backgrounds/luma/luma-prism.png'" \
  "$shared_profile"
grep -Fq '[org/gnome/desktop/a11y/applications]' "$handheld_profile"
grep -Fq 'screen-keyboard-enabled=true' "$handheld_profile"
grep -Fq 'idle-delay=uint32 60' "$handheld_profile"
grep -Fq "power-button-action='nothing'" "$handheld_profile"
grep -Fq "sleep-inactive-ac-type='nothing'" "$handheld_profile"
grep -Fq "sleep-inactive-battery-type='nothing'" "$handheld_profile"
grep -Fq "enabled-extensions=['dash-to-dock@micxgx.gmail.com', 'tiling-toggle@project-luma.local', 'handheld@project-luma.local']" \
  "$handheld_profile"
grep -Fq "button-layout=':'" "$handheld_profile"
grep -Fq 'auto-maximize=true' "$handheld_profile"
grep -Fq 'extend-height=false' "$handheld_profile"
grep -Fq 'height-fraction=0.984' "$handheld_profile"
grep -Fq 'dash-max-icon-size=48' "$handheld_profile"

dconf load / <"$shared_profile"
dconf load / <"$handheld_profile"

test "$(gsettings get org.gnome.desktop.interface color-scheme)" = "'prefer-light'"
test "$(gsettings get org.gnome.desktop.interface gtk-theme)" = "'Luma'"
test "$(gsettings get org.gnome.desktop.background picture-uri)" = \
  "'file:///usr/share/backgrounds/luma/luma-prism.png'"
test "$(gsettings get org.gnome.desktop.a11y.applications screen-keyboard-enabled)" = true
test "$(gsettings get org.gnome.desktop.session idle-delay)" = 'uint32 60'
test "$(gsettings get org.gnome.settings-daemon.plugins.power power-button-action)" = "'nothing'"
test "$(gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type)" = "'nothing'"
test "$(gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type)" = "'nothing'"
test "$(gsettings get org.gnome.desktop.wm.preferences button-layout)" = "':'"
test "$(gsettings get org.gnome.mutter auto-maximize)" = true
test "$(gsettings get org.gnome.shell.extensions.dash-to-dock dock-fixed)" = true
test "$(gsettings get org.gnome.shell.extensions.dash-to-dock autohide)" = false
test "$(gsettings get org.gnome.shell.extensions.dash-to-dock intellihide)" = false
test "$(gsettings get org.gnome.shell.extensions.dash-to-dock extend-height)" = false
dock_height_fraction=$(
  gsettings get org.gnome.shell.extensions.dash-to-dock height-fraction
)
awk -v value="$dock_height_fraction" 'BEGIN {
  exit !(value > 0.983 && value < 0.985)
}'
test "$(gsettings get org.gnome.shell.extensions.dash-to-dock dash-max-icon-size)" = 48
test "$(gsettings get org.gnome.shell enabled-extensions)" = \
  "['dash-to-dock@micxgx.gmail.com', 'tiling-toggle@project-luma.local', 'handheld@project-luma.local']"

printf 'Luma shared desktop profile and handheld capability overlay are active.\n'
