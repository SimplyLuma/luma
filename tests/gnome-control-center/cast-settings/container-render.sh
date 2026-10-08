#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Install the pinned Luma toolkit and the candidate Settings (.16) in a
# disposable Fedora 44 container on the Cast UI exec volume, then render
# Settings › Displays › Cast (0014-0016 build). Also runs plain Displays and Apps as controls,
# so a startup failure can be told apart from the Cast page.
set -euo pipefail
W=/mnt/luma-secondary/luma-cast-ui
P="podman --root $W/fs/podman/storage --runroot /run/luma-cast-ui-podman-runroot --storage-opt overlay.imagestore=/var/lib/containers/storage"
POOL=/mnt/luma-secondary/luma-build/os-release/fs/rpms/pool
G=$W/fs/gcc-rpmbuild
S=$W/fs/settings-render
rm -rf "$S"; mkdir -p "$S/rpms"
cp $POOL/gtk4-4.22.4-1.luma.17.preview1.fc44.x86_64.rpm $POOL/libadwaita-1.9.3-1.luma.45.preview20260914.fc44.x86_64.rpm \
   $POOL/google-figtree-fonts-2.0.3-1.luma.3.fc44.noarch.rpm $POOL/luma-shell-state-0.2.0-1.luma.26.preview20260911.fc44.noarch.rpm \
   $POOL/prairie-icon-theme-0.1.0-1.luma.25.fc44.noarch.rpm \
   /mnt/luma-secondary/luma-build/depot/fs/rpms/x86_64/luma-developer-platform-0.1.0-1.luma.62~preview.20260910.1.fc44.x86_64.rpm \
   $G/RPMS/x86_64/gnome-control-center-50.4-1.luma.16.preview20260915.1.fc44.x86_64.rpm \
   $G/RPMS/noarch/gnome-control-center-filesystem-50.4-1.luma.16.preview20260915.1.fc44.noarch.rpm \
   "$S/rpms/"
cp -r $W/settings-render/* "$S/"
$P rm -f luma-cast-ui-settings-render >/dev/null 2>&1 || true
$P run --rm --name luma-cast-ui-settings-render --security-opt label=disable -v "$S:/work" -w /work \
  --memory 4g registry.fedoraproject.org/fedora:44 bash -c '
    set -e
    dnf5 -y -q install --setopt=install_weak_deps=False /work/rpms/*.rpm xorg-x11-server-Xvfb dbus-daemon dbus-tools \
      python3-gobject at-spi2-core ImageMagick adwaita-icon-theme procps-ng gdb >/work/dnf.log 2>&1 || { tail -20 /work/dnf.log; exit 1; }
    rpm -q gnome-control-center gtk4 libadwaita
    mkdir -p /work/schemas /usr/share/icons/hicolor/scalable/status /run/dbus
    cp /usr/share/glib-2.0/schemas/*.xml /work/schemas/ 2>/dev/null || true
    cp /work/org.projectluma.cast.gschema.xml /work/schemas/
    glib-compile-schemas /work/schemas 2>/dev/null
    cp /work/icons/*.svg /usr/share/icons/hicolor/scalable/status/
    gtk4-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
    # gnome-control-center asserts on a missing system bus (its object
    # storage stores a NULL proxy), so the harness needs a real one.
    rm -f /run/dbus/system_bus_socket /run/dbus/pid
    dbus-daemon --system --fork
    for i in $(seq 1 50); do [ -S /run/dbus/system_bus_socket ] && break; sleep 0.1; done
    dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null \
      || { echo "system bus did not start" >&2; exit 1; }
    OUT=/work/out bash /work/run-cast-settings.sh dark-apps applications
    OUT=/work/out bash /work/run-cast-settings.sh dark-display display
    OUT=/work/out bash /work/run-cast-settings.sh dark-cast display cast
    OUT=/work/out bash /work/run-cast-settings.sh light-cast display cast
    for t in dark-apps dark-display dark-cast light-cast; do
      echo "== $t"; cat /work/out/$t-drive.log
      grep -n "ERROR\|Bail out\|CRITICAL" /work/out/$t-settings.log | head -4 || true
    done'
