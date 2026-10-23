#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the Luma Imager catalog (catalog.json, schema 1).

Sources, all in this repository:
  imager/catalog.toml           min Imager version, desktop architectures, channels, guide
  imager/mods/<id>.toml         add-ons
  devices/*/*/device.toml       phones with an [imager] table; never_write is
                                [install].preserve, the release is [[releases]]
  imager/phones/<vendor>-<codename>.toml
                                overlays for devices whose directory lives on
                                another branch; merged over device.toml if both exist

Release file sizes come from HTTPS HEAD requests, falling back to the
*_size_bytes the source states; a size that disagrees with the source is an
error. The aarch64 architecture is "available" only while the nightly index
lists an aarch64 image. The output is checked against imager/catalog.schema.json
(with jsonschema when it is installed, else a built-in subset validator) and
is byte-for-byte reproducible apart from generated_utc.

Usage:  tools/build-imager-catalog.py [--root DIR] [--output FILE] [--offline]
                                       [--generated-utc YYYY-MM-DDTHH:MM:SSZ] [--check]
        --check builds offline, validates and writes nothing (CI).
Exit:   0 valid, 1 invalid, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

SCHEMA = Path("imager/catalog.schema.json")
MAX_BYTES = 1024 * 1024
USER_AGENT = "luma-imager-catalog/1"
STATUS_ORDER = {"available": 0, "coming_soon": 1, "unavailable": 2}
OVERLAY_KEYS = {"vendor", "codename", "name", "tier", "fastboot_product", "never_write", "imager"}
IMAGER_KEYS = {"status", "method", "summary", "picture", "needs", "guide"}
MOD_KEYS = {"name", "summary", "arch", "status", "kind", "note", "suggest_when"}
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class CatalogError(Exception):
    pass


# -- network ------------------------------------------------------------------

def https_head_size(url: str) -> int | None:
    """Content-Length of url after redirects, or None when it cannot be read."""
    if not url.startswith("https://"):
        return None
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length and length.isdigit() else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def https_json(url: str) -> dict | None:
    if not url.startswith("https://"):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(8 * 1024 * 1024))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def offline(_url: str) -> None:
    return None


# -- sources ------------------------------------------------------------------

def load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CatalogError(f"{path}: cannot parse: {exc}") from None


def index_lists_arch(index: dict, arch: str) -> bool:
    if arch in (index.get("latest_by_arch") or {}):
        return True
    return any(isinstance(e, dict) and e.get("arch") == arch for e in index.get("entries") or [])


def build_desktop(source: dict, get_json) -> dict:
    desktop = source.get("desktop", {})
    channels = []
    for channel in desktop.get("channels", []):
        entry = {"id": channel["id"], "name": channel["name"]}
        if "default" in channel:
            entry["default"] = channel["default"]
        entry["status"] = channel["status"]
        if "index_url" in channel:
            entry["index_url"] = channel["index_url"]
        channels.append(entry)
    urls = {c["id"]: c.get("index_url") for c in channels}
    architectures = []
    for arch in desktop.get("architectures", []):
        status = arch["status"]
        channel = arch.get("status_from_index")
        if channel:
            if not urls.get(channel):
                raise CatalogError(f"architecture {arch['id']}: channel {channel!r} has no index_url")
            index = get_json(urls[channel])
            if isinstance(index, dict):
                status = "available" if index_lists_arch(index, arch["id"]) else "coming_soon"
        architectures.append({"id": arch["id"], "name": arch["name"], "status": status})
    return {
        "architectures": architectures,
        "channels": channels,
        "hub_catalog_url": desktop.get("hub_catalog_url"),
        "guide": [{"title": s["title"], "body": s["body"]} for s in desktop.get("guide", [])],
    }


def build_mods(root: Path) -> list[dict]:
    mods = []
    for path in sorted((root / "imager" / "mods").glob("*.toml")):
        source = load_toml(path)
        unknown = set(source) - MOD_KEYS
        if unknown:
            raise CatalogError(f"{path.relative_to(root)}: unknown keys {sorted(unknown)}")
        mods.append({
            "id": path.stem,
            "name": source.get("name"),
            "summary": source.get("summary"),
            "arch": source.get("arch"),
            "status": source.get("status"),
            "kind": source.get("kind"),
            "note": source.get("note"),
            "suggest_when": source.get("suggest_when"),
        })
    return mods


