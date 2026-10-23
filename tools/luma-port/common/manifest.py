# SPDX-License-Identifier: Apache-2.0
"""Reads a Luma app's luma-app.toml into the manifest every port backend consumes."""
from __future__ import annotations

import json
import re
import tomllib
from release import updates_config
from dataclasses import asdict, dataclass, field
from pathlib import Path

KIT_REL = Path("src/luma-platform/appkit/luma_appkit")


@dataclass
class Manifest:
    app_id: str
    name: str
    version: str
    app_dir: str
    packages: list[str]          # top-level Python packages to ship, relative to app_dir
    module: str                  # python -m <module> runs the app
    icon: str | None
    kit_dir: str                 # luma_appkit source
    repo_root: str               # ProjectLuma checkout (C kit sources)
    env: dict[str, str] = field(default_factory=dict)
    helpers: list[dict] = field(default_factory=list)  # [[port.helpers]]: {name, go} (see helpers.py)
    include: list[str] = field(default_factory=list)   # [port] include: more folders of app_dir to ship
    runtime: list[str] = field(default_factory=list)   # [port] runtime: optional shared components
                                                        # (numpy, pillow, libraw, pycairo; <platform>/components.py)
    kind: str = "python"         # "python": python -m <module>; "native": a meson C/C++ app (common/native.py)
    meson_options: list[str] = field(default_factory=list)   # native: extra -D options ([port] meson_options)
    executable: str | None = None                           # native: installed program (default: found)
    python: bool = False         # native: ship CPython (python3 on PATH) for the app's helper scripts
    version_code: int | None = None
    updates: dict = field(default_factory=dict)
    android_package: str | None = None   # Android package name when it isn't the app id lowercased

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


def _entry_module(app_dir: Path, entrypoint: str, packages: list[str]) -> str:
    """The module that runs the app: <pkg>.__main__ if present, else what the launcher imports."""
    for pkg in packages:
        if (app_dir / pkg / "__main__.py").exists():
            return pkg
    text = (app_dir / entrypoint).read_text() if entrypoint else ""
    m = re.search(r"from\s+([\w.]+)\s+import\s+main", text)
    if m:
        return m.group(1)
    raise SystemExit(f"luma port: cannot find the app's entry module (entrypoint={entrypoint!r})")


def _env_map(port: dict, env: list[str] | None) -> dict[str, str]:
    env_map = dict(port.get("env", {}))
    for item in env or []:
        k, _, v = item.partition("=")
        env_map[k] = v
    return env_map


def _is_native(app_dir: Path, port: dict) -> bool:
    """A C/C++ app built with meson: [port] kind = "native", or a meson.build and no Python package."""
    if port.get("kind"):
        return port["kind"] == "native"
    return ((app_dir / "meson.build").exists() and not port.get("packages")
            and not any(app_dir.glob("*/__init__.py")))


def _native_manifest(app_dir: Path, data: dict, port: dict, env: list[str] | None) -> Manifest:
    """Native apps need no luma-app.toml: identity comes from identity/meson.build, data/<id>.desktop
    and meson's project() (common/native.py); luma-app.toml, when present, wins."""
    import native

    ident = native.detect(app_dir)
    app = data.get("application", {})
    repo_root = Path(__file__).resolve().parents[3]
    return Manifest(
        app_id=app.get("id", ident["app_id"]), name=app.get("name", ident["name"]),
        version=str(app.get("version", ident["version"])), app_dir=str(app_dir), packages=[], module="",
        icon=port.get("icon") or data.get("distribution", {}).get("icon") or ident["icon"],
        kit_dir=str(Path(port.get("kit_dir", repo_root / KIT_REL))), repo_root=str(repo_root),
        env=_env_map(port, env), kind="native", meson_options=list(port.get("meson_options", [])),
        executable=port.get("executable"), python=bool(port.get("python", False)),
        version_code=port.get("version_code"),
        updates=updates_config(port.get("updates", {})),
        android_package=port.get("android_package"),
    )


def load_manifest(app_dir: Path, env: list[str] | None = None) -> Manifest:
    """APP may be a directory holding luma-app.toml, or a manifest file itself.

    A manifest file lets one source tree that ships several apps (prairie-core:
    Notes, Messages, ...) carry one manifest per app, e.g.
    src/prairie-core/port/notes.luma-app.toml. Its [port] app_dir (relative to
    the file, default: the file's directory) is the tree whose packages ship."""
    if app_dir.is_file():
        toml_path = app_dir
        app_dir = toml_path.parent
    else:
        toml_path = app_dir / "luma-app.toml"
    data = tomllib.loads(toml_path.read_text()) if toml_path.exists() else {}
    port = data.get("port", {})
    if port.get("app_dir"):
        app_dir = (toml_path.parent / port["app_dir"]).resolve()
    if _is_native(app_dir, port):
        return _native_manifest(app_dir, data, port, env)
    if not data:
        raise SystemExit(f"luma port: {app_dir} has no luma-app.toml and no meson.build")
    app = data["application"]
    packages = port.get("packages")
    if not packages:
        pyproject = app_dir / "pyproject.toml"
        if pyproject.exists():
            packages = tomllib.loads(pyproject.read_text()).get("tool", {}).get("setuptools", {}).get("packages")
    if not packages:
        packages = [p.parent.name for p in app_dir.glob("*/__init__.py")]
    packages = [p for p in packages if "." not in p]
    icon = port.get("icon")
    if not icon:
        for cand in sorted(app_dir.glob(f"data/**/{app['id']}*.svg")) + sorted(app_dir.glob("data/**/*.svg")):
            icon = str(cand.relative_to(app_dir))
            break
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    kit_dir = Path(port.get("kit_dir", repo_root / KIT_REL))
    env_map = dict(port.get("env", {}))
    for item in env or []:
        k, _, v = item.partition("=")
        env_map[k] = v
    helpers = []
    for item in port.get("helpers", []):
        spec = {"name": item["name"]}
        if item.get("go"):
            spec["go"] = str((toml_path.parent / item["go"]).resolve())
        helpers.append(spec)
    return Manifest(
        app_id=app["id"], name=app["name"], version=str(app.get("version", "0.0.0")),
        app_dir=str(app_dir), packages=packages,
        module=port.get("module") or _entry_module(app_dir, app.get("entrypoint", ""), packages),
        icon=icon, kit_dir=str(kit_dir), repo_root=str(repo_root), env=env_map, helpers=helpers,
        include=[str(p) for p in port.get("include", []) if ".." not in Path(p).parts],
        runtime=[str(name) for name in port.get("runtime", [])],
        version_code=port.get("version_code"),
        updates=updates_config(port.get("updates", {})),
        android_package=port.get("android_package"),
    )
