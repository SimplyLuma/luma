# SPDX-License-Identifier: Apache-2.0
"""Stage what an app ships, before any backend runs (shared by every platform).

When the manifest asks for it, the app is copied to ``<out>/app-src/`` and the manifest's
``app_dir`` points there, so every backend ships the staged tree with no change of its own:

``[port] modules = "closure"``
    A tree that holds many apps (prairie-core's ``prairie_apps``: a hundred modules for a
    dozen apps) ships only what the app imports: each of ``packages`` with the import closure
    of ``module`` inside those packages (lazy ``from .x import y`` in a function counts:
    modulefinder reads the bytecode). Modules reached only through importlib:
    ``[port] modules_extra = ["pkg.mod", ...]``.
``[port] include = ["style", ...]``
    Folders under app_dir shipped beside the packages, at ``{app}/<folder>``.
``[port.files]``  ``"data/x" = "../../somewhere/x"``
    Files or folders from anywhere in the checkout (source relative to the manifest file),
    shipped at ``{app}/<key>``. Keys must start with ``data/`` (every backend ships data/).
``[port.variant.<name>]``  (``luma-port ... --variant <name>``)
    ``env`` and ``files`` added to the base ones (base and --env keys win), e.g. a fixture build.

``data/`` and the icon are staged too. Every manifest also gets ``LUMA_APP_ID`` in its env
(the kit puts an app's own folders under it off Luma OS: luma_appkit.platform.user_dir).
"""
from __future__ import annotations

import modulefinder
import shutil
import tomllib
from pathlib import Path

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache")


def _toml_path(app_arg: Path) -> Path:
    return app_arg if app_arg.is_file() else app_arg / "luma-app.toml"


def closure(app_dir: Path, packages: list[str], module: str, extra: list[str]) -> set[str]:
    """Dotted names of the modules under ``packages`` that ``module`` (and ``extra``) import."""
    finder = modulefinder.ModuleFinder(path=[str(app_dir)])
    for name in [module, *extra]:
        finder.import_hook(name, fromlist=["*"])
    roots = tuple(packages)
    return {name for name, mod in finder.modules.items()
            if name.split(".")[0] in roots and getattr(mod, "__file__", None)}


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, ignore=IGNORE, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)


def stage(app_arg: Path, manifest, out: Path, variant: str | None = None):
    """Apply the [port] keys above to ``manifest`` (in place) and return it."""
    toml_path = _toml_path(app_arg)
    # A native (meson) app needs no luma-app.toml (common/native.py detects it): no [port] keys then.
    port = tomllib.loads(toml_path.read_text()).get("port", {}) if toml_path.exists() else {}
    files = dict(port.get("files", {}))
    if variant:
        chosen = port.get("variant", {}).get(variant)
        if chosen is None:
            raise SystemExit(f"luma port: no [port.variant.{variant}] in {toml_path}")
        files.update(chosen.get("files", {}))
        for key, value in chosen.get("env", {}).items():
            manifest.env.setdefault(key, value)  # --env on the command line still wins
    manifest.env.setdefault("LUMA_APP_ID", manifest.app_id)
    manifest.env.setdefault("LUMA_APP_NAME", manifest.name)
    include = list(port.get("include", []))
    prune = port.get("modules") == "closure"
    if not (prune or files or include):
        return manifest

    src = Path(manifest.app_dir)
    dest = out / "app-src"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    module = manifest.module.partition(":")[0]
    total = sum(1 for pkg in manifest.packages for _ in (src / pkg).rglob("*.py"))
    if prune:
        shipped = 0
        for name in sorted(closure(src, manifest.packages, module, list(port.get("modules_extra", [])))):
            rel = Path(*name.split("."))
            for cand in (rel / "__init__.py", rel.with_suffix(".py")):
                if (src / cand).is_file():
                    _copy(src / cand, dest / cand)
                    shipped += 1
                    break
        for pkg in manifest.packages:
            if (src / pkg / "__init__.py").is_file() and not (dest / pkg / "__init__.py").exists():
                _copy(src / pkg / "__init__.py", dest / pkg / "__init__.py")
        print(f"luma port: {module}: {shipped} of {total} modules in {', '.join(manifest.packages)} "
              "staged (import closure)", flush=True)
    else:
        for pkg in manifest.packages:
            _copy(src / pkg, dest / pkg)
    for folder in ["data", *include]:
        if (src / folder).is_dir():
            _copy(src / folder, dest / folder)
    if manifest.icon and (src / manifest.icon).is_file():
        _copy(src / manifest.icon, dest / manifest.icon)
    for key, source in files.items():
        target = Path(key)
        if target.is_absolute() or ".." in target.parts or target.parts[:1] != ("data",):
            raise SystemExit(f"luma port: [port.files] key {key!r} must be a path under data/")
        origin = (toml_path.parent / source).resolve()
        if not origin.exists():
            raise SystemExit(f"luma port: [port.files] {key!r}: {origin} does not exist")
        _copy(origin, dest / target)
    # Backends ship `packages` and data/; an include folder rides along as a package until
    # every backend reads [port] include itself (same place: {app}/<folder>).
    manifest.packages = [*manifest.packages, *(f for f in include if f not in manifest.packages)]
    manifest.app_dir = str(dest)
    print(f"luma port: app staged at {dest}", flush=True)
    return manifest
