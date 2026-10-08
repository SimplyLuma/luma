#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Render Settings › Mouse & Touchpad › TrackPoint (patch 0021) in light and
# dark in a disposable Fedora 44 container. $1: work dir holding rpms/ (the
# candidate Settings plus the image's toolkit, icon theme and font); the repo's
# dconf defaults supply the font. Output in $1/out: {light,dark}.png, trees,
# drive logs. The touchpad page's clips play through GStreamer's GL sink, so
# GLES must be installed or Settings aborts.
set -euo pipefail
S=$(realpath "$1"); here=$(dirname "$(realpath "$0")"); repo=$(realpath "$here/../../..")
cp "$here/devices.umockdev" "$here/drive.py" "$here/run.sh" "$S/"
rm -rf "$S/dconf"; cp -r "$repo/config/desktop/dconf" "$S/dconf"
podman run --rm --security-opt label=disable -v "$S:/work" -w /work --memory 4g \
  registry.fedoraproject.org/fedora@sha256:69b1219730fe52d6cbb0874cbc1a36dd43b722354a603f6cdf73acbff8dd7c59 bash -c '
    set -e
    dnf5 -y -q install --setopt=install_weak_deps=False /work/rpms/*.rpm xorg-x11-server-Xvfb dbus-daemon dbus-tools \
      python3-gobject at-spi2-core ImageMagick adwaita-icon-theme procps-ng umockdev dconf libglvnd-gles mesa-dri-drivers >/work/dnf.log 2>&1 || { tail -20 /work/dnf.log; exit 1; }
    rpm -q gnome-control-center gtk4 libadwaita prairie-icon-theme google-figtree-fonts
    mkdir -p /etc/dconf/profile /etc/dconf/db
    cp /work/dconf/profile/user /etc/dconf/profile/user
    cp -r /work/dconf/db/luma.d /etc/dconf/db/
    dconf update
    # Settings keeps a system bus connection in its object storage.
    mkdir -p /run/dbus; rm -f /run/dbus/system_bus_socket /run/dbus/pid
    dbus-daemon --system --fork
    for i in $(seq 1 50); do [ -S /run/dbus/system_bus_socket ] && break; sleep 0.1; done
    status=0
    for t in light dark; do OUT=/work/out bash /work/run.sh $t || status=1; done
    grep -q "0 failure" /work/out/light-drive.log && grep -q "0 failure" /work/out/dark-drive.log
'
