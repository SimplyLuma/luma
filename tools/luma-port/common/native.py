# SPDX-License-Identifier: Apache-2.0
"""The native-app half of luma port: Luma apps written in C/C++ and built with meson
(Write, Grid, Stage, ...). Every backend uses it the same way:

    identity = detect(app_dir)                       # manifest fields for an app without luma-app.toml
    exes = build(app_dir, build_dir, prefix, ...)    # meson setup/compile/install of what the app installs

The platform's runtime (GTK, Adw, the LumaUI C kit, json-glib, libxml2, ...) is found through
pkg-config (PKG_CONFIG_PATH / a cross file); the app's own code is not changed. Only the targets
meson would install are compiled: tests, benchmarks and tools stay unbuilt.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

# Options every native Luma app accepts or that meson ignores when absent are not passed blindly:
# only options the app declares (meson_options.txt / meson.options) are set from this table.
PORT_DEFAULTS = {
    "werror": "false",              # built-in: warnings differ between clang/NDK and Fedora's gcc
    # Write/Stage/Grid's LibreOffice engine: built against the vendored LOK SDK headers
    # (LOK_INCLUDE); LibreOffice itself is a shared runtime component found at run time.
    "libreofficekit": "enabled",
    "sanitizers": "none",
    "tests": "false",
    "profile": "default",
}
BUILTIN = {"werror", "buildtype", "b_ndebug", "default_library", "strip"}
# LibreOfficeKit's SDK headers (header-only, MPL-2.0), from the LibreOffice release the ports ship.
LOK_INCLUDE = Path(__file__).resolve().parents[1] / "libreoffice" / "include"


def lok_wanted(app_dir: Path) -> bool:
    return "libreofficekit" in _declared_options(app_dir)


def _declared_options(app_dir: Path) -> set[str]:
    names: set[str] = set()
    for name in ("meson_options.txt", "meson.options"):
        f = app_dir / name
        if f.exists():
            names |= set(re.findall(r"option\(\s*['\"]([\w-]+)['\"]", f.read_text()))
    return names


def detect(app_dir: Path) -> dict:
    """Identity of a meson app that has no luma-app.toml: id, name, version, icon."""
    meson = (app_dir / "meson.build").read_text()
    m = re.search(r"project\(\s*['\"]([^'\"]+)['\"].*?version\s*:\s*['\"]([^'\"]+)['\"]", meson, re.S)
    project, version = (m.group(1), m.group(2)) if m else (app_dir.name, "0.0.0")
    identity = (app_dir / "identity" / "meson.build")
    ident = identity.read_text() if identity.exists() else ""
    app_id = _assign(ident, "luma_app_id")
    name = _assign(ident, "luma_app_name")
    if not app_id:
        for cand in sorted(app_dir.glob("data/*.desktop*")):
            app_id = cand.name.split(".desktop")[0]
            break
    if not app_id:
        raise SystemExit(f"luma port: {app_dir} has no luma-app.toml and no data/<app-id>.desktop(.in)")
    if not name:
        desktop = next(iter(sorted(app_dir.glob(f"data/{app_id}.desktop*"))), None)
        text = desktop.read_text() if desktop else ""
        dm = re.search(r"^Name=(.+)$", text, re.M)
        name = dm.group(1).strip() if dm and "@" not in dm.group(1) else app_id.rsplit(".", 1)[-1]
    icon = None
    svg = app_dir / "data" / f"{app_id}.svg"
    if svg.exists():
        icon = str(svg.relative_to(app_dir))
    return {"app_id": app_id, "name": name, "version": version, "icon": icon, "project": project}


def _assign(text: str, var: str) -> str | None:
    m = re.search(rf"^{var}\s*=\s*'([^']*)'", text, re.M)
    return m.group(1) if m else None


def meson_options(app_dir: Path, extra: list[str] | None = None) -> list[str]:
    """-D options for the port build: the defaults the app declares, then the manifest's own."""
    declared = _declared_options(app_dir)
    opts = [f"-D{k}={v}" for k, v in PORT_DEFAULTS.items() if k in declared or k in BUILTIN]
    return opts + [o if o.startswith("-D") else f"-D{o}" for o in (extra or [])]


