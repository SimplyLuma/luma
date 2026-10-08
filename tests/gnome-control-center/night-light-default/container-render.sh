#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Headless verification for the Night Light Color Temperature default
# (config/desktop/dconf/db/luma.d/00-luma-desktop, installed by
# image/luma-desktop/scripts/configure-system.sh and compiled with
# `dconf update`, exactly as it is in the real image).
#
# Installs stock Fedora 44 gnome-control-center (Luma does not patch the
# Night Light panel, so the distro build is representative) in a disposable
# container, lays down the same /etc/dconf/profile/user and
# /etc/dconf/db/luma.d/00-luma-desktop the image build installs, runs
# `dconf update`, then renders Settings > Displays > Night Light in Xvfb
# with a fresh $HOME (no user dconf value) and checks the Color Temperature
# slider starts at its far-left (4700K) end.
#
# Usage: container-render.sh
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
test_dir="$repo_root/tests/gnome-control-center/night-light-default"

work_root="$repo_root/build/tests/night-light-default"
rm -rf "$work_root"
mkdir -p "$work_root/out" "$work_root/dconf/profile" "$work_root/dconf/db/luma.d"
cp "$test_dir"/drive_night_light.py "$test_dir"/run-night-light.sh "$work_root/"
cp "$test_dir/check-shipped-override.sh" "$work_root/"
cp "$repo_root/config/desktop/dconf/profile/user" "$work_root/dconf/profile/user"
cp "$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop" "$work_root/dconf/db/luma.d/00-luma-desktop"

podman run --rm --name luma-night-light-default-render \
  --security-opt label=disable \
  -v "$work_root:/work" -w /work \
  --memory 4g \
  registry.fedoraproject.org/fedora:44 bash -c '
    set -e
    dnf5 -y -q install --setopt=install_weak_deps=False \
      gnome-control-center xorg-x11-server-Xvfb dbus-daemon dbus-tools \
      python3-gobject at-spi2-core ImageMagick dconf \
      >/work/dnf.log 2>&1 || { tail -40 /work/dnf.log; exit 1; }
    rpm -q gnome-control-center gnome-settings-daemon dconf

    echo "== shipped-override check (against the real image dconf layout)"
    install -D -m 0644 /work/dconf/profile/user /etc/dconf/profile/user
    install -D -m 0644 /work/dconf/db/luma.d/00-luma-desktop /etc/dconf/db/luma.d/00-luma-desktop
    dconf update
    bash /work/check-shipped-override.sh /etc/dconf/db/luma.d/00-luma-desktop

    mkdir -p /run/dbus
    rm -f /run/dbus/system_bus_socket /run/dbus/pid
    dbus-daemon --system --fork
    for i in $(seq 1 50); do [ -S /run/dbus/system_bus_socket ] && break; sleep 0.1; done
    dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null \
      || { echo "system bus did not start" >&2; exit 1; }

    echo "== headless Night Light render"
    OUT=/work/out bash /work/run-night-light.sh night-light
    render_status=$?
    echo "== drive log"
    cat /work/out/night-light-drive.log 2>/dev/null || true
    exit $render_status
  '
status=$?
echo "night-light-default headless verification: $([ "$status" -eq 0 ] && echo PASS || echo FAIL)"
echo "artifacts: $work_root/out"
exit $status
