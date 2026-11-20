#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Headless test of luma_window_shape_v1 and of the configure that ends an
# interactive resize (patches/mutter 0032, 0033). Runs GNOME Shell on a
# virtual 1920x1080 monitor with the Mutter under test and drives real
# pointer drags (driver.js). Needs Fedora 44 with gnome-shell and that
# Mutter, gtk4-devel, wayland-devel, libadwaita, gtk3, python3-gobject,
# mesa-dri-drivers and dbus-daemon; no GPU, display server or logind session.
#
#   run.sh OUTDIR [luma|stock]
#
# "stock" expects a Mutter without the protocol: the kit call must be a
# silent no-op and nothing else may change. Screenshots, the client logs and
# result.json land in OUTDIR; the exit status is the number of failures.
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
out=$1
expect=${2:-luma}
mkdir -p "$out/build" "$out/runtime" "$out/config/glib-2.0/settings" "$out/data"
chmod 0700 "$out/runtime"
rm -f "$out"/*.png "$out"/*.log "$out/result.json"

protocol=$repo/src/luma-platform/protocols/luma-window-shape-v1.xml
wayland-scanner client-header "$protocol" "$out/build/luma-window-shape-v1-client-protocol.h"
wayland-scanner private-code "$protocol" "$out/build/luma-window-shape-v1-client-protocol.c"
# The client is built from the kit's own luma-window-shape.c.
gcc -O1 -Wall -Wextra -Werror -Wno-unused-parameter -DLUMA_HAVE_WAYLAND \
  -I"$out/build" -I"$repo/src/luma-platform/ui" \
  "$here/client.c" "$repo/src/luma-platform/ui/luma-window-shape.c" \
  "$out/build/luma-window-shape-v1-client-protocol.c" \
  $(pkg-config --cflags --libs gtk4 wayland-client) -o "$out/build/client"

cat >"$out/config/glib-2.0/settings/keyfile" <<'KEYFILE'
[org/gnome/shell]
welcome-dialog-last-shown-version='999'

[org/gnome/desktop/interface]
enable-animations=false
KEYFILE

rpm -q mutter gnome-shell gtk4 libadwaita gtk3 >"$out/packages.txt" 2>&1 || :

status=0
env -u DISPLAY -u WAYLAND_DISPLAY \
  XDG_RUNTIME_DIR="$out/runtime" XDG_CONFIG_HOME="$out/config" \
  XDG_DATA_HOME="$out/data" GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 \
  GTK_A11Y=none NO_AT_BRIDGE=1 MUTTER_DEBUG=wayland \
  LUMA_SHAPE_OUT="$out" LUMA_SHAPE_HERE="$here" \
  LUMA_SHAPE_CLIENT="$out/build/client" LUMA_SHAPE_EXPECT="$expect" \
  dbus-run-session -- timeout 600 gnome-shell --headless --no-x11 \
  --virtual-monitor 1920x1080 --automation-script="$here/driver.js" \
  >"$out/shell.log" 2>&1 || status=$?

python3 - "$out/result.json" "$status" <<'PY'
import json, sys
path, status = sys.argv[1], int(sys.argv[2])
try:
    result = json.load(open(path))
except FileNotFoundError:
    print(f"no result: gnome-shell exited with {status}")
    sys.exit(1)
failed = [r for r in result["results"] if not r["ok"]]
for r in result["results"]:
    print(("PASS " if r["ok"] else "FAIL ") + r["name"] + ("  -- " + r["detail"] if not r["ok"] else ""))
print(f"{len(result['results']) - len(failed)} passed, {len(failed)} failed; "
      f"{len(result['shots'])} screenshots")
sys.exit(min(len(failed), 100))
PY
