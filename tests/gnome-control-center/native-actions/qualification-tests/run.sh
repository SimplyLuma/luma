#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
if test "$#" != 2; then
 echo 'Usage: run.sh PATCHED_SETTINGS_SOURCE MATCHING_SETTINGS_BUILD' >&2
 exit 2
fi
for tool in gcc pkg-config python3 glib-compile-schemas dbus-run-session pulseaudio pactl pacat Xvfb timeout; do
 command -v "$tool" >/dev/null || { echo "Missing dependency: $tool" >&2; exit 1; }
done
test "$(id -u)" != 0 || { echo 'Run as an ordinary user in a disposable build environment.' >&2; exit 1; }
source=$(CDPATH= cd -- "$1" && pwd)
build=$(CDPATH= cd -- "$2" && pwd)
tests=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
runtime=$(mktemp -d /tmp/luma-sound-input-test-XXXXXX)
trap 'rm -rf "$runtime"' EXIT HUP INT TERM
export GSETTINGS_BACKEND=memory G_DEBUG=fatal-warnings
# All data/config writes and fake desktop entries are private to this run.
export XDG_DATA_HOME="$runtime/data" XDG_CONFIG_HOME="$runtime/config"
mkdir -p "$XDG_DATA_HOME/applications" "$XDG_CONFIG_HOME"
for app in SettingsProbe Alternate; do
 cat > "$XDG_DATA_HOME/applications/org.example.$app.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$app
Exec=true
X-Flatpak=org.example.$app
X-GNOME-UsesNotifications=true
MimeType=x-scheme-handler/http;x-scheme-handler/mailto;text/calendar;audio/x-vorbis+ogg;video/x-ogm+ogg;image/jpeg;
EOF
done
# Compile the current production writer, rather than a hand-copied test implementation.
# pkg-config returns compiler/linker tokens, which intentionally undergo shell splitting.
flags=$(pkg-config --cflags --libs gtk4 json-glib-1.0 gio-unix-2.0)
gcc -g -O0 -Wall -Wno-deprecated-declarations -I"$source/shell" -c "$source/shell/cc-luma-live-gsettings.c" -o "$runtime/gsettings.o" $flags
for name in gsettings permissions notifications app-portals defaults shelf windows; do
 extra="$runtime/gsettings.o"
 test "$name" != gsettings || extra=
 test "$name" != shelf || extra="$extra $source/shell/cc-luma-desktop.c"
 gcc -g -O0 -Wall -Wno-deprecated-declarations -I"$source/shell" "$tests/test-native-$name.c" "$source/shell/cc-luma-fixture.c" $extra -o "$runtime/test-native-$name" $flags -lm
done
"$runtime/test-native-gsettings"
dbus-run-session -- "$runtime/test-native-permissions"
"$runtime/test-native-notifications"
dbus-run-session -- "$runtime/test-native-app-portals"
dbus-run-session -- "$runtime/test-native-defaults"
"$runtime/test-native-shelf" "$source/shell/luma-settings-structure.json"
schemas="$XDG_DATA_HOME/gnome-shell/extensions/tilingshell@ferrarodomenico.com/schemas"
mkdir -p "$schemas"
cat > "$schemas/test.gschema.xml" <<'EOF'
<schemalist>
 <schema id="org.gnome.shell.extensions.tilingshell" path="/org/gnome/shell/extensions/tilingshell/">
  <key name="inner-gaps" type="u"><default>4</default></key>
  <key name="active-screen-edges" type="b"><default>false</default></key>
  <key name="enable-autotiling" type="b"><default>false</default></key>
 </schema>
 <schema id="org.gnome.shell" path="/org/gnome/shell/">
  <key name="enabled-extensions" type="as"><default>[]</default></key>
  <key name="disabled-extensions" type="as"><default>[]</default></key>
  <key name="disable-user-extensions" type="b"><default>false</default></key>
 </schema>
</schemalist>
EOF
glib-compile-schemas "$schemas"
GSETTINGS_SCHEMA_DIR="$schemas" dbus-run-session -- "$runtime/test-native-windows"
python3 "$tests/compile-sound.py" "$source" "$build" "$runtime/test-sound-controls"
sh "$tests/run-sound.sh" "$runtime"
