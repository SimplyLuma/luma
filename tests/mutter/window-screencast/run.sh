#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Headless test of window screencasts (patches/mutter 0034): GNOME Shell on a
# virtual 2880x1620 monitor with Xwayland, PipeWire and WirePlumber; windows
# recorded through org.gnome.Mutter.ScreenCast RecordWindow and read with
# GStreamer's pipewiresrc (capture.c). Needs Fedora 44 with gnome-shell and
# the Mutter under test, gtk4-devel, libadwaita, gtk3, python3-gobject,
# mesa-dri-drivers, dbus-daemon, pipewire, wireplumber, pipewire-gstreamer,
# gstreamer1-plugins-base-devel, cairo-devel and Xwayland; no GPU, display
# server or logind session.
#
#   run.sh OUTDIR [luma|stock]
#
# "stock" expects a Mutter whose window streams crop to the whole buffer.
# Per window the capture writes NAME-buffer.png (the buffer) and
# NAME-crop.png (the crop); result.json has every check, the geometry and the
# sampled alpha values. The exit status is the number of failures.
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=$1
expect=${2:-luma}
mkdir -p "$out/build" "$out/runtime" "$out/config/glib-2.0/settings" "$out/data"
chmod 0700 "$out/runtime"
rm -f "$out"/*.png "$out"/*.log "$out"/*.stop "$out/result.json"

gcc -O2 -Wall -Wextra -Werror -Wno-unused-parameter "$here/capture.c" \
  $(pkg-config --cflags --libs gio-2.0 gstreamer-1.0 gstreamer-app-1.0 gstreamer-video-1.0 cairo) \
  -o "$out/build/capture"

cat >"$out/config/glib-2.0/settings/keyfile" <<'KEYFILE'
[org/gnome/shell]
welcome-dialog-last-shown-version='999'

[org/gnome/desktop/interface]
enable-animations=false

[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer', 'xwayland-native-scaling']
KEYFILE

rpm -q mutter gnome-shell gtk4 libadwaita gtk3 pipewire >"$out/packages.txt" 2>&1 || :

status=0
env -u DISPLAY -u WAYLAND_DISPLAY \
  XDG_RUNTIME_DIR="$out/runtime" XDG_CONFIG_HOME="$out/config" \
  XDG_DATA_HOME="$out/data" GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 \
  GTK_A11Y=none NO_AT_BRIDGE=1 \
  LUMA_CAST_OUT="$out" LUMA_CAST_HERE="$here" \
  LUMA_CAST_CAPTURE="$out/build/capture" LUMA_CAST_EXPECT="$expect" \
  dbus-run-session -- sh -c '
    pipewire >"$LUMA_CAST_OUT/pipewire.log" 2>&1 &
    sleep 1
    wireplumber >"$LUMA_CAST_OUT/wireplumber.log" 2>&1 &
    sleep 1
    exec timeout 900 gnome-shell --headless --virtual-monitor 2880x1620 \
      --automation-script="$LUMA_CAST_HERE/driver.js"' \
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
for c in result["cases"]:
    f = c.get("final") or {}
    print(f"  {c['label']}: frame {c['frame']} buffer {c['buffer']} crop {f.get('crop')} "
          f"corners {f.get('corners')} format {f.get('format')}")
print(f"{len(result['results']) - len(failed)} passed, {len(failed)} failed")
sys.exit(min(len(failed), 100))
PY
