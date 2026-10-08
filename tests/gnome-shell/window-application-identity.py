#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the production tracker functions against real GDesktopAppInfo.

Window/app-system inputs are explicit metadata fixtures. This does not replace
native compositor, favorite persistence or drag runtime acceptance.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).read_text()

def function(name):
    pattern = re.compile(r"static (?:gboolean|ShellApp \*)\s*\n" + name + r"\s*\(")
    match = pattern.search(source)
    if not match:
        raise RuntimeError(f"missing production function {name}")
    begin = source.index("{", match.start())
    depth = 1
    end = begin + 1
    while depth:
        if source[end] == "{": depth += 1
        if source[end] == "}": depth -= 1
        end += 1
    return source[match.start():end]

prefix = r'''
#include <gio/gdesktopappinfo.h>
#include <glib.h>
#include <string.h>
typedef GDesktopAppInfo ShellApp;
typedef struct { const char *gtk_id; const char *sandbox_id; } MetaWindow;
typedef int ShellAppSystem;
static ShellApp *exact_app, *class_app;
static ShellAppSystem app_system;
static const char *meta_window_get_gtk_application_id (MetaWindow *window) { return window->gtk_id; }
static const char *meta_window_get_sandboxed_app_id (MetaWindow *window) { return window->sandbox_id; }
static ShellAppSystem *shell_app_system_get_default (void) { return &app_system; }
static const char *shell_app_get_id (ShellApp *app) { return g_app_info_get_id (G_APP_INFO (app)); }
static GDesktopAppInfo *shell_app_get_app_info (ShellApp *app) { return app; }
static ShellApp *shell_app_system_lookup_app (ShellAppSystem *system, const char *id) {
  return exact_app && g_strcmp0(shell_app_get_id(exact_app), id) == 0 ? exact_app : NULL;
}
static ShellApp *shell_app_system_lookup_startup_wmclass (ShellAppSystem *system, const char *id) {
  return class_app;
}
'''
main = r'''
static ShellApp *desktop (const char *dir, const char *id, const char *wmclass,
                          gboolean hidden, const char *only_show) {
  g_autofree char *path = g_build_filename(dir, id, NULL);
  g_autofree char *text = g_strdup_printf(
    "[Desktop Entry]\nType=Application\nName=Identity fixture\nExec=/usr/bin/true\nNoDisplay=%s\nStartupWMClass=%s\n%s",
    hidden ? "true" : "false", wmclass, only_show ? only_show : "");
  g_assert_true(g_file_set_contents(path, text, -1, NULL));
  ShellApp *info = g_desktop_app_info_new_from_filename(path);
  g_assert_nonnull(info);
  return info;
}
static void expect (MetaWindow *window, ShellApp *wanted, const char *label) {
  ShellApp *result = get_app_from_gapplication_id(window);
  g_assert_true(result == wanted);
  g_clear_object(&result);
  g_print("PASS compiled tracker/GIO: %s\n", label);
}
int main (int argc, char **argv) {
  g_assert_cmpint(argc, ==, 2);
  ShellApp *hidden = desktop(argv[1], "org.example.Integration.desktop", "org.example.Integration", TRUE, NULL);
  ShellApp *visible = desktop(argv[1], "example-visible.desktop", "org.example.Integration", FALSE, NULL);
  ShellApp *direct = desktop(argv[1], "org.example.Integration.desktop", "different", FALSE, NULL);
  ShellApp *foreign = desktop(argv[1], "example-hidden.desktop", "org.example.Integration", TRUE, NULL);
  ShellApp *wrong = desktop(argv[1], "example-wrong.desktop", "wrong.Class", FALSE, NULL);
  ShellApp *restricted = desktop(argv[1], "example-restricted.desktop", "org.example.Integration", FALSE, "OnlyShowIn=KDE;\n");
  ShellApp *sandbox = desktop(argv[1], "org.example.Sandbox.Launcher.desktop", "org.example.Integration", FALSE, NULL);
  MetaWindow window = {"org.example.Integration", NULL};
  exact_app = hidden; class_app = visible;
  expect(&window, visible, "hidden integration resolves installed visible launcher");
  exact_app = NULL;
  expect(&window, visible, "missing exact GTK launcher resolves explicit class owner");
  exact_app = direct;
  expect(&window, direct, "existing visible exact identity wins over class owner");
  exact_app = hidden; class_app = foreign;
  expect(&window, hidden, "hidden canonical candidate is never promoted");
  class_app = restricted;
  expect(&window, hidden, "OnlyShowIn visibility boundary retained");
  class_app = wrong;
  expect(&window, hidden, "undeclared GTK class cannot acquire launcher identity");
  exact_app = NULL; class_app = NULL;
  expect(&window, NULL, "missing identity remains absent");
  exact_app = hidden; class_app = visible; window.sandbox_id = "org.example.Sandbox";
  expect(&window, hidden, "cross-sandbox class mapping cannot acquire host launcher");
  class_app = sandbox;
  expect(&window, sandbox, "same-sandbox explicit launcher prefix accepted");
  window.gtk_id = NULL;
  expect(&window, NULL, "absent GTK application ID is unchanged");
  g_object_unref(hidden); g_object_unref(visible); g_object_unref(direct);
  g_object_unref(foreign); g_object_unref(wrong); g_object_unref(restricted); g_object_unref(sandbox);
  return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="luma-tracker-gio-") as temporary:
    directory = Path(temporary)
    code = prefix + "\n".join(function(name) for name in (
        "check_app_id_prefix", "get_app_from_id", "get_app_from_gapplication_id")) + main
    (directory / "tracker.c").write_text(code)
    flags = shlex.split(subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "gio-unix-2.0"], text=True))
    subprocess.run(["gcc", "-std=gnu11", "-Werror", str(directory / "tracker.c"),
                    "-o", str(directory / "tracker"), *flags], check=True, timeout=30)
    environment = dict(os.environ, XDG_CURRENT_DESKTOP="GNOME")
    subprocess.run([str(directory / "tracker"), str(directory)],
                   check=True, timeout=15, env=environment)
