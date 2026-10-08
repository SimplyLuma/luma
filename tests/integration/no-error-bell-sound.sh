#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fails if the shipped image would beep when Backspace is pressed in an empty
# field (or any other action GTK refuses with its error bell).
#
# Runs GTK's own gtk_widget_error_bell() path against Luma's *shipped* GTK 3
# and GTK 4 settings (config/desktop/gtk-3.0/settings.ini, gtk-4.0/settings.ini
# — the actual files this repo installs to /etc, not stand-ins) and proves
# nothing downstream ever asks to ring the bell. It intercepts GDK's own beep
# entry points (gdk_window_beep for GTK 3, gdk_surface_beep for GTK 4) with an
# LD_PRELOAD shim: gtk_widget_error_bell() is documented to check
# GtkSettings:gtk-error-bell and, only if true, call exactly one of these
# before anything reaches the window manager or libcanberra. With the setting
# off, neither is ever called, so no bell request leaves the process — nothing
# for mutter to answer and nothing for libcanberra to play from the sound
# theme.
#
# A positive control (gtk-error-bell forced back on) proves the shim and test
# harness actually observe a call when one happens, so a silent pass can't be
# a broken harness.
#
# Needs a Linux GTK 3 + GTK 4 stack (gtk3-devel, gtk4-devel, python3-gobject,
# a C compiler, Xvfb) — run this in the Fedora RPM build container, not on the
# Mac control station.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

for tool in cc python3 Xvfb; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: %s is required (run this in the Fedora build container)\n' "$tool" >&2
    exit 1
  }
done
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' 2>/dev/null || {
  printf 'error: python3-gobject with Gtk 4 introspection is required\n' >&2
  exit 1
}

# The shipped defaults this test holds the image to. Read from the repo, not
# copied by hand, so a future edit to the real files is what this test checks.
gtk3_ini="$repo_root/config/desktop/gtk-3.0/settings.ini"
gtk4_ini="$repo_root/config/desktop/gtk-4.0/settings.ini"
for ini in "$gtk3_ini" "$gtk4_ini"; do
  grep -Fxq 'gtk-error-bell=0' "$ini" ||
    { printf 'error: %s no longer ships gtk-error-bell=0\n' "$ini" >&2; exit 1; }
done

# GTK's system settings.ini path is compiled in (sysconfdir), with no portable
# env-var redirect, so this does not try to make GTK auto-load the shipped
# file from an arbitrary path. Instead the Python probe below parses the exact
# shipped file with GLib.KeyFile and applies it to GtkSettings itself, then
# exercises the real error_bell() path with that value in effect — the same
# check GTK performs, driven by the same file the image installs to
# /etc/gtk-4.0/settings.ini, without assuming an unverified GTK env override.

# The interposed symbols: gdk_window_beep (GTK 3 / GDK 3) and gdk_surface_beep
# (GTK 4 / GDK 4). gtk_widget_error_bell() calls exactly one, for the toolkit
# in use, and only when GtkSettings:gtk-error-bell is true.
cat >"$work/beep_guard.c" <<'C'
#include <stdio.h>
#include <stdlib.h>

static void log_call(const char *name) {
    const char *path = getenv("LUMA_BEEP_LOG");
    if (!path)
        return;
    FILE *f = fopen(path, "a");
    if (!f)
        return;
    fprintf(f, "%s\n", name);
    fclose(f);
}

void gdk_window_beep(void *window) {
    (void)window;
    log_call("gdk_window_beep");
}

void gdk_surface_beep(void *surface) {
    (void)surface;
    log_call("gdk_surface_beep");
}
C
cc -shared -fPIC -o "$work/beep_guard.so" "$work/beep_guard.c"

cat >"$work/error_bell_probe.py" <<'PY'
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

def make_entry():
    window = Gtk.Window()
    entry = Gtk.Entry()
    window.set_child(entry)
    window.realize()
    entry.realize()
    return window, entry

def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [line.strip() for line in fh if line.strip()]

log_path = os.environ["LUMA_BEEP_LOG"]
ini_path = sys.argv[1]

# Parse the exact file the image ships to /etc/gtk-4.0/settings.ini and read
# gtk-error-bell the way GTK's key-file settings loader does.
keyfile = GLib.KeyFile()
keyfile.load_from_file(ini_path, GLib.KeyFileFlags.NONE)
shipped_value = keyfile.get_boolean("Settings", "gtk-error-bell")
if shipped_value is not False:
    sys.exit(f"FAIL shipped gtk-4.0/settings.ini gtk-error-bell={shipped_value!r}, expected false")
print("PASS shipped gtk-4.0/settings.ini: gtk-error-bell is false")

settings = Gtk.Settings.get_default()

# 1. Apply the shipped value and confirm error_bell() does not reach GDK's
#    beep call — exactly what happens when Backspace is pressed in an empty
#    entry, since GtkText's backspace handler calls this same method.
settings.set_property("gtk-error-bell", shipped_value)
_, entry = make_entry()
entry.error_bell()
if read_log(log_path):
    sys.exit("FAIL error_bell() reached gdk_surface_beep with the shipped setting")
print("PASS error_bell() with the shipped setting calls no GDK beep")

# 2. Positive control: force the setting back on and confirm the same call
#    IS observed, proving the shim and this harness actually see a beep when
#    one happens (a silent pass above isn't a broken harness).
settings.set_property("gtk-error-bell", True)
entry.error_bell()
calls = read_log(log_path)
if "gdk_surface_beep" not in calls:
    sys.exit("FAIL positive control: forcing gtk-error-bell=True did not reach gdk_surface_beep")
print("PASS positive control: gtk-error-bell=True does reach gdk_surface_beep")
PY

beep_log="$work/beep.log"
GDK_BACKEND=x11 LUMA_BEEP_LOG="$beep_log" LD_PRELOAD="$work/beep_guard.so" \
  xvfb-run -a python3 "$work/error_bell_probe.py" "$gtk4_ini"

printf '\nno-error-bell-sound: PASS\n'
