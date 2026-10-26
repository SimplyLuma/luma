# SPDX-License-Identifier: Apache-2.0
"""Optional runtime components for macOS `luma port` apps (fetched/built once, cached, shared).

An app asks for them in luma-app.toml:

    [port]
    runtime = ["numpy", "pillow", "libraw", "pycairo"]

Same names and versions as tools/luma-port/android/components.py. Cached under
LUMA_PORT_CACHE/components (default ~/Library/Caches/luma-port/components):

  numpy     NumPy 2.4.6, PyPI's cp314 macOS arm64 wheel (Accelerate BLAS)
  pillow    Pillow 12.3.0, PyPI's cp314 macOS arm64 wheel (its own libjpeg, lcms2, webp, ...)
  libraw    LibRaw 0.22.2 built from the release tarball with clang (jpeg + zlib, no OpenMP),
            bundled as Contents/Frameworks/libraw_r.dylib, which ctypes.util.find_library("raw_r")
            finds through the launcher's DYLD_FALLBACK_LIBRARY_PATH
  pycairo   already part of every mac bundle (Homebrew's pycairo + gi._gi_cairo): nothing to do

Wheels are unpacked into the bundle's site-packages (never system-wide); their .dylibs are
moved into Contents/Frameworks and relocated like every other library.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PY_TAG = "314"
WHEELS = {"numpy": "2.4.6", "pillow": "12.3.0"}
WHEEL_PLATFORM = "macosx_14_0_arm64"
LIBRAW = ("0.22.2", "https://www.libraw.org/data/LibRaw-0.22.2.tar.gz")
KNOWN = ("numpy", "pillow", "libraw", "pycairo")


def _log(message: str) -> None:
    print(f"luma port mac: {message}", flush=True)


def _cache(cache: Path) -> Path:
    path = cache / "components"
    path.mkdir(parents=True, exist_ok=True)
    return path


def wheel(cache: Path, name: str, python: Path) -> Path:
    """The unpacked wheel (a site-packages fragment) for `name`."""
    version = WHEELS[name]
    root = _cache(cache)
    unpacked = root / f"{name}-{version}-cp{PY_TAG}-{WHEEL_PLATFORM}"
    if (unpacked / ".complete").exists():
        return unpacked
    dl = root / "wheels"
    dl.mkdir(exist_ok=True)
    found = sorted(dl.glob(f"{name}-{version}-cp{PY_TAG}-cp{PY_TAG}-macosx_*_arm64.whl"))
    if not found:
        _log(f"fetching {name} {version} (PyPI wheel, cp{PY_TAG} macOS arm64)")
        subprocess.run([str(python), "-m", "pip", "download", f"{name}=={version}", "--only-binary=:all:",
                        "--no-deps", "--platform", WHEEL_PLATFORM, "--python-version", "3.14",
                        "--implementation", "cp", "--abi", f"cp{PY_TAG}", "-d", str(dl), "-q"], check=True)
        found = sorted(dl.glob(f"{name}-{version}-cp{PY_TAG}-cp{PY_TAG}-macosx_*_arm64.whl"))
    shutil.rmtree(unpacked, ignore_errors=True)
    with zipfile.ZipFile(found[-1]) as zf:
        zf.extractall(unpacked)
    for info in unpacked.glob("*.dist-info"):
        shutil.rmtree(info)
    (unpacked / ".complete").write_text(found[-1].name + "\n")
    return unpacked


def libraw(cache: Path, brew: Path) -> Path:
    version, url = LIBRAW
    root = _cache(cache)
    out = root / f"libraw-{version}" / "libraw_r.dylib"
    if out.exists():
        return out
    tarball = root / url.rsplit("/", 1)[1]
    if not tarball.exists():
        _log(f"fetching LibRaw {version}")
        with urllib.request.urlopen(url, timeout=300) as resp, open(tarball.with_suffix(".part"), "wb") as fh:
            shutil.copyfileobj(resp, fh)
        tarball.with_suffix(".part").rename(tarball)
    work = root / f"libraw-{version}-build"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    with tarfile.open(tarball) as tf:
        tf.extractall(work, filter="data")
    src = next(work.glob("LibRaw-*"))
    makefile = (src / "Makefile.dist").read_text()
    block = makefile[makefile.index("LIB_OBJECTS"):]
    names = re.findall(r"object/(\w+)\.o", block[:block.index("\n\n")])
    by_name = {p.stem: p for p in (src / "src").rglob("*.cpp")}
    jpeg = brew / "opt" / "jpeg-turbo"
    flags = ["-O2", "-DNDEBUG", "-w", "-fPIC", f"-I{src}", f"-I{jpeg}/include", "-DUSE_JPEG", "-DUSE_JPEG8",
             "-DUSE_ZLIB", "-mmacosx-version-min=11.0"]
    objs = work / "obj"
    objs.mkdir()
    _log(f"building LibRaw {version} ({len(names)} sources)")

    def one(name: str) -> Path:
        obj = objs / f"{name}.o"
        subprocess.run(["clang++", *flags, "-c", str(by_name[name]), "-o", str(obj)], check=True)
        return obj

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 8) as pool:
        built = list(pool.map(one, names))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["clang++", "-dynamiclib", "-o", str(out), *map(str, built), f"-L{jpeg}/lib", "-ljpeg", "-lz",
                    "-install_name", "@rpath/libraw_r.dylib", "-mmacosx-version-min=11.0"], check=True)
    shutil.rmtree(work)
    return out


def stage(names: list[str], contents: Path, bundler, cache: Path, brew: Path, site: Path, python: Path) -> None:
    """Put the components an app asked for into the bundle (before relocation and signing)."""
    for name in names:
        if name not in KNOWN or name == "pycairo":  # pycairo: always in the mac bundle; others: their own provider
            continue
        if name == "libraw":
            bundler.add_library(libraw(cache, brew), "libraw_r.dylib")
            continue
        src = wheel(cache, name, python)
        for item in src.iterdir():
            if item.name.startswith("."):
                continue
            dest = site / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True, symlinks=False,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "*.pyi"))
            else:
                shutil.copy2(item, dest)
        # Extensions: resolve their @loader_path/.dylibs deps against the cached wheel, bundle them.
        for so in [p for p in site.rglob("*.so") if (src / p.relative_to(site)).exists()]:
            bundler.track(so, (src / so.relative_to(site)).resolve())
            bundler.walk(so)
        for dylibs in list(site.glob("*/.dylibs")) + list(site.glob("*.libs")):
            if (src / dylibs.relative_to(site)).exists():
                shutil.rmtree(dylibs)
        _log(f"component {name} {WHEELS[name]} staged")
