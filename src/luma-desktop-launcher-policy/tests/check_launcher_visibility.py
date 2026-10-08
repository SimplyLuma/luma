#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise actual GIO override resolution and prove each NoDisplay can fail."""
import argparse
import ctypes
import ctypes.util
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile

HIDDEN = {"org.projectluma.Displays.desktop": "org.projectluma.Displays",
          "org.projectluma.Mods.desktop": "luma-mods",
          "org.projectluma.SoftwareUpdate.desktop": "luma-depot --view updates"}
VISIBLE = ("org.projectluma.Terminal.desktop", "org.projectluma.Calculator.desktop")


def probe(overlay):
    gio = ctypes.CDLL(ctypes.util.find_library("gio-2.0"))
    gio.g_desktop_app_info_new.argtypes = [ctypes.c_char_p]
    gio.g_desktop_app_info_new.restype = ctypes.c_void_p
    gio.g_app_info_should_show.argtypes = [ctypes.c_void_p]
    gio.g_app_info_should_show.restype = ctypes.c_int
    gio.g_app_info_get_commandline.argtypes = [ctypes.c_void_p]
    gio.g_app_info_get_commandline.restype = ctypes.c_char_p
    gio.g_desktop_app_info_get_filename.argtypes = [ctypes.c_void_p]
    gio.g_desktop_app_info_get_filename.restype = ctypes.c_char_p
    for desktop, command in HIDDEN.items():
        app = gio.g_desktop_app_info_new(desktop.encode())
        assert app, f"GIO could not resolve {desktop}"
        assert not gio.g_app_info_should_show(app), f"{desktop} is visible"
        filename = gio.g_desktop_app_info_get_filename(app).decode()
        assert Path(filename) == overlay / "applications" / desktop, filename
        assert gio.g_app_info_get_commandline(app).decode() == command, desktop
    for desktop in VISIBLE:
        app = gio.g_desktop_app_info_new(desktop.encode())
        assert app and gio.g_app_info_should_show(app), f"native role hidden: {desktop}"
    print(f"PASS GIO: {len(HIDDEN)} hidden entries preserve provider commands; {len(VISIBLE)} native roles remain visible")


def run(overlay, providers, user):
    env = dict(os.environ, XDG_DATA_HOME=str(user),
               XDG_DATA_DIRS=f"{overlay}:{providers}",
               PATH=f"{providers / 'bin'}:{os.environ.get('PATH', '')}")
    return subprocess.run([sys.executable, __file__, "--probe", str(overlay)],
                          env=env, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.probe:
        probe(args.probe)
        return
    source = Path(__file__).resolve().parents[1] / "applications"
    with tempfile.TemporaryDirectory(prefix="luma-launcher-policy-") as temp:
        root = Path(temp)
        providers = root / "providers"
        (providers / "applications").mkdir(parents=True)
        (providers / "bin").mkdir()
        # GIO requires the Exec target to exist. These harmless fixture providers
        # prove metadata/visibility, not live display or recovery functionality.
        for command in HIDDEN.values():
            binary = providers / "bin" / shlex.split(command)[0]
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
        overlay = root / "overlay"
        (overlay / "applications").mkdir(parents=True)
        for desktop in HIDDEN:
            text = (source / desktop).read_text()
            (providers / "applications" / desktop).write_text(text.replace("NoDisplay=true\n", ""))
            (overlay / "applications" / desktop).write_text(text)
        for desktop in VISIBLE:
            (providers / "applications" / desktop).write_text(
                "[Desktop Entry]\nType=Application\nName=Native role\nExec=true\n")
        result = run(overlay, providers, root / "user")
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout, end="")
        if args.self_test:
            for desktop in HIDDEN:
                path = overlay / "applications" / desktop
                before = path.read_text()
                path.write_text(before.replace("NoDisplay=true", "NoDisplay=false"))
                broken = run(overlay, providers, root / "user")
                assert broken.returncode != 0 and f"{desktop} is visible" in broken.stderr, broken.stderr
                path.write_text(before)
                print(f"PASS negative: visible {desktop} is rejected")


if __name__ == "__main__":
    main()
