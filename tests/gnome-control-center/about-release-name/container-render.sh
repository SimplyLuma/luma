#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Render Settings › About and System Details (ADR-040 verification) in a
# disposable Fedora 44 container with the image's toolkit, luma-logos and a
# candidate Settings build, against a real hostnamed and a Luma os-release.
# $1: work dir holding rpms/ (candidate + toolkit RPMs); output in $1/out.
# Current os-release: {light,dark}-{1x,2x}; legacy (pre-release-name image, no
# LOGO or VERSION_CODENAME) with a stand-in luma-update BootedName:
# legacy-{light,dark}-1x; luma-release's own os-release (no IMAGE_VERSION):
# package-{light,dark}-1x.
set -euo pipefail
S=$(realpath "$1"); here=$(dirname "$(realpath "$0")")
cp "$here/os-release" "$here/os-release-legacy" "$here/os-release-package" "$here/mock_update.py" "$here/drive_about.py" "$here/run-about.sh" "$S/"
podman run --rm --no-hostname --security-opt label=disable -v "$S:/work" -w /work --memory 4g \
  registry.fedoraproject.org/fedora@sha256:69b1219730fe52d6cbb0874cbc1a36dd43b722354a603f6cdf73acbff8dd7c59 bash -c '
    set -e
    dnf5 -y -q install --setopt=install_weak_deps=False /work/rpms/*.rpm xorg-x11-server-Xvfb dbus-daemon dbus-tools \
      python3-gobject at-spi2-core ImageMagick adwaita-icon-theme procps-ng systemd glib2 gtk-update-icon-cache xdotool >/work/dnf.log 2>&1 || { tail -20 /work/dnf.log; exit 1; }
    rpm -q gnome-control-center gtk4 libadwaita luma-logos prairie-icon-theme
    cp /work/os-release /usr/lib/os-release; rm -f /etc/os-release; ln -s ../usr/lib/os-release /etc/os-release
    ls -la --time-style=+%s /usr/share/icons/hicolor/icon-theme.cache /usr/share/icons/hicolor/scalable/apps/luma-logo-text.svg
    for n in luma-logo luma-logo-dark luma-logo-text luma-logo-text-dark; do
      printf "hicolor cache has %s: %s\n" $n "$(python3 -c "import sys; print(open(\"/usr/share/icons/hicolor/icon-theme.cache\",\"rb\").read().count(sys.argv[1].encode()+bytes(1)))" $n)"
    done
    mkdir -p /run/dbus; rm -f /run/dbus/system_bus_socket /run/dbus/pid
    dbus-daemon --system --fork
    for i in $(seq 1 50); do [ -S /run/dbus/system_bus_socket ] && break; sleep 0.1; done
    # --no-hostname: hostnamed must be able to replace /etc/hostname.
    echo luma > /etc/hostname
    /usr/lib/systemd/systemd-hostnamed >/work/hostnamed.log 2>&1 &
    sleep 1
    cat > /etc/dbus-1/system.d/org.projectluma.Update1-test.conf <<"CONF"
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><policy user="root"><allow own="org.projectluma.Update1"/></policy>
<policy context="default"><allow send_destination="org.projectluma.Update1"/></policy></busconfig>
CONF
    dbus-send --system --type=method_call --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig; sleep 1
    python3 /work/mock_update.py "Luma (Prairie, Beta 0, Nightly 20260916)" 1.0.0-nightly.20260917.5 >/work/mock-update.log 2>&1 &
    sleep 1
    DEVICE_NAME_TEST=1 OUT=/work/out bash /work/run-about.sh light-1x
    for t in dark-1x light-2x dark-2x; do OUT=/work/out bash /work/run-about.sh $t; done
    echo "mock BootedName reads during current os-release runs: $(grep -c ^Get /work/mock-update.log)"
    cp /work/os-release-legacy /usr/lib/os-release
    for t in light-1x dark-1x; do EXPECT_NAME="Luma (Prairie, Beta 0, Nightly 20260916)" OUT=/work/out bash /work/run-about.sh legacy-$t; done
    cp /work/os-release-package /usr/lib/os-release
    for t in light-1x dark-1x; do EXPECT_NAME="Luma (Prairie, Beta 0, Nightly 20260916)" OUT=/work/out bash /work/run-about.sh package-$t; done
    echo "== mock luma-update"; cat /work/mock-update.log
    grep -h "CRITICAL" /work/out/*-settings.log | sort | uniq -c | head -5 || true
    echo /etc/hostname: $(cat /etc/hostname)'
