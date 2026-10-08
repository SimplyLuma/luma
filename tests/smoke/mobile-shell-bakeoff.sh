#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
config_dir=$repo_root/config/mobile/shell-bakeoff
switcher=$repo_root/scripts/mobile/luma-shell-bakeoff-switch
gnome_mobile_launcher=$repo_root/scripts/mobile/luma-gnome-mobile-session
plasma_mobile_launcher=$repo_root/scripts/mobile/luma-plasma-mobile-session
gnome_mobile_inputs=$repo_root/config/mobile/gnome-mobile-source.env

pass=0
check() {
  description=$1
  shift
  "$@"
  pass=$((pass + 1))
  printf 'PASS: %s\n' "$description"
}

for candidate in luma phosh gnome-mobile plasma-mobile; do
  config=$config_dir/greetd-$candidate.toml
  check "$candidate config exists" test -f "$config"
  check "$candidate has one initial session" \
    test "$(grep -c '^\[initial_session\]$' "$config")" -eq 1
  check "$candidate runs initial session as luma" \
    test "$(grep -c '^user = "luma"$' "$config")" -eq 1
done

check 'switcher accepts only explicit candidates' \
  grep -Fq 'luma|phosh|gnome-mobile|plasma-mobile' "$switcher"
check 'switcher validates Fedora 44' \
  grep -Fq '[ "${VERSION_ID:-}" = 44 ]' "$switcher"
check 'switcher validates FP6 device tree' \
  grep -Fq "die 'device-tree identity is not Fairphone 6'" "$switcher"
check 'switcher recovery target is Phosh' \
  grep -Fq 'rescue_config=$candidate_dir/greetd-phosh.toml' "$switcher"
check 'switcher quiesces stale graphical-session state' \
  grep -Fq 'graphical-session.target || true' "$switcher"
check 'GNOME Mobile has immutable source pins' bash -c \
  'test "$(grep -Ec "_COMMIT=[0-9a-f]{40}$" "$1")" -eq 8' _ \
  "$gnome_mobile_inputs"
check 'GNOME Mobile carries the eleven reviewed compatibility patches' bash -c \
  'test "$(find "$1/patches/gnome-mobile" -maxdepth 1 -type f -name "*.patch" | wc -l | tr -d " ")" -eq 11' _ \
  "$repo_root"
check 'GNOME Mobile build includes private GJS' \
  grep -Fq 'setup_build "$workdir/build-gjs"' \
  "$repo_root/scripts/mobile/build-gnome-mobile-bakeoff.sh"
check 'GNOME Mobile build applies every reviewed patch' \
  grep -Fq 'git -C "$src/gnome-shell-mobile" apply "$patch_file"' \
  "$repo_root/scripts/mobile/build-gnome-mobile-bakeoff.sh"
check 'GNOME Mobile build removes staging runtime paths' \
  grep -Fq 'patchelf --set-rpath' \
  "$repo_root/scripts/mobile/build-gnome-mobile-bakeoff.sh"
check 'GNOME Mobile launcher uses only the private Shell binary' \
  grep -Fq '"$prefix/bin/gnome-shell" --wayland --display-server --mode=user' \
  "$gnome_mobile_launcher"
check 'GNOME Mobile launcher marks only its greetd compatibility session' \
  grep -Fq 'export LUMA_GREETD_SESSION=1' "$gnome_mobile_launcher"
check 'GNOME Mobile greetd session never enters unavailable GDM lock' \
  grep -Fq 'return !this._isLumaGreetdSession' \
  "$repo_root/patches/gnome-mobile/0009-skip-gdm-lock-in-greetd-bakeoff.patch"
check 'GNOME Mobile greetd session ignores stale GDM lock state' \
  grep -Fq "GLib.getenv('LUMA_GREETD_SESSION') !== '1'" \
  "$repo_root/patches/gnome-mobile/0010-skip-stale-gdm-lock-restore-in-greetd.patch"
check 'GNOME Mobile idle blank checks lock policy before unlock UI' bash -c \
  'guard=$(grep -n "if (!this._lockEnabled())" "$1" | head -n1 | cut -d: -f1); dialog=$(grep -n "^+.*Main.screenShield.activate(false)" "$1" | head -n1 | cut -d: -f1); test "$guard" -lt "$dialog"' _ \
  "$repo_root/patches/gnome-mobile/0011-skip-unavailable-unlock-dialog-on-idle-blank.patch"
check 'GNOME Mobile launcher has no dynamic-loader tracing' bash -c \
  '! grep -Eq "LD_DEBUG|gnome-mobile-loader" "$1"' _ \
  "$gnome_mobile_launcher"
check 'Plasma Mobile launcher delegates to Fedora packaged session' \
  grep -Fq 'exec /usr/libexec/plasma-dbus-run-session-if-needed' \
  "$plasma_mobile_launcher"
check 'Plasma Mobile launcher selects the packaged mobile starter' \
  grep -Fq '/usr/bin/startplasmamobile >>"$session_log" 2>&1' \
  "$plasma_mobile_launcher"
check 'Plasma Mobile launcher identifies a Wayland handheld session' bash -c \
  'grep -Fq "export XDG_SESSION_DESKTOP=plasma-mobile" "$1" && grep -Fq "export LUMA_DEVICE_CLASS=handheld" "$1"' _ \
  "$plasma_mobile_launcher"
check 'switcher clears candidate-specific KDE environment' \
  grep -Fq 'KDE_FULL_SESSION KDE_SESSION_VERSION PLASMA_PLATFORM' "$switcher"
check 'GNOME Mobile readiness checks the private executable' \
  grep -Fq '/opt/luma/gnome-mobile-48/bin/gnome-shell' "$switcher"
check 'switcher never invokes reboot or partition tools' bash -c \
  '! grep -Eq "(^|[[:space:]])(reboot|poweroff|fastboot|flash|mkfs|mount|umount)([[:space:]]|$)" "$1"' _ "$switcher"

printf 'Mobile shell bake-off smoke checks passed: %s\n' "$pass"
