#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch Hub's unsigned catalog snapshots, verify them, sign them, publish them.

ADR-028 section 4.1: Hub renders the catalog but never holds the catalog key.
This job runs on the build host (luma-depot-catalog.timer). Each run:

1. GET <hub>/api/depot/catalog/snapshot?schema=4 and ?schema=3 with the Hub
   service token (Authorization: Bearer, read from a 0600 file).
2. Refuse either document unless it parses and passes the schema rules below.
   For schema 4 that includes: only public and unlisted entries, known tiers,
   repositories and permission keys, https URLs, and the 5,000-entry / 8 MiB
   bound. For schema 3 it is the validator installed Depot clients run
   (luma_installer.depot_catalog), so an old client can never be handed a
   document it would reject.
3. Sign the exact bytes Hub served with minisign, verify the signature with the
   public key, and write catalog-4.json(.minisig) and catalog-3.json(.minisig)
   atomically under <site>/catalog/. Unchanged documents are left untouched.
4. Run the upload command (sync-remote.sh --catalog) when anything changed.

Exit status: 0 published or unchanged, 2 not configured (no token), 1 refused.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES_4 = 8 * 1024 * 1024
MAX_APPLICATIONS_4 = 5000
TIERS = {"luma", "verified", "listed"}
PUBLISHED_VISIBILITY = {"public", "unlisted"}
REPOSITORIES = {"luma", "flathub"}
SIGN_IN = {"none", "luma", "third-party", "required-third-party"}
QUALIFICATION = {"admitted", "pending", "unsupported"}
APP_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*){2,}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CatalogRefused(ValueError):
    pass


def _permission_vocabulary() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "flatpak_permissions", ROOT / "scripts/depot/flatpak-permissions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The catalogue spells hyphenated keys with an underscore for older Depots.
    return set(module.VOCABULARY) | {key.replace("-", "_") for key in module.VOCABULARY}


def _https(value, where: str, required: bool = False) -> None:
    if value in (None, ""):
        if required:
            raise CatalogRefused(f"{where}: missing URL")
        return
    if not isinstance(value, str) or not value.startswith("https://"):
        raise CatalogRefused(f"{where}: URL must be https")


