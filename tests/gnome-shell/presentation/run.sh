#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
set -eu
export PRESENTATION_SOURCE=${PRESENTATION_SOURCE:-/source}
export PRESENTATION_OUT=${PRESENTATION_OUT:-/out}
export HOME=/tmp/presentation-home XDG_RUNTIME_DIR=/tmp/presentation-run
mkdir -p "$HOME/.config/glib-2.0/settings" "$HOME/.local/share/applications" "$XDG_RUNTIME_DIR" "$PRESENTATION_OUT" /tmp/presentation-schemas
chmod 700 "$XDG_RUNTIME_DIR"
cp "$PRESENTATION_SOURCE/src/luma-shell-state/"*.gschema.xml /tmp/presentation-schemas/
glib-compile-schemas --strict /tmp/presentation-schemas
if [ -n "${PRESENTATION_WIDGETS_LIB:-}" ]; then
    export GI_TYPELIB_PATH="$PRESENTATION_WIDGETS_LIB${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    export LD_LIBRARY_PATH="$PRESENTATION_WIDGETS_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
gjs -m "$PRESENTATION_SOURCE/tests/gnome-shell/presentation/grid.js"
export GSETTINGS_SCHEMA_DIR=/tmp/presentation-schemas GSETTINGS_BACKEND=keyfile
export LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
# Only inside this networkless, capability-free test container; screenshot
# encoding otherwise attempts an unavailable nested user namespace.
mkdir -p /tmp/presentation-bin
install -m 0755 "$PRESENTATION_SOURCE/tests/gnome-shell/presentation/container-image-loader" /tmp/presentation-bin/bwrap
export PATH=/tmp/presentation-bin:$PATH
cat > "$HOME/.config/glib-2.0/settings/keyfile" <<'EOF'
[org/gnome/shell]
enabled-extensions=[]
disable-user-extensions=true
welcome-dialog-last-shown-version='999'
EOF
if [ "${PRESENTATION_DESKTOP_DRAG_ONLY:-0}" = 1 ] || [ "${PRESENTATION_OPTIONS_ONLY:-0}" = 1 ]; then
    sed -i "s/enabled-extensions=\[\]/enabled-extensions=['tilingshell@ferrarodomenico.com','tiling-toggle@project-luma.local']/; s/disable-user-extensions=true/disable-user-extensions=false/" "$HOME/.config/glib-2.0/settings/keyfile"
fi
if [ "${PRESENTATION_OPTIONS_ONLY:-0}" = 1 ]; then
    cat >> "$HOME/.config/glib-2.0/settings/keyfile" <<'EOF'
[org/gnome/desktop/interface]
icon-theme='Prairie'
EOF
fi
for fixture in graphical terminal hidden; do
  terminal=false; hidden=false; name='Graphical fixture'
  if [ "$fixture" = terminal ]; then terminal=true; name='Terminal command fixture'; fi
  if [ "$fixture" = hidden ]; then hidden=true; name='Hidden fixture'; fi
  cat > "$HOME/.local/share/applications/org.example.$fixture.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Exec=true
Icon=application-x-executable
Terminal=$terminal
NoDisplay=$hidden
EOF
done
for index in {1..64}; do
  cat > "$HOME/.local/share/applications/org.example.Grid$index.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Grid fixture $index
Exec=true
Icon=application-x-executable
EOF
done
for fixture in A B; do
  cat > "$HOME/.local/share/applications/org.example.Split$fixture.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Split fixture $fixture
Exec=python3 $PRESENTATION_SOURCE/tests/gnome-shell/presentation/window.py --application-id org.example.Split$fixture --title Split$fixture --no-decorations
Icon=application-x-executable
EOF
done
export DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/presentation-system-bus
# Isolated fixture bus, not the host system bus. A system-config daemon drops
# UID, which is deliberately forbidden by the container's empty capability set.
dbus-daemon --session --fork --nopidfile --address="$DBUS_SYSTEM_BUS_ADDRESS"
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId
timeout 120 dbus-run-session -- bash -c '
  python3 "$PRESENTATION_SOURCE/src/luma-shell-state/luma-shell-state-service" > "$PRESENTATION_OUT/state.log" 2>&1 &
  state=$!
  trap "kill $state 2>/dev/null || true" EXIT
  monitors=(--virtual-monitor "${PRESENTATION_MONITOR:-1280x800}")
  if [ -n "${PRESENTATION_SECOND_MONITOR:-}" ]; then monitors+=(--virtual-monitor "$PRESENTATION_SECOND_MONITOR"); fi
  gnome-shell --headless --no-x11 "${monitors[@]}" \
    --automation-script="$PRESENTATION_SOURCE/tests/gnome-shell/presentation/native.js"
' > "$PRESENTATION_OUT/shell.log" 2>&1
python3 - "$PRESENTATION_OUT/results.json" <<'PY'
import json,sys
checks=json.load(open(sys.argv[1]))
for check in checks:
    print(('PASS' if check['ok'] else 'FAIL'),check['name'],check['detail'])
assert checks and all(item['ok'] for item in checks)
PY