def run(cmd: list, env: dict | None = None, log: Path | None = None, cwd: Path | None = None) -> None:
    printable = " ".join(shlex.quote(str(c)) for c in cmd)
    if log is None:
        subprocess.run([str(c) for c in cmd], check=True, env=env, cwd=cwd)
        return
    with open(log, "a") as fh:
        fh.write(f"\n$ {printable}\n")
        fh.flush()
        rc = subprocess.run([str(c) for c in cmd], env=env, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-40:])
        raise SystemExit(f"{tail}\nluma port: `{printable}` failed (log: {log})")


def installed_targets(build: Path) -> list[dict]:
    raw = subprocess.run(["meson", "introspect", "--targets", str(build)], check=True,
                         capture_output=True, text=True).stdout
    return [t for t in json.loads(raw) if t.get("installed")]


PORT_PREFIX = "/luma-port"   # the prefix meson sees; files are installed under DESTDIR and relocated by the backend


def build(app_dir: Path, build_dir: Path, root: Path, *, setup_args: list[str] | None = None,
          options: list[str] | None = None, env: dict | None = None, log: Path | None = None) -> tuple[Path, list[Path]]:
    """Configure (once), compile the installed targets, install them under DESTDIR=root.

    Returns (prefix, executables): prefix is root/luma-port (bin/, lib/, share/), executables the
    installed programs, the app's own first. Installing under DESTDIR keeps gnome.post_install
    (update-mime-database, gtk-update-icon-cache, ...) from running against the build machine."""
    env = dict(env or os.environ)
    if not (build_dir / "build.ninja").exists():
        run(["meson", "setup", build_dir, app_dir, f"--prefix={PORT_PREFIX}", "--libdir=lib",
             "--buildtype=release", *(setup_args or []), *meson_options(app_dir, options)], env=env, log=log)
    # Compile only what is installed (and what it needs), so tests/ and tools never build.
    outputs = sorted({f for t in installed_targets(build_dir) for f in t.get("filename", [])})
    rel = [os.path.relpath(f, build_dir) for f in outputs]
    if rel:
        run(["ninja", "-C", build_dir, *rel], env=env, log=log)
    if root.exists():
        import shutil
        shutil.rmtree(root)
    run(["meson", "install", "-C", build_dir, "--no-rebuild", "--skip-subprojects", f"--destdir={root}"],
        env=env, log=log)
    prefix = root / PORT_PREFIX.lstrip("/")
    bindir = prefix / "bin"
    # bin/ or, with pixiewood's Android cross files, bin/<abi>/
    exes = sorted(p for p in bindir.rglob("*") if p.is_file() and os.access(p, os.X_OK)) if bindir.is_dir() else []
    exes.sort(key=lambda p: (not p.name.startswith("luma-"), p.name))
    return prefix, exes


def _cli(argv: list[str]) -> int:
    """python3 native.py build APP_DIR BUILD_DIR ROOT [--cross-file F ...] [-Dopt=v ...] [--log F]
    Prints {"prefix": ..., "executables": [...]} as JSON (used by the Android backend in its container)."""
    import argparse

    ap = argparse.ArgumentParser(prog="native.py")
    ap.add_argument("step", choices=["build"])
    ap.add_argument("app_dir")
    ap.add_argument("build_dir")
    ap.add_argument("root")
    ap.add_argument("--cross-file", action="append", default=[])
    ap.add_argument("--log", default=None)
    a, rest = ap.parse_known_args(argv)
    setup = [f"--cross-file={f}" for f in a.cross_file]
    prefix, exes = build(Path(a.app_dir), Path(a.build_dir), Path(a.root), setup_args=setup,
                         options=[o for o in rest if o.startswith("-D")], log=Path(a.log) if a.log else None)
    print(json.dumps({"prefix": str(prefix), "executables": [str(e) for e in exes]}))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