def release_files(release: dict, method: str, head) -> list[dict]:
    roles = (("boot", "boot_image"), ("sd_image" if method == "sd_card" else "system", "system_image"),
             ("checksums", "checksums"))
    files = []
    for role, key in roles:
        url = release.get(key)
        if not url:
            continue
        stated = release.get(f"{key}_size_bytes")
        measured = head(url)
        if measured is not None and stated is not None and measured != stated:
            raise CatalogError(f"{url}: served size {measured} differs from {key}_size_bytes {stated}")
        size = measured if measured is not None else stated
        if size is None:
            raise CatalogError(f"{url}: size unknown (HEAD failed and the source states no {key}_size_bytes)")
        files.append({"role": role, "name": url.rsplit("/", 1)[-1], "url": url,
                      "sha256": release.get(f"{key}_sha256"), "size_bytes": size})
    return files


def phone_sources(root: Path) -> dict[str, dict]:
    """Every listed phone as one merged source: device.toml, then its overlay."""
    found: dict[str, dict] = {}
    for path in sorted((root / "devices").glob("*/*/device.toml")):
        device = load_toml(path)
        found[f"{device.get('vendor')}-{device.get('codename')}"] = {"device": device, "where": path}
    for path in sorted((root / "imager" / "phones").glob("*.toml")):
        overlay = load_toml(path)
        where = path.relative_to(root)
        unknown = set(overlay) - OVERLAY_KEYS
        if unknown:
            raise CatalogError(f"{where}: unknown keys {sorted(unknown)}")
        unknown = set(overlay.get("imager", {})) - IMAGER_KEYS
        if unknown:
            raise CatalogError(f"{where}: unknown [imager] keys {sorted(unknown)}")
        key = f"{overlay.get('vendor')}-{overlay.get('codename')}"
        if path.stem != key:
            raise CatalogError(f"{where}: must be named {key}.toml")
        entry = found.setdefault(key, {"device": {}, "where": path})
        entry["overlay"] = overlay
    return {k: v for k, v in found.items() if v["device"].get("imager") or v.get("overlay")}


def build_phone(key: str, entry: dict, head) -> dict:
    device, overlay = entry["device"], entry.get("overlay", {})
    imager = {**device.get("imager", {}), **overlay.get("imager", {})}
    method = imager.get("method")
    releases = device.get("releases", [])
    release = None
    if releases:
        newest = max(enumerate(releases), key=lambda pair: (pair[1].get("date", ""), pair[0]))[1]
        release = {"name": newest.get("name"), "date": newest.get("date"),
                   "files": release_files(newest, method, head)}
    pick = lambda name, default=None: overlay.get(name, device.get(name, default))
    return {
        "id": key,
        "vendor": pick("vendor"),
        "codename": pick("codename"),
        "name": overlay.get("name") or device.get("marketing_name") or device.get("name"),
        "summary": imager.get("summary"),
        "picture_url": imager.get("picture"),
        "tier": pick("tier"),
        "status": imager.get("status"),
        "method": method,
        "fastboot_product": pick("fastboot_product"),
        "never_write": list(overlay.get("never_write", device.get("install", {}).get("preserve", []))),
        "needs": list(imager.get("needs", [])),
        "release": release,
        "guide": [{"title": s.get("title"), "body": s.get("body")} for s in imager.get("guide", [])],
    }


def build(root: Path, head=https_head_size, get_json=https_json, generated_utc: str | None = None) -> dict:
    source = load_toml(root / "imager" / "catalog.toml")
    if generated_utc is None:
        generated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    phones = [build_phone(key, entry, head) for key, entry in phone_sources(root).items()]
    phones.sort(key=lambda p: (STATUS_ORDER.get(p["status"], 9), p["id"]))
    return {
        "schema": 1,
        "generated_utc": generated_utc,
        "imager": {"min_version": source.get("imager", {}).get("min_version")},
        "desktop": build_desktop(source, get_json),
        "mods": build_mods(root),
        "phones": phones,
    }


# -- validation ---------------------------------------------------------------

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[name])


def subset_validate(value, schema: dict, root: dict, path: str = "") -> list[str]:
    """The JSON Schema keywords catalog.schema.json uses, for hosts without jsonschema."""
    where = path or "(top)"
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return subset_validate(value, target, root, path)
    if "anyOf" in schema:
        if not any(not subset_validate(value, option, root, path) for option in schema["anyOf"]):
            return [f"{where}: {value!r} matches none of the allowed forms"]
        return []
    errors = []
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, name) for name in names):
            return [f"{where}: {value!r} is not of type {' or '.join(names)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{where}: length {len(value)} out of range")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if _is_type(value, "number") and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{where}: {value} is less than {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs at least {schema['minItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{where}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(value):
                errors += subset_validate(item, schema["items"], root, f"{path}/{index}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{where}: {name!r} is a required property")
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{where}: needs at least {schema['minProperties']} properties")
        for name, item in value.items():
            if name in properties:
                errors += subset_validate(item, properties[name], root, f"{path}/{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: additional property {name!r} is not allowed")
    return errors


def schema_errors(document: dict, schema: dict) -> list[str]:
    if jsonschema is not None:
        validator = jsonschema.Draft202012Validator(schema)
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}"
                for e in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))]
    return subset_validate(document, schema, schema)


