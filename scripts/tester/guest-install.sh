#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Runs only inside the disposable builder guest, via the producer's SSH key.
set -euo pipefail
work=/var/tmp/luma-tester-build
cd "$work"
[ -f /etc/cloud/cloud.cfg ]
grep -qx 'ID=fedora' /etc/os-release
[ -f source/scripts/tester/guest-install.sh ]
# Exact local packages come from the producer's recorded input cohort.
dnf -y --setopt=install_weak_deps=False install --allowerasing rpms/*.rpm \
  greetd gnome-session gnome-session-wayland-session dbus-daemon \
  mesa-dri-drivers mesa-libEGL mesa-libGL pipewire pipewire-pulseaudio wireplumber \
  gnome-keyring gnome-keyring-pam dconf rsync sudo openssh-server \
  xdg-desktop-portal xdg-desktop-portal-gnome xdg-desktop-portal-gtk \
  ibus python3-gobject ModemManager NetworkManager NetworkManager-wifi \
  bluez upower iio-sensor-proxy feedbackd desktop-file-utils \
  rpm-build dnf5-plugins python3-devel xorg-x11-server-Xvfb libXtst \
  evolution-data-server openssl qrencode-libs gstreamer1-plugins-bad-free \
  libnice-gstreamer1 libX11 python3-cryptography python3-websockets python3-httpx

# Build the existing shared apps from the exact committed source and its normal
# RPM spec/checks. No app fork, skipped %check, or relabeled predecessor package.
if ! rpm -q "$(cat expected-apps)" >/dev/null 2>&1; then
  build="$work/app-rpmbuild"
  mkdir -p "$build"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
  cp prairie-core-apps.tar.gz luma-continuity.tar.gz "$build/SOURCES/"
  cp source/LICENSE.md "$build/SOURCES/"
  cp source/src/luma-shell-state/org.project_luma.shell-state.gschema.xml "$build/SOURCES/"
  cp source/packaging/rpm/prairie-core-apps.spec "$build/SPECS/"
  dnf -y --setopt=install_weak_deps=False builddep "$build/SPECS/prairie-core-apps.spec"
  rpmbuild -ba --define "_topdir $build" "$build/SPECS/prairie-core-apps.spec"
  dnf -y --setopt=install_weak_deps=False install "$build"/RPMS/noarch/prairie-core-apps-*.rpm
fi
rpm -q "$(cat expected-apps)"
bash source/scripts/tester/guest-additions.sh
# QEMU profile configuration happens after source/package validation succeeds.
bash source/scripts/tester/guest-configure.sh "$(cat target)" "$(cat build-id)"
dnf clean all
