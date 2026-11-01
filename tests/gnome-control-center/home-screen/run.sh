#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
# Use a disposable build runtime with the matching production dependencies.
source=$(realpath "$1")
mkdir -p "$2"
output=$(realpath "$2")
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
mkdir -p "$output/schemas"
cp "$repo/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" "$output/schemas/"
glib-compile-schemas --strict "$output/schemas"
flags=$(pkg-config --cflags --libs gtk4 json-glib-1.0 gio-unix-2.0)
gcc -O1 -Wall -Wno-deprecated-declarations -ffunction-sections -fdata-sections \
  -I"$source/shell" "$repo/tests/gnome-control-center/home-screen/test-native-home.c" \
  "$source/shell/cc-luma-live-gsettings.c" "$source/shell/cc-luma-fixture.c" \
  -Wl,--gc-sections -o "$output/test-native-home" $flags -lm
GSETTINGS_SCHEMA_DIR="$output/schemas" GSETTINGS_BACKEND=memory "$output/test-native-home" "$source/shell/luma-settings-structure.json"

mkdir -p "$output/old-schemas"
printf '%s\n' '<schemalist><schema id="org.project_luma.shell-state" path="/org/project-luma/shell-state/"><key name="home-layouts" type="s"><default>"{}"</default></key></schema></schemalist>' > "$output/old-schemas/test.gschema.xml"
glib-compile-schemas --strict "$output/old-schemas"
GSETTINGS_SCHEMA_DIR="$output/old-schemas" GSETTINGS_BACKEND=memory "$output/test-native-home" "$source/shell/luma-settings-structure.json" missing
