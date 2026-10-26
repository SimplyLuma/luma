#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
set -eu
ulimit -c 0
export HOME=/tmp/context-home XDG_RUNTIME_DIR=/tmp/context-run
mkdir -p "$HOME/.config/glib-2.0/settings" "$XDG_RUNTIME_DIR" /tmp/context-schemas
chmod 700 "$XDG_RUNTIME_DIR"
cp /source/src/luma-shell-state/*.gschema.xml /tmp/context-schemas/
glib-compile-schemas --strict /tmp/context-schemas
export GSETTINGS_SCHEMA_DIR=/tmp/context-schemas GSETTINGS_BACKEND=keyfile
dbus-run-session -- bash -c '
  python3 /source/src/luma-shell-state/luma-shell-state-service > /out/context-state.log 2>&1 &
  state=$!
  trap "kill $state 2>/dev/null || true" EXIT
  ready=false
  for attempt in {1..30}; do
    if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.GetNameOwner org.project_luma.ShellState1 >/dev/null 2>&1; then
      ready=true; break
    fi
    sleep 0.1
  done
  $ready
  /out/context-contract
'
