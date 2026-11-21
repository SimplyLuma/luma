# SPDX-License-Identifier: Apache-2.0
"""``luma-app-closure``: the ADR-052 application store prototype from a terminal.

  build FILE | --name PACKAGE --userland NAME    build a closure and commit it
  list | info ID | history ID | sharing | verify  what the store holds; whether it is intact
  prepare ID                                      make the checkout it runs from (otherwise made at first start)
  run ID [ARGUMENT ...]                           start it
  shell ID -- COMMAND ...                         run a command in its sandbox
  refresh ID [--check]                            re-resolve; a changed set is a new commit
  rollback ID | remove ID [--keep-data] | gc
  pull REPOSITORY [REF ...]                       copy trees from another store on this device
  graphics mesa USERLAND | host | nvidia | show   device graphics layers

Nothing in the shipped install path calls this.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from . import closure, closure_launcher, device_graphics
from .app_store import Store
from .errors import InstallerError


def _paths(arguments: argparse.Namespace, building: bool = True) -> tuple[Store, Path, Path]:
    store = Store(Path(arguments.store) if arguments.store else None, fsync=not arguments.no_fsync)
    scratch = Path(arguments.scratch or os.environ.get("LUMA_APP_SCRATCH") or Path.home() / ".cache/luma/app-closures")
    work, cache = Path(os.environ.get("LUMA_APP_WORK") or scratch / "work"), scratch / "cache"
    if building:
        # Only commands that build leave scratch behind; it is a cache and may be deleted at any time.
        for folder in (work, cache):
            folder.mkdir(parents=True, exist_ok=True)
    return store, work, cache


def _print(value: object) -> None:
    print(json.dumps(value, indent=1, sort_keys=True))


def _extract_appimage(path: Path, work: Path) -> Path:
    from . import appimage_payload
    destination = Path(tempfile.mkdtemp(prefix="appimage-", dir=work)) / "AppDir"
    kind, offset = appimage_payload.locate(path)
    if kind == appimage_payload.SQUASHFS:
        closure._run(["unsquashfs", "-quiet", "-no-xattrs", "-o", str(offset), "-d", str(destination), str(path)], timeout=1800)
    else:
        appimage_payload.extract_with_runtime(path, destination)
    return destination


def _commit(store: Store, built: closure.Built, started: float) -> dict[str, object]:
    record = built.record
    ref = store.app_ref(str(record["id"]), str(record["architecture"]))
    previous = store.resolve(ref)
    origin = dict(record["origin"])  # type: ignore[arg-type]
    commit = store.commit_tree(built.root, ref, record, subject=f"{record['id']} {origin.get('version', '')}".strip())
    summary = {"id": record["id"], "ref": ref, "commit": commit, "userland": record["userland"],
               "packages": len(record["packages"]), "version": origin.get("version", ""),  # type: ignore[arg-type]
               "added_repositories": [entry["path"] for entry in origin.get("added_repositories") or []],
               "undeclared": record["undeclared"], "seconds": round(time.monotonic() - started, 1)}
    if previous:
        summary["previous"] = previous
        summary["store_delta"] = store.delta(previous, commit)
    return summary


def command_build(arguments: argparse.Namespace) -> int:
    store, work, cache = _paths(arguments)
    started = time.monotonic()
    options = {"language": arguments.language, "stamp": arguments.stamp, "updates": not arguments.no_updates,
               "extra_packages": tuple(arguments.extra), "windowed": not arguments.console}
    payload = None
    if arguments.file:
        path = Path(arguments.file).resolve()
        spec, candidates = closure.spec_for_file(path, arguments.id, arguments.userland,
                                                 foreign_architectures=tuple(arguments.add_architecture), **options)
        store.ensure()
        spec.package_file = str(store.keep_package(path, spec.sha256))
        if spec.kind == "appimage":
            payload = _extract_appimage(path, work)
    else:
        if not arguments.name or not arguments.userland:
            raise InstallerError("Give a package file, or --name with --userland.")
        spec = closure.spec_for_name(arguments.name, arguments.userland, arguments.id,
                                     foreign_architectures=tuple(arguments.add_architecture), **options)
        candidates = [arguments.userland]
    log = Path(arguments.log) if arguments.log else None
    built = closure.build_first(spec, candidates, work, cache, store, log, payload)
    try:
        _print(_commit(store, built, started))
    finally:
        closure.remove_tree(built.root.parent)
        if payload is not None:
            closure.remove_tree(payload.parent)
    return 0


def command_refresh(arguments: argparse.Namespace) -> int:
    store, work, cache = _paths(arguments)
    started = time.monotonic()
    ref = store.app_ref(arguments.id)
    record = store.read_record(ref)
    log = closure.BuildLog(Path(arguments.log) if arguments.log else None)
    probe = Path(tempfile.mkdtemp(prefix=f"refresh-{arguments.id}-", dir=work))
    try:
        store.export(ref, probe / "root")
        resolved = closure.resolve_changes(record, probe / "root", cache, log)
    finally:
        closure.remove_tree(probe)
    result: dict[str, object] = {"id": arguments.id, "changes": resolved["changes"],
                                 "download_bytes": resolved["download_bytes"]}
    if not resolved["changes"] or arguments.check:
        result["commit"] = store.resolve(ref)
        result["changed"] = bool(resolved["changes"])
        _print(result)
        return 0
    spec = closure.spec_from_record(record, store.kept_package(record))
    target = Path(tempfile.mkdtemp(prefix=f"{arguments.id}-", dir=work))
    try:
        built = closure.build(spec, target, cache, store, log.path)
        difference = closure.lock_difference(record["packages"], built.record["packages"])  # type: ignore[arg-type]
        result.update(_commit(store, built, started))
        result["lock_difference"] = {key: len(value) for key, value in difference.items()}
        result["changed"] = True
    finally:
        closure.remove_tree(target)
    _print(result)
    return 0


def command_graphics(arguments: argparse.Namespace) -> int:
    store, work, cache = _paths(arguments, building=arguments.what != "show")
    store.ensure()
    if arguments.what == "show":
        _print({"drivers": device_graphics.gpu_drivers(), "nvidia_module": device_graphics.nvidia_module_version(),
                "layers": {ref: {key: value for key, value in store.read_record(ref).items()
                                 if key in {"route", "mesa", "glibc_version", "version", "source"}}
                           for ref in store.refs("graphics/")}})
        return 0
    if arguments.what == "mesa":
        if not arguments.userland:
            raise InstallerError("Name the userland the Mesa layer is for.")
        ref = device_graphics.build_mesa_layer(arguments.userland, store, work, cache,
                                               Path(arguments.log) if arguments.log else None)
        _print({"ref": ref, "commit": store.resolve(ref), "mesa": store.read_record(ref).get("mesa")})
        return 0
    staging = Path(tempfile.mkdtemp(prefix=f"graphics-{arguments.what}-", dir=work))
    try:
        device = Path(arguments.device)
        if arguments.what == "host":
            record = device_graphics.stage_host_layer(device, staging)
            ref = store.graphics_ref("host")
        else:
            record = device_graphics.stage_nvidia_layer(device, staging)
            ref = store.graphics_ref(f"nvidia-{record['version']}")
        commit = store.commit_tree(staging, ref, record, subject=str(record["id"]))
        _print({"ref": ref, "commit": commit, "libraries": len(record["libraries"]),  # type: ignore[arg-type]
                "glibc_version": record.get("glibc_version")})
    finally:
        closure.remove_tree(staging)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-app-closure", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", help="store folder (default ~/.local/share/luma/apps)")
    parser.add_argument("--scratch", help="build scratch and download cache folder")
    parser.add_argument("--no-fsync", action="store_true", help="do not sync each object to disk when committing")
    parser.add_argument("--log", help="append build output to this file")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("file", nargs="?")
    build.add_argument("--name")
    build.add_argument("--userland", default="")
    build.add_argument("--id", default="")
    build.add_argument("--language", default="")
    build.add_argument("--stamp", default="", help="Debian: build as of this archive snapshot (YYYYMMDDTHHMMSSZ)")
    build.add_argument("--no-updates", action="store_true", help="Fedora: release repository only")
    build.add_argument("--extra", action="append", default=[], help="another package for the same tree")
    build.add_argument("--add-architecture", action="append", default=[], help="foreign architecture, e.g. i386")
    build.add_argument("--console", action="store_true", help="no window: skip the desktop baseline")
    for name in ("info", "history", "rollback", "prepare"):
        commands.add_parser(name).add_argument("id")
    for name in ("list", "sharing", "gc", "verify"):
        commands.add_parser(name)
    run = commands.add_parser("run")
    run.add_argument("id")
    run.add_argument("--graphics", action="append", help="graphics layer ref to use; 'none' for the tree's own Mesa")
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    shell = commands.add_parser("shell")
    shell.add_argument("id")
    shell.add_argument("--graphics", action="append")
    shell.add_argument("arguments", nargs=argparse.REMAINDER)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("id")
    refresh.add_argument("--check", action="store_true")
    pull = commands.add_parser("pull")
    pull.add_argument("repository")
    pull.add_argument("refs", nargs="*")
    remove = commands.add_parser("remove")
    remove.add_argument("id")
    remove.add_argument("--keep-data", action="store_true")
    graphics = commands.add_parser("graphics")
    graphics.add_argument("what", choices=("mesa", "host", "nvidia", "show"))
    graphics.add_argument("userland", nargs="?", default="")
    graphics.add_argument("--device", default="/", help="root to take the device's drivers from")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            return command_build(arguments)
        if arguments.command == "refresh":
            return command_refresh(arguments)
        if arguments.command == "graphics":
            return command_graphics(arguments)
        store, _work, _cache = _paths(arguments, building=False)
        if arguments.command == "list":
            _print([{"ref": ref, "commit": store.resolve(ref)[:12], **{
                key: store.read_record(ref).get(key) for key in ("userland", "kind", "format")}} for ref in store.refs()])
        elif arguments.command == "info":
            record = store.read_record(store.app_ref(arguments.id))
            record["packages"] = len(record["packages"])  # type: ignore[arg-type]
            record.pop("build", None)
            _print(record)
        elif arguments.command == "prepare":
            started = time.monotonic()
            checkout, _record = closure_launcher.deployment(store, arguments.id)
            description = json.loads((checkout / ".luma-deployment.json").read_text())
            _print({"id": arguments.id, "checkout": str(checkout), "seconds": round(time.monotonic() - started, 2),
                    "graphics": [{key: layer.get(key) for key in ("route", "mesa", "version", "glibc_replaced")}
                                 for layer in description.get("graphics") or []]})
        elif arguments.command == "history":
            _print(store.history(store.app_ref(arguments.id)))
        elif arguments.command == "sharing":
            _print({**store.sharing("app/"), "repository_disk_bytes": store.disk_bytes()})
        elif arguments.command == "rollback":
            _print({"id": arguments.id, "commit": store.rollback(store.app_ref(arguments.id))})
        elif arguments.command == "pull":
            _print({"pulled": store.pull(Path(arguments.repository), list(arguments.refs))})
        elif arguments.command == "verify":
            intact = store.verify()
            _print({"intact": intact})
            return 0 if intact else 1
        elif arguments.command == "gc":
            _print(store.collect())
        elif arguments.command == "remove":
            store.remove(arguments.id)
            if not arguments.keep_data:
                closure_launcher.remove_data(arguments.id)
            _print({"id": arguments.id, "removed": True, "data_removed": not arguments.keep_data})
        elif arguments.command in {"run", "shell"}:
            layers = None
            if arguments.graphics:
                layers = [] if arguments.graphics == ["none"] else list(arguments.graphics)
            extra = [value for value in arguments.arguments if value != "--"] if arguments.command == "shell" else list(arguments.arguments)
            if arguments.command == "shell":
                return closure_launcher.launch(store, arguments.id, graphics=layers, command=extra or ["/bin/sh"])
            return closure_launcher.launch(store, arguments.id, extra, graphics=layers)
        return 0
    except InstallerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
