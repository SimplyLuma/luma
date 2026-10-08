#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Windows open straight into their tile: run open-trace.js in a headless Shell.
# Expects tests/tiling-shell/window-memory at /wm/test and this directory at
# /wm/open inside a Fedora 44 container with the Luma Shell and Mutter, gtk4,
# libadwaita, python3-gobject, python3-xlib, Xwayland and chromium installed.
#   run.sh [extension-source-dir]   (OPEN_TRACE_ONLY=key,key to run some clients)
# Exits non-zero when any check fails, and when nothing was checked at all.
set -u
out=${WM_OUT:-/wm/out-open}
WM_SCRIPT=/wm/open/open-trace.js WM_OUT=$out OPEN_TRACE_DIR=/wm/test \
  bash /wm/test/run.sh "${1:-}" >/dev/null 2>&1
log=$out/shell.log
grep -E '\[opentrace\] ' "$log" | sed 's/.*\[opentrace\] //'
done_line=$(grep -c '\[opentrace\] DONE ' "$log")
failed=$(grep -c '\[opentrace\] FAIL ' "$log")
passed=$(grep -c '\[opentrace\] PASS ' "$log")
if [ "$done_line" -ne 1 ] || [ "$passed" -eq 0 ] || [ "$failed" -ne 0 ]; then
  echo "open-trace: $passed passed, $failed failed, finished $done_line time(s)" >&2
  exit 1
fi
echo "open-trace: $passed passed"
