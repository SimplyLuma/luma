#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Depot's firmware section against a real fwupd daemon, in a disposable Fedora
# container: fwupd running, stopped, paused and restarting must never close
# Depot. With --depot-rpms DIR (the luma-application-installer RPM under test and
# a luma-developer-platform RPM), the packaged Depot window is also opened on the
# Updates view under Xvfb while fwupd runs, and must still be open 45 seconds later.
#
#   tests/depot/fwupd-container.sh [--depot-rpms DIR] [--keep]
#
# Needs podman. The container runs privileged (fwupd reads /sys and /dev) and is
# removed afterwards unless --keep is given.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
image=${FWUPD_TEST_IMAGE:-registry.fedoraproject.org/fedora:44}
name=luma-depot-fwupd-test-$$
rpms=""
keep=0
while [ $# -gt 0 ]; do
  case "$1" in
    --depot-rpms) rpms=$(realpath "$2"); shift 2 ;;
    --keep) keep=1; shift ;;
    *) printf 'usage: %s [--depot-rpms DIR] [--keep]\n' "$0" >&2; exit 2 ;;
  esac
done

cleanup() { [ "$keep" = 1 ] || podman rm -f "$name" >/dev/null 2>&1 || :; }
trap cleanup EXIT INT TERM

mounts=(--volume "$repo_root/src:/depot/src:ro" --volume "$repo_root/tests/depot:/depot/tests:ro")
[ -n "$rpms" ] && mounts+=(--volume "$rpms:/depot/rpms:ro")
podman run --detach --name "$name" --privileged --security-opt label=disable "${mounts[@]}" \
  "$image" sleep infinity >/dev/null

podman exec "$name" bash -euo pipefail -c '
  dnf5 -y -q install fwupd dbus-daemon python3-gobject gtk4 libadwaita python3-systemd python3-pyyaml \
    python3-libarchive-c flatpak-libs xorg-x11-server-Xvfb procps-ng >/tmp/dnf.log 2>&1 \
    || { tail -20 /tmp/dnf.log; exit 1; }
  rpm -q fwupd python3-gobject glib2 | sed "s/^/INFO /"
  # The test starts and stops fwupd itself, so D-Bus must not start it behind its back.
  rm -f /usr/share/dbus-1/system-services/org.freedesktop.fwupd.service
  mkdir -p /run/dbus
  dbus-daemon --system --fork
'

podman exec --workdir /depot --env PYTHONPATH=/depot/src/luma-depot:/depot/src/luma-installer "$name" \
  python3 -W ignore tests/fwupd_regression.py

if [ -n "$rpms" ]; then
  podman exec --workdir /tmp "$name" bash -euo pipefail -c '
    rpm -i --nodeps --noscripts /depot/rpms/luma-developer-platform-*.rpm \
      /depot/rpms/luma-application-installer-*.noarch.rpm >/dev/null
    rpm -q luma-application-installer | sed "s/^/INFO /"
    /usr/libexec/fwupd/fwupd >/tmp/fwupd.log 2>&1 &
    for _ in $(seq 60); do fwupdmgr get-devices >/dev/null 2>&1 && break; sleep 1; done
    mkdir -p -m 0700 /tmp/runtime
    status=0
    XDG_RUNTIME_DIR=/tmp/runtime timeout 45 xvfb-run -a dbus-run-session -- \
      luma-depot --view updates \
      >/tmp/depot.log 2>&1 || status=$?
    if [ "$status" = 124 ]; then
      echo "PASS Depot window on the Updates view still open after 45 seconds with fwupd running"
    else
      echo "FAIL Depot window closed with status $status"; grep -v MESA /tmp/depot.log | tail -20; exit 1
    fi
    ! grep -q "g_mutex_clear" /tmp/depot.log
  '
fi
