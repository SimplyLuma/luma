# SPDX-License-Identifier: Apache-2.0
"""Native helper programs an app runs beside its Python, built once per platform.

A manifest names them:

    [[port.helpers]]
    name = "gmessages"                               # the file name the app looks for
    go = "../../luma-messages-bridges/gmessages"     # a Go main package, relative to the manifest

and points the app at them with the ``{helpers}`` placeholder in ``[port.env]``
(e.g. ``LUMA_MESSAGES_HELPER_DIR = "{helpers}"``). Each backend asks :func:`build` for
its target and places the program the platform's way:

    darwin-arm64      Contents/Helpers/<name>              (CGO_ENABLED=0; signed with the bundle)
    android-<arch>    lib/<abi>/libhelper-<name>.so        (cgo with the NDK's clang, so name lookups
                                                            use bionic's resolver; Go's own resolver
                                                            finds no /etc/resolv.conf on Android)
    windows-x86_64    <root>/helpers/<name>.exe            (CGO_ENABLED=0)

Builds are cached by a digest of the sources (go.mod, go.sum, non-test .go files) and the
target, in ``<cache>/helpers``. Go comes from ``LUMA_PORT_GO`` (a ``go`` binary), the shared
toolchain at ``~/luma-port-20261001/go`` on the build server, or ``go`` on PATH. With none of
those (this Mac has no Go) the build runs on ``LUMA_PORT_HELPER_HOST`` (default luma-build)
over ssh and the program is copied back. Module and build caches live on the server's data
disk (``LUMA_PORT_GO_DATA``); nothing executes from there.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path

# target -> (GOOS, GOARCH, cgo)
TARGETS = {
    "darwin-arm64": ("darwin", "arm64", False),
    "android-x86_64": ("android", "amd64", True),
    "android-aarch64": ("android", "arm64", True),
    "windows-x86_64": ("windows", "amd64", False),
    "linux-x86_64": ("linux", "amd64", False),
}
SERVER_GO = Path.home() / "luma-port-20261001" / "go" / "bin" / "go"
REMOTE_ROOT = "luma-port-20261001"
GO_DATA = os.environ.get("LUMA_PORT_GO_DATA", "/mnt/luma-secondary/luma-port-20261001/go")


def log(message: str) -> None:
    print(f"luma port helpers: {message}", flush=True)


def sources(src: Path) -> list[Path]:
    return sorted(p for p in src.rglob("*") if p.is_file() and (
        p.name in ("go.mod", "go.sum") or (p.suffix == ".go" and not p.name.endswith("_test.go"))))


def digest(src: Path, target: str) -> str:
    h = hashlib.sha256(target.encode())
    for path in sources(src):
        h.update(str(path.relative_to(src)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def find_go() -> str | None:
    forced = os.environ.get("LUMA_PORT_GO")
    if forced:
        return forced
    if SERVER_GO.exists():
        return str(SERVER_GO)
    return shutil.which("go")


def go_env(goos: str, goarch: str, cgo: bool, cc: str | None, data: str) -> dict[str, str]:
    env = {"GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "1" if cgo else "0", "GOTOOLCHAIN": "local",
           "GOTELEMETRY": "off", "GOFLAGS": "-modcacherw",
           "GOMODCACHE": f"{data}/mod", "GOCACHE": f"{data}/cache"}
    if cc:
        env["CC"] = cc
    return env


def build(spec: dict, target: str, cache: Path, *, cc: str | None = None) -> Path:
    """The helper ``spec`` ({"name", "go"}) built for ``target``; cached."""
    if target not in TARGETS:
        raise SystemExit(f"luma port: no helper target {target!r}")
    if not spec.get("go"):
        raise SystemExit(f"luma port: helper {spec.get('name')!r} has no source this tool can build (only go = ...)")
    name, src = spec["name"], Path(spec["go"])
    goos, goarch, cgo = TARGETS[target]
    if cgo and not cc:
        raise SystemExit(f"luma port: helper {name} for {target} needs a C compiler (cc=)")
    exe = ".exe" if goos == "windows" else ""
    out = cache / "helpers" / f"{name}-{target}-{digest(src, target)}{exe}"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    flags = ["build", "-trimpath", "-ldflags=-s -w", "-o"]
    go = find_go()
    if go:
        data = GO_DATA if Path(GO_DATA).is_dir() else str(cache / "go")
        log(f"{name} for {target} (local {go})")
        subprocess.run([go, *flags, str(out) + ".tmp", "."], cwd=src, check=True,
                       env=dict(os.environ, **go_env(goos, goarch, cgo, cc, data)))
    else:
        if cgo:
            raise SystemExit(f"luma port: helper {name} for {target} needs cgo; build it where the NDK is")
        host = os.environ.get("LUMA_PORT_HELPER_HOST", "luma-build")
        remote = f"{REMOTE_ROOT}/helpers/src/{name}"
        log(f"{name} for {target} (on {host}: no Go here)")
        subprocess.run(["ssh", host, f"mkdir -p {shlex.quote(remote)}"], check=True)
        subprocess.run(["rsync", "-a", "--delete", "--exclude", ".git", f"{src}/", f"{host}:{remote}/"], check=True)
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in go_env(goos, goarch, cgo, None, GO_DATA).items())
        built = f"{REMOTE_ROOT}/helpers/out/{out.name}"
        cmd = (f"mkdir -p {REMOTE_ROOT}/helpers/out && cd {shlex.quote(remote)} && "
               f"env {env} ~/{REMOTE_ROOT}/go/bin/go build -trimpath -ldflags='-s -w' -o ~/{built} .")
        subprocess.run(["ssh", host, cmd], check=True)
        subprocess.run(["scp", "-q", f"{host}:{built}", str(out) + ".tmp"], check=True)
        subprocess.run(["ssh", host, f"rm -f ~/{built}"], check=False)
    os.replace(str(out) + ".tmp", out)
    out.chmod(0o755)
    for old in out.parent.glob(f"{name}-{target}-*"):
        if old != out:
            old.unlink()
    log(f"{out.name} ({out.stat().st_size / 1e6:.1f} MB)")
    return out
