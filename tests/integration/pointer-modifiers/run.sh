#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Headless end-to-end test for modifier state on the window under the pointer.
# Needs a Fedora 44 environment with mutter, python3-gobject, gtk4 (and gtk3),
# mesa-dri-drivers and dbus-daemon; no GPU, display server or logind session.
#
#   run.sh [--mutter PATH] [--target-toolkit gtk4|gtk3] [--workdir DIR]
#
# A locally built Mutter can be used with LD_LIBRARY_PATH pointing at its
# build tree. Exit status is the number of failed checks.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
workdir=""
forward=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --workdir) workdir=$2; shift 2 ;;
    *) forward+=("$1"); shift ;;
  esac
done
[ -n "$workdir" ] || workdir=$(mktemp -d "${TMPDIR:-/tmp}/luma-pointer-modifiers.XXXXXX")

runtime=$(mktemp -d "${TMPDIR:-/tmp}/luma-pm-runtime.XXXXXX")
chmod 0700 "$runtime"
trap 'rm -rf "$runtime"' EXIT

export XDG_RUNTIME_DIR=$runtime
export XDG_CONFIG_HOME=$runtime/config XDG_DATA_HOME=$runtime/data XDG_CACHE_HOME=$runtime/cache
export GSETTINGS_BACKEND=memory
export LIBGL_ALWAYS_SOFTWARE=1
unset DISPLAY WAYLAND_DISPLAY

dbus-run-session -- python3 "$here/driver.py" --workdir "$workdir" "${forward[@]}"