def rule_errors(document: dict) -> list[str]:
    """Rules the schema cannot express."""
    errors = []
    channels = document["desktop"]["channels"]
    if sum(1 for c in channels if c.get("default")) != 1:
        errors.append("desktop/channels: exactly one channel must be the default")
    for channel in channels:
        if channel.get("status") == "available" and not channel.get("index_url"):
            errors.append(f"desktop/channels/{channel.get('id')}: an available channel needs index_url")
    for kind in ("mods", "phones"):
        ids = [item.get("id") for item in document[kind]]
        if len(set(ids)) != len(ids):
            errors.append(f"{kind}: ids must be unique")
    for phone in document["phones"]:
        where = f"phones/{phone.get('id')}"
        release = phone.get("release")
        roles = {f.get("role") for f in (release or {}).get("files", [])}
        if phone.get("status") == "available":
            if phone.get("method") == "none":
                errors.append(f"{where}: an available phone needs an install method")
            if not release:
                errors.append(f"{where}: an available phone needs a release")
            if not phone.get("guide"):
                errors.append(f"{where}: an available phone needs guide steps")
        if phone.get("method") == "fastboot_bundle":
            if not phone.get("fastboot_product"):
                errors.append(f"{where}: fastboot_bundle needs fastboot_product")
            if release and "boot" not in roles:
                errors.append(f"{where}: fastboot_bundle release needs a boot file")
        if phone.get("method") == "sd_card" and release and "sd_image" not in roles:
            errors.append(f"{where}: sd_card release needs an sd_image file")
    texts = [s.get("body", "") for s in document["desktop"]["guide"]]
    texts += [m.get("summary", "") for m in document["mods"]]
    for phone in document["phones"]:
        texts += [phone.get("summary") or ""] + phone.get("needs", [])
        texts += [s.get(k) or "" for s in phone.get("guide", []) for k in ("title", "body")]
    if any("!" in text for text in texts):
        errors.append("user-facing text must not use exclamation marks")
    return errors


def render(document: dict) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def check(document: dict, schema: dict) -> list[str]:
    errors = schema_errors(document, schema) + rule_errors(document)
    if len(render(document)) > MAX_BYTES:
        errors.append(f"catalog is larger than {MAX_BYTES} bytes")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("catalog.json"))
    parser.add_argument("--offline", action="store_true", help="no network: stated sizes and statuses only")
    parser.add_argument("--generated-utc", help="fix generated_utc (tests, reproducing a catalog)")
    parser.add_argument("--check", action="store_true", help="build offline and validate; write nothing")
    args = parser.parse_args(argv)
    if args.generated_utc and not TIMESTAMP.match(args.generated_utc):
        print("build-imager-catalog: --generated-utc must look like 2026-10-23T03:00:00Z", file=sys.stderr)
        return 2
    root = args.root.resolve()
    try:
        schema = json.loads((root / SCHEMA).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"build-imager-catalog: cannot read {SCHEMA}: {exc}", file=sys.stderr)
        return 2
    network = not (args.offline or args.check)
    try:
        document = build(root, head=https_head_size if network else offline,
                         get_json=https_json if network else offline, generated_utc=args.generated_utc)
    except (CatalogError, KeyError, TypeError) as exc:
        print(f"error: {exc}")
        return 1
    errors = check(document, schema)
    for message in errors:
        print(f"error: {message}")
    if errors:
        print(f"{len(errors)} problem(s) found")
        return 1
    summary = (f"{len(document['phones'])} phones, {len(document['mods'])} mods, "
               f"validated with {'jsonschema' if jsonschema else 'the built-in validator'}")
    if args.check:
        print(f"imager catalog: valid ({summary})")
        return 0
    data = render(document)
    temporary = args.output.with_name(f".{args.output.name}.new")
    temporary.write_bytes(data)
    temporary.replace(args.output)
    print(f"wrote {args.output} ({len(data)} bytes; {summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
