#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Complete the converged desktop/mobile extension stack inside the builder VM.
set -euo pipefail
work=/var/tmp/luma-tester-build
cd "$work"
# shellcheck disable=SC1091
. source/config/desktop/inputs.env
dnf -y --setopt=install_weak_deps=False install git-core cpio gjs rpm-build dnf5-plugins
if compgen -G 'rpms/*.rpm' >/dev/null; then
  dnf -y --setopt=install_weak_deps=False install --allowerasing rpms/*.rpm
fi
if ! rpm -q "$DASH_TO_DOCK_NEVRA" >/dev/null 2>&1; then
  printf '%s  %s\n' "$DASH_TO_DOCK_SRPM_SHA256" "$DASH_TO_DOCK_SRPM" | sha256sum -c -
  top=$work/dock-rpmbuild
  mkdir -p "$top"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work/dock-extract"
  (cd "$work/dock-extract"; rpm2cpio "$work/$DASH_TO_DOCK_SRPM" >payload.cpio; cpio -idm --quiet <payload.cpio; rm payload.cpio)
  mv "$work/dock-extract/gnome-shell-extension-dash-to-dock.spec" "$top/SPECS/"
  cp "$work/dock-extract/"* "$top/SOURCES/"
  cp source/patches/dash-to-dock/0001-luma-dock-material.patch "$top/SOURCES/"
  (cd "$top/SPECS"; git apply "$work/source/patches/dash-to-dock/0000-luma-fedora-spec.patch")
  dnf -y --setopt=install_weak_deps=False builddep "$top/SPECS/gnome-shell-extension-dash-to-dock.spec"
  rpmbuild -ba --define "_topdir $top" "$top/SPECS/gnome-shell-extension-dash-to-dock.spec"
  dnf -y --setopt=install_weak_deps=False install "$top/RPMS/noarch/$DASH_TO_DOCK_NEVRA.rpm"
fi
rpm -q "$DASH_TO_DOCK_NEVRA"
