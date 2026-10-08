#!/usr/bin/env python3
"""Content-addressed, complete Studio reference bundles for conform variants.

Only spec-meta.json and the exact state JSON/PNG pairs are cached. The image ID
pins the browser, fonts, Node and Playwright runtime in the capture container.
The caller must hold the design shared lock while preparing and capturing.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

SCHEMA = 1
STATE = re.compile(r"^[A-Za-z0-9_-]+$")
PNG = b"\x89PNG\r\n\x1a\n"


def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def design_digest(root):
    """Hash every served path and byte; reject directory links and escapes."""
    root = Path(root).resolve(strict=True)
    h = hashlib.sha256()
    count = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for directory in dirs:
            if (Path(parent) / directory).is_symlink():
                raise ValueError("linked Studio directory: " + directory)
        for name in files:
            path = Path(parent) / name
            target = path.resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError("Studio asset escapes the served tree: " + str(path))
            relative = path.relative_to(root).as_posix()
            h.update(canonical([relative, digest_file(target)]))
            count += 1
    if not count:
        raise ValueError("Studio served tree is empty")
    return {"sha256": h.hexdigest(), "files": count}


def expected_states(scenario, theme, phone):
    states = scenario["states"]
    names = [s["name"] for s in states if (not phone or s["name"] in scenario.get("phone_states", []))
             and (not s.get("themes") or theme in s["themes"])]
    if not names or len(names) != len(set(names)) or any(not STATE.fullmatch(name) for name in names):
        raise ValueError("reference variant has no valid, unique states")
    return names


def prepare(design, scenario_path, capture, loader, image_id, theme, phone):
    scenario = json.loads(Path(scenario_path).read_text())
    # The native half of a scenario may change without altering a Studio capture.
    reference_scenario = {key: value for key, value in scenario.items() if key != "gtk"}
    states = expected_states(scenario, theme, phone)
    provenance = {
        "schema": SCHEMA,
        "design": design_digest(design),
        "scenario": hashlib.sha256(canonical(reference_scenario)).hexdigest(),
        "capture_tool": {"spec_capture.js": digest_file(capture), "lib/playwright.js": digest_file(loader)},
        "container_image_id": image_id,
        "capture_base_url": os.environ.get("LUMAUI_CONFORM_BASE", "http://127.0.0.1:8787/").rstrip("/") + "/",
        "playwright_override": os.environ.get("LUMAUI_CONFORM_PLAYWRIGHT", ""),
        "theme": theme,
        "phone": phone,
        "states": states,
    }
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ValueError("a full pinned container image ID is required")
    return {"key": hashlib.sha256(canonical(provenance)).hexdigest(), "provenance": provenance}


def filenames(identity):
    return ["spec-meta.json"] + [f"spec-{name}.{suffix}" for name in identity["provenance"]["states"] for suffix in ("json", "png")]


def validate_files(directory, identity, checksums=None):
    directory = Path(directory)
    expected = filenames(identity)
    for name in expected:
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            return None
        if name.endswith(".png"):
            with path.open("rb") as stream:
                if stream.read(8) != PNG:
                    return None
        if name.endswith(".json"):
            try:
                document = json.loads(path.read_text())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
            if not isinstance(document, dict):
                return None
            if name != "spec-meta.json" and document.get("state") != name[5:-5]:
                return None
    try:
        meta = json.loads((directory / "spec-meta.json").read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("states"), list) or any(not isinstance(s, dict) for s in meta["states"]):
        return None
    p = identity["provenance"]
    if [s.get("name") for s in meta.get("states", [])] != p["states"] or meta.get("theme") != p["theme"] or meta.get("phone") != p["phone"]:
        return None
    actual = {name: digest_file(directory / name) for name in expected}
    if checksums is not None and actual != checksums:
        return None
    return actual


def cache_entry(cache, identity):
    return Path(cache) / identity["key"]


def valid_entry(entry, identity):
    try:
        manifest = json.loads((entry / "manifest.json").read_text())
        if manifest.get("schema") != SCHEMA or manifest.get("identity") != identity or not isinstance(manifest.get("checksums"), dict):
            return False
        return validate_files(entry, identity, manifest.get("checksums")) is not None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return False


def restore(cache, identity, output):
    entry = cache_entry(cache, identity)
    # The container sees the cache read-only. Publishers serialize on the host
    # and expose complete entries only by rename, so a concurrent replacement
    # can at worst cause a miss and a fresh Studio capture.
    if not valid_entry(entry, identity):
        return False
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name in filenames(identity):
        shutil.copyfile(entry / name, output / name)
    return True


def publish(cache, identity, output):
    checksums = validate_files(output, identity)
    if checksums is None:
        raise ValueError("capture is incomplete or has mismatched state metadata")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    entry = cache_entry(cache, identity)
    with open(cache / (identity["key"] + ".lock"), "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if valid_entry(entry, identity):
            return
        stage = Path(tempfile.mkdtemp(prefix=".reference-", dir=cache))
        try:
            for name in filenames(identity):
                shutil.copyfile(Path(output) / name, stage / name)
            if validate_files(stage, identity, checksums) is None:
                raise ValueError("capture changed while publishing")
            manifest = {"schema": SCHEMA, "identity": identity, "checksums": checksums}
            (stage / "manifest.json").write_bytes(canonical(manifest))
            # Capture containers mount this directory read-only as uid 1000.
            os.chmod(stage, 0o755)
            for name in [*filenames(identity), "manifest.json"]:
                os.chmod(stage / name, 0o644)
                with open(stage / name, "rb") as stream:
                    os.fsync(stream.fileno())
            directory_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            if entry.exists():
                shutil.rmtree(entry)
            os.replace(stage, entry)
            directory_fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    key = sub.add_parser("prepare")
    for flag in ("design", "scenario", "capture", "loader", "image-id", "theme", "output"):
        key.add_argument("--" + flag, required=True)
    key.add_argument("--phone", action="store_true")
    for action in ("restore", "publish"):
        p = sub.add_parser(action)
        p.add_argument("--cache", required=True)
        p.add_argument("--identity", required=True)
        p.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            identity = prepare(args.design, args.scenario, args.capture, args.loader, args.image_id, args.theme, args.phone)
            Path(args.output).write_bytes(canonical(identity))
        else:
            identity = json.loads(Path(args.identity).read_text())
            if args.action == "restore":
                if not restore(args.cache, identity, args.out):
                    return 1
                print("reference cache hit: " + identity["key"][:16], file=sys.stderr)
            else:
                publish(args.cache, identity, args.out)
                print("reference cache published: " + identity["key"][:16], file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("reference cache: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
