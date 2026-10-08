#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the built Settings local-options gate and actual launcher visibility.

Run with the installed preview's LD_LIBRARY_PATH, GI_TYPELIB_PATH and
XDG_DATA_DIRS. No graphical display or fixture application is required.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("desktop", type=Path)
    args = parser.parse_args()
    if not args.binary.is_file() or not args.desktop.is_file():
        parser.error("the built binary and installed main desktop entry must exist")
    failures = []
    checks = 0
    desktops = (None, "", "Phosh", "Luma", "GNOME", "Luma:GNOME", "KDE")
    with tempfile.TemporaryDirectory(prefix="settings-session-identity-") as tmp:
        base = os.environ.copy()
        for key in ("LUMA_SETTINGS_FIXTURE", "LUMA_SETTINGS_PAGE", "LUMA_SETTINGS_LEGACY",
                    "DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP"):
            base.pop(key, None)
        base["PATH"] = str(args.binary.resolve().parent) + os.pathsep + base.get("PATH", "")
        base.update(HOME=tmp, XDG_CONFIG_HOME=tmp + "/config",
                    XDG_DATA_HOME=tmp + "/data", XDG_CACHE_HOME=tmp + "/cache")
        for legacy in (False, True):
            for desktop in desktops:
                env = base.copy()
                if legacy:
                    env["LUMA_SETTINGS_LEGACY"] = "1"
                if desktop is not None:
                    env["XDG_CURRENT_DESKTOP"] = desktop
                expected = not legacy or desktop in ("GNOME", "Luma:GNOME")
                result = subprocess.run([str(args.binary.resolve()), "--list"], env=env,
                                        text=True, capture_output=True, timeout=30)
                checks += 1
                label = f"{'legacy' if legacy else 'native'} desktop={desktop!r}"
                if expected:
                    # A zero exit alone could mean no panels were enumerated.
                    ok = result.returncode == 0 and "wifi" in result.stdout.lower()
                else:
                    ok = result.returncode != 0 and "only supported" in result.stderr
                print(f"{'PASS' if ok else 'FAIL'} {label}: exit={result.returncode}")
                if not ok:
                    failures.append({"case": label, "stdout": result.stdout,
                                     "stderr": result.stderr, "exit": result.returncode})
        code = ("import gi,sys; gi.require_version('GioUnix','2.0'); "
                "from gi.repository import GioUnix; "
                "app=GioUnix.DesktopAppInfo.new_from_filename(sys.argv[1]); "
                "assert app is not None; print(int(app.should_show()))")
        for desktop in (None, "Phosh", "Luma", "GNOME"):
            env = base.copy()
            if desktop is not None:
                env["XDG_CURRENT_DESKTOP"] = desktop
            result = subprocess.run([sys.executable, "-c", code, str(args.desktop.resolve())],
                                    env=env, text=True, capture_output=True, timeout=30)
            checks += 1
            ok = result.returncode == 0 and result.stdout.strip() == "1"
            label = f"main launcher desktop={desktop!r}"
            print(f"{'PASS' if ok else 'FAIL'} {label}")
            if not ok:
                failures.append({"case": label, "stdout": result.stdout,
                                 "stderr": result.stderr, "exit": result.returncode})
    if checks != 18:
        raise RuntimeError(f"expected 18 checks, ran {checks}")
    if failures:
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    print(f"Settings session identity: {checks} checks PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