def validate_schema4(content: bytes) -> dict:
    if len(content) > MAX_BYTES_4:
        raise CatalogRefused("schema 4: document larger than 8 MiB")
    try:
        document = json.loads(content)
    except json.JSONDecodeError as error:
        raise CatalogRefused(f"schema 4: not JSON: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 4:
        raise CatalogRefused("schema 4: schema_version is not 4")
    generated = document.get("generated_at")
    try:
        dt.datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
    except ValueError as error:
        raise CatalogRefused("schema 4: generated_at is not an ISO 8601 time") from error
    applications = document.get("applications")
    collections = document.get("collections", [])
    if not isinstance(applications, list) or not isinstance(collections, list):
        raise CatalogRefused("schema 4: applications and collections must be lists")
    if len(applications) > MAX_APPLICATIONS_4:
        raise CatalogRefused("schema 4: more than 5,000 applications")

    vocabulary = _permission_vocabulary()
    ids = set()
    for index, app in enumerate(applications):
        where = f"applications[{index}]"
        if not isinstance(app, dict):
            raise CatalogRefused(f"{where}: not an object")
        for field in ("id", "name", "tier", "visibility", "backend"):
            if not isinstance(app.get(field), str) or not app[field]:
                raise CatalogRefused(f"{where}: missing {field}")
        where = f"applications[{app['id']}]"
        if not SLUG.match(app["id"]):
            raise CatalogRefused(f"{where}: id is not a slug")
        if app["id"] in ids:
            raise CatalogRefused(f"{where}: duplicate id")
        ids.add(app["id"])
        if app["visibility"] not in PUBLISHED_VISIBILITY:
            # private and withdrawn entries live only in Hub; a snapshot that
            # carries one would publish something that must not be public
            raise CatalogRefused(f"{where}: visibility {app['visibility']!r} must never be published")
        if app["tier"] not in TIERS:
            raise CatalogRefused(f"{where}: unknown tier {app['tier']!r}")
        if app.get("sign_in") is not None and app["sign_in"] not in SIGN_IN:
            raise CatalogRefused(f"{where}: unknown sign_in")
        if app.get("qualification") is not None and app["qualification"] not in QUALIFICATION:
            raise CatalogRefused(f"{where}: unknown qualification")
        if app["backend"] == "flatpak":
            if not APP_ID.match(str(app.get("app_id", ""))):
                raise CatalogRefused(f"{where}: flatpak entries need a valid app_id")
            if app.get("repository") not in REPOSITORIES:
                raise CatalogRefused(f"{where}: flatpak repository must be luma or flathub")
            if app.get("branch", "stable") not in ("stable", "beta", "nightly"):
                raise CatalogRefused(f"{where}: branch must be stable, beta or nightly")
        if app["tier"] in ("luma", "verified") and app["backend"] == "flatpak" \
                and app.get("repository") != "luma":
            raise CatalogRefused(f"{where}: {app['tier']} apps are hosted on the Luma remote")
        for field in ("homepage", "support_url", "privacy_url", "source_url", "reference_url"):
            _https(app.get(field), f"{where}.{field}")
        icon = app.get("icon")
        if icon is not None:
            if not isinstance(icon, dict):
                raise CatalogRefused(f"{where}.icon: not an object")
            _https(icon.get("url"), f"{where}.icon.url", required=True)
            # An icon without a digest is not an icon: nothing downstream
            # would verify it, so Depot would refuse to draw it and the entry
            # would sit there with a placeholder while claiming artwork.
            if not SHA256.match(str(icon.get("sha256", ""))):
                raise CatalogRefused(f"{where}.icon: no usable sha256 to check the bytes against")
        for shot in app.get("screenshots", []) or []:
            if not isinstance(shot, dict):
                raise CatalogRefused(f"{where}.screenshots: not an object")
            _https(shot.get("url"), f"{where}.screenshots.url", required=True)
        for permission in app.get("permissions", []) or []:
            if not isinstance(permission, dict) or permission.get("key") not in vocabulary:
                raise CatalogRefused(f"{where}.permissions: unknown key {permission!r}")
            if permission.get("level") not in ("standard", "sensitive", "high"):
                raise CatalogRefused(f"{where}.permissions: unknown level")

    for index, collection in enumerate(collections):
        where = f"collections[{index}]"
        if not isinstance(collection, dict) or not SLUG.match(str(collection.get("id", ""))):
            raise CatalogRefused(f"{where}: id is not a slug")
        members = collection.get("applications", [])
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise CatalogRefused(f"{where}: applications must be a list of ids")
        missing = [m for m in members if m not in ids]
        if missing:
            # a private member is legitimately absent; clients skip unknown ids
            print(f"note: {where} names entries not in this snapshot: {missing}", file=sys.stderr)
    return document


def icon_coverage(document: dict) -> tuple[int, int, list[str]]:
    """How many public entries carry an icon, and which do not.

    An entry with no icon is not a broken listing -- Depot draws it as the
    application's monogram -- but it is an entry whose artwork depends on
    whatever else the computer happens to have downloaded, which on a first
    boot is nothing. So the number is published with every run and it is not
    allowed to go backwards.
    """

    listed = [app for app in document["applications"]
              if app.get("visibility") == "public"]
    without = [str(app.get("id", "?")) for app in listed
               if not isinstance(app.get("icon"), dict)]
    return len(listed) - len(without), len(listed), sorted(without)


def refuse_icon_regression(document: dict, published: Path) -> None:
    """Refuse a catalogue that carries icons for fewer entries than the last.

    The published document is the record of what Depot's users already have.
    A render that lost its icons -- a broken asset job, a schema change, a
    field dropped upstream -- would otherwise publish silently and take the
    artwork off every new installation. A run that adds entries without icons
    is refused for the same reason: coverage is measured as a count, and the
    count may only go up.
    """

    covered, total, without = icon_coverage(document)
    print(f"icons: {covered}/{total} public entries carry one")
    if without:
        print(f"note: no icon in the catalogue for: {', '.join(without)}", file=sys.stderr)
    if not published.is_file():
        return
    try:
        previous = json.loads(published.read_bytes())
    except (OSError, ValueError):
        return
    if not isinstance(previous, dict) or not isinstance(previous.get("applications"), list):
        return
    before, _total, _missing = icon_coverage(previous)
    if covered < before:
        raise CatalogRefused(
            f"icon coverage fell from {before} to {covered} entries; "
            f"entries now without a usable icon: {', '.join(without) or 'none'}")


MEDIA_PREFIX = "https://dl.simplyluma.com/media/listings/"
MIN_ICON = 256
MIN_DESCRIPTION = 60


def install_path(app: dict) -> str:
    """How Depot installs a listing, or "" when it has no automated channel."""
    backend, repository = app.get("backend"), app.get("repository")
    sources = app.get("sources") if isinstance(app.get("sources"), dict) else {}
    if backend == "flatpak" and repository in ("luma", "flathub"):
        return f"flatpak:{repository}"
    if backend == "snap" and repository == "snap-store" and isinstance(sources.get("snap"), dict):
        return "snap"
    if backend == "rpm" and repository == "luma" and isinstance(sources.get("luma_system"), dict):
        return "luma-image"
    if backend == "deb" and isinstance(sources.get("deb_repository"), dict):
        return "deb:publisher-repository"
    if backend == "rpm" and isinstance(sources.get("rpm_repository"), dict):
        return "rpm:fedora" if repository == "fedora" else "rpm:publisher-repository"
    return ""


def quality_gate(document: dict, media_root: Path | None = None) -> tuple[list[str], dict, list[str]]:
    """Every public listing must look and work like a store listing.

    Returns (failures, counts by install path, publisher-only exceptions).
    A listing fails without an icon of at least 256 px, a real description,
    screenshots (system tools excepted), or an automated install path; the
    only listing allowed no install path is a documented publisher-only
    exception (``channel.kind == "publisher"`` with its reason). When the
    site's media directory is given, every image Luma serves must be there
    with the digest the listing names, so a catalogue is never published
    ahead of its pictures.
    """

    failures, counts, exceptions = [], {}, []
    for app in document["applications"]:
        if app.get("visibility") != "public":
            continue
        where = app["id"]
        icon = app.get("icon")
        if not isinstance(icon, dict):
            failures.append(f"{where}: no icon")
        elif int(icon.get("width") or 0) < MIN_ICON or int(icon.get("height") or 0) < MIN_ICON:
            failures.append(f"{where}: icon smaller than {MIN_ICON} px (or its size is not recorded)")
        if len((app.get("description") or "").strip()) < MIN_DESCRIPTION:
            failures.append(f"{where}: no real description")
        if not (app.get("summary") or "").strip():
            failures.append(f"{where}: no summary")
        shots = app.get("screenshots") or []
        pending = (app.get("screenshots_pending") or "").strip()
        if not shots and not app.get("system_tool"):
            if pending and app.get("tier") == "luma":
                # Only Luma's own apps, only with the reason written down:
                # reported on every run until the screenshots exist.
                exceptions.append(f"{where}: screenshots pending: {pending}")
            else:
                failures.append(f"{where}: no screenshots")
        path = install_path(app)
        channel = app.get("channel") if isinstance(app.get("channel"), dict) else {}
        if not path:
            if channel.get("kind") == "publisher" and (channel.get("reason") or "").strip():
                path = "publisher-only"
                exceptions.append(f"{where}: {channel['reason']}")
            else:
                failures.append(f"{where}: no automated install path and no documented publisher-only reason")
        counts[path or "none"] = counts.get(path or "none", 0) + 1
        if media_root is not None:
            for image in ([icon] if isinstance(icon, dict) else []) + [s for s in shots if isinstance(s, dict)]:
                url = str(image.get("url", ""))
                if not url.startswith(MEDIA_PREFIX):
                    continue
                local = media_root / "media/listings" / url[len(MEDIA_PREFIX):]
                if not local.is_file():
                    failures.append(f"{where}: {url} is not in the site's media")
                elif hashlib.sha256(local.read_bytes()).hexdigest() != image.get("sha256"):
                    failures.append(f"{where}: {url} does not match its digest")
    return failures, counts, exceptions


def refuse_below_quality(document: dict, media_root: Path | None) -> None:
    failures, counts, exceptions = quality_gate(document, media_root)
    print("install paths: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))
    for line in exceptions:
        print(f"exception: {line}")
    if failures:
        raise CatalogRefused("quality gate: " + "; ".join(failures))


def validate_schema3(content: bytes) -> None:
    sys.path.insert(0, str(ROOT / "src/luma-installer"))
    try:
        from luma_installer.depot_catalog import CatalogError, MAX_BYTES, validate_catalog
    finally:
        sys.path.pop(0)
    if len(content) > MAX_BYTES:
        raise CatalogRefused("schema 3: larger than installed clients accept")
    try:
        document = json.loads(content)
        if document.get("schema_version") != 3:
            raise CatalogRefused("schema 3: schema_version is not 3")
        validate_catalog(document)
    except (CatalogError, json.JSONDecodeError, AttributeError) as error:
        raise CatalogRefused(f"schema 3: {error}") from error


def fetch(url: str, token: str, limit: int, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "luma-depot-catalog-signer",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https only)
        if response.status != 200:
            raise CatalogRefused(f"{url}: HTTP {response.status}")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise CatalogRefused(f"{url}: response larger than {limit} bytes")
    return content


def sign_and_verify(content: bytes, name: str, key: Path, public_key: Path, workdir: Path,
                    comment: str) -> tuple[Path, Path]:
    document = workdir / name
    document.write_bytes(content)
    signature = workdir / f"{name}.minisig"
    subprocess.run(["minisign", "-S", "-s", str(key), "-m", str(document),
                    "-x", str(signature), "-t", comment],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    subprocess.run(["minisign", "-V", "-q", "-p", str(public_key), "-m", str(document),
                    "-x", str(signature)], check=True, capture_output=True)
    return document, signature


def install_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(source.read_bytes())
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", default=os.environ.get("DEPOT_HUB_URL", "https://hub.simplyluma.com"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True, help="minisign secret key")
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--upload", help="command run when a document changed")
    parser.add_argument("--skip-quality-gate", action="store_true",
                        help="sign without the listing quality gate (tests and emergencies only)")
    parser.add_argument("--from-dir", type=Path,
                        help="read catalog-4.json and catalog-3.json from a directory instead of Hub (testing)")
    args = parser.parse_args(argv)

    if args.from_dir is None:
        if not args.token_file.is_file() or args.token_file.stat().st_size == 0:
            print(f"not configured: no Hub service token at {args.token_file}", file=sys.stderr)
            return 2
        if args.token_file.stat().st_mode & 0o077:
            print(f"refusing: {args.token_file} must be readable by its owner only", file=sys.stderr)
            return 1
        token = args.token_file.read_text().strip()
        base = args.hub.rstrip("/")
        _https(base, "hub")
        content4 = fetch(f"{base}/api/depot/catalog/snapshot?schema=4", token, MAX_BYTES_4)
        content3 = fetch(f"{base}/api/depot/catalog/snapshot?schema=3", token, MAX_BYTES_4)
    else:
        content4 = (args.from_dir / "catalog-4.json").read_bytes()
        content3 = (args.from_dir / "catalog-3.json").read_bytes()

    catalog_dir = args.site / "catalog"
    try:
        document4 = validate_schema4(content4)
        refuse_icon_regression(document4, catalog_dir / "catalog-4.json")
        if not args.skip_quality_gate:
            refuse_below_quality(document4, args.site)
        validate_schema3(content3)
    except CatalogRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1

    changed = False
    with tempfile.TemporaryDirectory() as tmp:
        for name, content, comment in (
            ("catalog-4.json", content4,
             f"schema 4 generated {document4['generated_at']} sha256 {hashlib.sha256(content4).hexdigest()}"),
            ("catalog-3.json", content3, f"schema 3 sha256 {hashlib.sha256(content3).hexdigest()}"),
        ):
            current = catalog_dir / name
            if current.is_file() and current.read_bytes() == content \
                    and (catalog_dir / f"{name}.minisig").is_file():
                continue
            document, signature = sign_and_verify(content, name, args.key, args.public_key,
                                                  Path(tmp), comment)
            # Both files are staged beside their targets and swapped in by two
            # renames. A client that fetches between them sees a signature that
            # does not match and keeps its last verified copy (ADR-028 4.1).
            install_atomically(signature, catalog_dir / f"{name}.minisig.next")
            install_atomically(document, catalog_dir / f"{name}.next")
            os.replace(catalog_dir / f"{name}.next", catalog_dir / name)
            os.replace(catalog_dir / f"{name}.minisig.next", catalog_dir / f"{name}.minisig")
            changed = True
            print(f"signed {name} ({len(content)} bytes)")

    if not changed:
        print("catalog unchanged")
        return 0
    if args.upload:
        subprocess.run(args.upload, shell=True, check=True)  # noqa: S602 (operator-configured)
    return 0


if __name__ == "__main__":
    sys.exit(main())
