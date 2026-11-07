# SPDX-License-Identifier: Apache-2.0
"""Sign and publish the Hub's Depot catalogue.

The Hub holds every listing; this turns its catalogue into the one Depot
trusts:

1. fetch the unsigned catalogue from the Hub;
2. copy every listing picture the Hub serves into R2 at
   media.simplyluma.com/depot/<app-id>/<sha256>.<ext> (content-addressed, so
   never overwritten) and point the catalogue at those copies;
3. sign it with the Depot catalogue key (minisign), and
4. upload catalogue.json and its .minisig to dl.simplyluma.com/depot/catalogue/v1/
   in one transfer, then read both back over HTTPS and verify them.

Nothing is uploaded when the signed bytes would not change.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import images
from .config import Config
from .hub import Hub

APP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z")
VOLATILE = ("generated", "generated_at", "signed")
TYPES = {"png": "image/png", "webp": "image/webp", "svg": "image/svg+xml"}


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), "catalogue:", message, flush=True)


class Mirror:
    def __init__(self, config: Config, hub: Hub, r2, cache: dict):
        self.config, self.hub, self.r2, self.cache = config, hub, r2, cache
        hub_origin = urllib.parse.urlsplit(config.hub)
        self.origins = {(hub_origin.scheme, hub_origin.netloc), ("https", "simplyluma.com"),
                        ("https", "www.simplyluma.com")}

    def wanted(self, value: str) -> bool:
        if value.startswith("/"):
            return value.startswith(("/api/depot/media/", "/media/", "/uploads/"))
        parts = urllib.parse.urlsplit(value)
        return (parts.scheme, parts.netloc) in self.origins and bool(
            re.search(r"\.(png|webp|svg)(\?|$)|/media/|/uploads/", parts.path))

    def copy(self, url: str, owner: str) -> str:
        if url in self.cache:
            return self.cache[url]
        with tempfile.NamedTemporaryFile(dir=self.config.work) as handle:
            path = Path(handle.name)
            self.hub.download(url, path, limit=images.MAX_BYTES)
            data = path.read_bytes()
        kind, _, _ = images.identify(data)
        digest = hashlib.sha256(data).hexdigest()
        key = f"{self.config.media_prefix}{owner}/{digest}.{kind}"
        if not self.r2.exists(key):
            self.r2.put(key, data, TYPES[kind])
            log(f"media {key} ({len(data)} bytes)")
        public = self.config.media_base.rstrip("/") + "/" + key[len(self.config.media_prefix):]
        self.cache[url] = public
        return public

    def walk(self, node, owner: str = "shared"):
        if isinstance(node, dict):
            ident = node.get("id") or node.get("app_id")
            if isinstance(ident, str) and APP_ID.fullmatch(ident):
                owner = ident
            return {k: self.walk(v, owner) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v, owner) for v in node]
        if isinstance(node, str) and self.wanted(node):
            try:
                return self.copy(node, owner)
            except (images.ImageError, OSError, RuntimeError) as error:
                log(f"skipping picture {node}: {error}")
                return None
        return node


def sign(config: Config, data: bytes, out: Path) -> bytes:
    digest = hashlib.sha256(data).hexdigest()
    (out / "catalogue.json").write_bytes(data)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    subprocess.run(["minisign", "-S", "-s", str(config.catalogue_key), "-m", str(out / "catalogue.json"),
                    "-x", str(out / "catalogue.json.minisig"),
                    "-t", f"depot-catalogue v1 sha256={digest} signed={stamp}"],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["minisign", "-V", "-q", "-p", str(config.catalogue_pub), "-m", str(out / "catalogue.json"),
                    "-x", str(out / "catalogue.json.minisig")], check=True, capture_output=True)
    return (out / "catalogue.json.minisig").read_bytes()


def origin_settings(config: Config) -> tuple[str, str]:
    values = {}
    for line in (config.depot_keys / "secrets/dl-origin.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values["DL_RSYNC_DEST"].rstrip(":"), values["DL_SSH_KEY"]


def upload(config: Config, local: Path) -> None:
    dest, key = origin_settings(config)
    subprocess.run(["rsync", "--recursive", "--times", "--chmod=D0755,F0644", "--no-owner", "--no-group",
                    "--delay-updates", "--mkpath", "--timeout=120",
                    "-e", f"ssh -i {key} -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=yes",
                    f"{local}/", f"{dest}:{config.catalogue_dest}"],
                   check=True, capture_output=True, timeout=600, stdin=subprocess.DEVNULL)


def read_back(config: Config, data: bytes, signature: bytes) -> None:
    for suffix, expected in (("", data), (".minisig", signature)):
        request = urllib.request.Request(config.catalogue_url + suffix + f"?v={int(time.time())}",
                                         headers={"User-Agent": "luma-depot-review/1", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.read() != expected:
                raise RuntimeError(f"public catalogue{suffix} differs from what was signed")


def validate(document) -> None:
    if not isinstance(document, dict):
        raise ValueError("the catalogue must be a JSON object")
    items = [v for k, v in document.items() if k in ("apps", "looks", "tweaks", "items") and isinstance(v, list)]
    if not items:
        raise ValueError("the catalogue lists no apps, looks or tweaks")
    for listing in (x for group in items for x in group):
        ident = listing.get("id") if isinstance(listing, dict) else None
        if not isinstance(ident, str) or not ident:
            raise ValueError("a catalogue item has no id")


def publish(config: Config, hub: Hub, r2, force: bool = False) -> dict | None:
    state_file = config.state / "catalogue.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    document = hub.call("GET", "catalogue")
    validate(document)
    mirror = Mirror(config, hub, r2, state.get("media", {}))
    document = mirror.walk(document)
    # The Hub stamps each response; only a change in content earns a new signature.
    stable = {k: v for k, v in document.items() if k not in VOLATILE}
    document["signed"] = {"by": "Luma Depot", "key": config.catalogue_pub.name,
                          "source_sha256": hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()}
    source = document["signed"]["source_sha256"]
    if not force and state.get("source_sha256") == source:
        return None
    document["signed"]["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    out = config.work / "catalogue"
    out.mkdir(parents=True, exist_ok=True)
    for name in os.listdir(out):
        os.unlink(out / name)
    signature = sign(config, data, out)
    upload(config, out)
    read_back(config, data, signature)
    state.update({"source_sha256": source, "sha256": hashlib.sha256(data).hexdigest(),
                  "published": document["signed"]["at"], "media": mirror.cache})
    state_file.write_text(json.dumps(state, indent=1) + "\n")
    log(f"published {len(data)} bytes, sha256 {state['sha256'][:12]}")
    return state
