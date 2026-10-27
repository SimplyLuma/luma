# SPDX-License-Identifier: Apache-2.0
"""Windows start-up for a `luma port` app: `python -m luma_port_boot [args]`.

The launcher (<App>.exe or <App>.cmd) sets PATH, PYTHONHOME and PYTHONPATH. This
module does the rest before anything imports GTK, then runs the app the way it runs
on Luma (`python -m <module>`):

- GI_TYPELIB_PATH, XDG_DATA_DIRS, GSETTINGS_SCHEMA_DIR inside the folder;
- the manifest's env (luma-port-app.json, e.g. LUMA_CHARLIE_FIXTURE=v71);
- a gdk-pixbuf loaders.cache for this folder, written once to %LOCALAPPDATA%;
- LUMA_FONT_DIRS at share/fonts/luma (Figtree, IBM Plex Mono as Luma OS ships them), then hands over to
  the kit's shared boot path, luma_appkit.platform_boot (private GDI fonts, a fresh Pango font map, Luma
  OS's GTK settings), which runs `python -m <module>`;
- under pythonw (no console), stdout/stderr and any crash go to last-run.log there.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent


def _state_dir(app_id: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "LumaPort" / app_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pixbuf_cache(state: Path) -> None:
    """gdk-pixbuf needs a loaders.cache with absolute paths; write one for wherever the folder is now."""
    cache = state / "loaders.cache"
    stamp = state / "loaders.root"
    if not (cache.exists() and stamp.exists() and stamp.read_text(encoding="utf-8") == str(ROOT)):
        loaders = ROOT / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders"
        tool = ROOT / "bin" / "gdk-pixbuf-query-loaders.exe"
        try:
            dlls = sorted(str(p) for p in loaders.glob("*.dll"))
            out = subprocess.run([str(tool), *dlls], capture_output=True, check=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            cache.write_bytes(out)
            stamp.write_text(str(ROOT), encoding="utf-8")
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"luma port: no pixbuf loader cache ({error})", file=sys.stderr)
            return
    os.environ["GDK_PIXBUF_MODULE_FILE"] = str(cache)


def _fonts_and_diagnostics() -> None:
    """Fonts go through the kit's one boot path (luma_appkit.platform_boot, shared with mac/android):
    this only points it at the bundled faces and logs what Pango can see, for remote debugging."""
    os.environ["LUMA_FONT_DIRS"] = os.pathsep.join(
        [str(ROOT / "share" / "fonts" / "luma"), *filter(None, [os.environ.get("LUMA_FONT_DIRS")])])
    from luma_appkit import platform_boot

    registered = platform_boot.register_fonts()   # AddFontResourceExW(FR_PRIVATE) on Windows; idempotent
    print(f"luma port: {len(registered)} private faces registered", flush=True)
    try:
        from gi.repository import PangoCairo

        have = {f.get_name() for f in PangoCairo.FontMap.get_default().list_families()}
        print("luma port: Luma faces Pango sees: "
              + (", ".join(sorted(n for n in have if n.startswith(("Figtree", "IBM Plex", "Newsreader")))) or "none"),
              flush=True)
    except (ImportError, ValueError) as error:
        print(f"luma port: pango check skipped ({error})", flush=True)


def main() -> int:
    meta = json.loads((APP / "luma-port-app.json").read_text(encoding="utf-8"))
    state = _state_dir(meta["app_id"])
    if sys.stdout is None or sys.stderr is None or os.path.basename(sys.executable).lower().startswith("pythonw"):
        log = open(state / "last-run.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    env = os.environ
    share = ROOT / "share"
    env["GI_TYPELIB_PATH"] = str(ROOT / "lib" / "girepository-1.0")
    env["XDG_DATA_DIRS"] = os.pathsep.join([str(share), *filter(None, [env.get("XDG_DATA_DIRS")])])
    env["GSETTINGS_SCHEMA_DIR"] = str(share / "glib-2.0" / "schemas")
    env.setdefault("LUMA_PORT", "windows")
    env.setdefault("LUMA_APPKIT_ICON_PATH", str(share / "icons" / "Prairie" / "symbolic" / "actions"))
    for key, value in meta.get("env", {}).items():
        # {app}: the folder with the app's packages and data/; {root}: the whole runtime folder.
        env.setdefault(key, value.replace("{app}", str(APP)).replace("{root}", str(ROOT)))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(ROOT / "bin"))
    _pixbuf_cache(state)
    try:
        _fonts_and_diagnostics()
    except Exception:  # noqa: BLE001 - a font problem must not stop the app
        traceback.print_exc()

    print(f"luma port: {meta['name']} {meta['version']} from {ROOT}; python -m {meta['module']}", flush=True)
    from luma_appkit import platform_boot
    try:
        platform_boot.main([meta["module"], *sys.argv[1:]])   # tune_gtk, then python -m <module>
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 1)
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
