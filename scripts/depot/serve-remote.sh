#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Serve the Luma remote over HTTP on the build host's loopback, laid out
# exactly as dl.simplyluma.com is, for testing before or without hosting.
#
#   serve-remote.sh [PORT]      (default 8787; stop with serve-remote.sh --stop)
#
# Reach it from a test machine through an SSH tunnel, then:
#   flatpak remote-add --user luma http://127.0.0.1:PORT/luma.flatpakrepo
# Content is still verified with the Luma Depot key, so plain HTTP on a
# tunnel changes transport, not trust.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

public="$DEPOT_ROOT/public"
pidfile="$DEPOT_ROOT/run/serve-remote.pid"
if [ "${1:-}" = --stop ]; then
  [ -f "$pidfile" ] && kill "$(cat "$pidfile")" 2>/dev/null || true
  rm -f "$pidfile"
  exit 0
fi
port=${1:-8787}
install -d "$DEPOT_ROOT/run" "$public"
real_root=$(realpath "$DEPOT_ROOT")
ln -sfn "$real_root/repo" "$public/repo"
for entry in "$real_root/site/"*; do ln -sfn "$entry" "$public/$(basename "$entry")"; done
# a copy of the repo file whose Url points at this server
sed "s#^Url=.*#Url=http://127.0.0.1:$port/repo/#" "$real_root/site/luma.flatpakrepo" >"$public/luma-test.flatpakrepo.tmp"
rm -f "$public/luma.flatpakrepo"
mv "$public/luma-test.flatpakrepo.tmp" "$public/luma.flatpakrepo"
[ -f "$pidfile" ] && kill "$(cat "$pidfile")" 2>/dev/null || true
cd "$public"
nohup python3 -m http.server "$port" --bind 127.0.0.1 >"$DEPOT_ROOT/logs/serve-remote.log" 2>&1 &
echo $! >"$pidfile"
depot_log "serving $public on 127.0.0.1:$port (pid $(cat "$pidfile"))"
