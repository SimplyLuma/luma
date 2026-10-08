# SPDX-License-Identifier: Apache-2.0

"""Tide music sources, synced across the account's devices.

Only network sources travel (a Navidrome server, say); a folder on one device
means nothing on another. The source list is an account-wide list like world
clocks. Its password never rides in that list: it moves separately, from this
device's keyring to the source of truth (which keeps it sealed and hands it
only to the account's enrolled devices) and from there into the other
devices' keyrings, under the exact schema Tide itself reads.

Tide owns its library. This module reads it read-only, and adds or removes a
source only through Tide's own model code, so a Tide schema change cannot be
bypassed by sync. Removing a source elsewhere forgets its records here; files
Tide downloaded for offline play are left on disk.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from .connect_collections import Collection

SCHEMA_NAME = "org.projectluma.Tide.SourcePassword"
REFERENCE_PREFIX = "secret-service:"
SECRET_PREFIX = "tide-source:"


class TideSyncError(RuntimeError):
    pass


# ── Where Tide keeps its library, and whose code writes to it ─────────────

def _data_home(environment: dict[str, str] | None) -> Path:
    env = os.environ if environment is None else environment
    return Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME", str(Path.home()))) / ".local" / "share")


def _remote_capable(path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return {"auth_ref", "account", "kind", "uri"} <= columns


def library(environment: dict[str, str] | None = None) -> tuple[Path, str] | None:
    """The Tide library that can hold network sources, and the PYTHONPATH of
    the Tide code that goes with it ("" for the installed Tide).

    A Tide development build keeps its own data beside its source tree
    (`<root>/data/luma-tide/library.db`, `<root>/src/luma_tide`); until that
    build ships, its library is the one with network sources in it.
    """
    env = os.environ if environment is None else environment
    candidates: list[tuple[Path, str]] = []
    override = env.get("LUMA_TIDE_LIBRARY", "")
    if override:
        candidates.append((Path(override), env.get("LUMA_TIDE_PYTHONPATH", "")))
    candidates.append((_data_home(environment) / "luma-tide" / "library.db", ""))
    development = _data_home(environment) / "luma-dev"
    try:
        for root in development.iterdir():
            database = root / "data" / "luma-tide" / "library.db"
            if database.is_file() and (root / "src" / "luma_tide" / "model.py").is_file():
                candidates.append((database, str(root / "src")))
    except OSError:
        pass
    usable = [(path, code) for path, code in candidates if path.is_file() and _remote_capable(path)]
    if not usable:
        return None
    return max(usable, key=lambda item: item[0].stat().st_mtime)


_MODEL_SCRIPT = r"""
import json, sys
from pathlib import Path
request = json.load(sys.stdin)
from luma_tide.model import LibraryStore
store = LibraryStore(Path(request["database"]))
if request["op"] == "add":
    item = request["item"]
    store.add_source(item["name"], item["kind"], item["uri"], local=False,
                     capabilities=item["capabilities"], auth_ref=item["auth_ref"],
                     account=item["account"] or None, source_id=item["id"])
elif request["op"] == "remove":
    try:
        store.remove_source(request["id"])
    except KeyError:
        pass
print(json.dumps({"ok": True}))
"""


def _model(database: Path, code: str, request: dict) -> None:
    env = dict(os.environ)
    if code:
        env["PYTHONPATH"] = code + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run([sys.executable, "-c", _MODEL_SCRIPT], input=json.dumps({**request, "database": str(database)}),
                               capture_output=True, text=True, timeout=60, env=env)
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-1:] or ["no detail"]
        raise TideSyncError(f"Tide could not update its library ({tail[0]}).")


# ── The keyring, in Tide's own schema ─────────────────────────────────────

def _secret_api():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    schema = Secret.Schema.new(SCHEMA_NAME, Secret.SchemaFlags.NONE,
                               {"reference": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def keyring_lookup(reference: str) -> str | None:
    Secret, schema = _secret_api()
    return Secret.password_lookup_sync(schema, {"reference": reference}, None)


def keyring_store(reference: str, label: str, secret: str) -> None:
    Secret, schema = _secret_api()
    if not Secret.password_store_sync(schema, {"reference": reference}, Secret.COLLECTION_DEFAULT, label, secret, None):
        raise TideSyncError("The keyring would not save the music server password. Unlock it and sync again.")


def keyring_clear(reference: str) -> None:
    Secret, schema = _secret_api()
    Secret.password_clear_sync(schema, {"reference": reference}, None)


# ── The shared list ───────────────────────────────────────────────────────

def _rows(database: Path) -> list[tuple[str, dict, str]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id,name,kind,uri,capabilities,auth_ref,account FROM sources WHERE local=0 ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    finally:
        connection.close()
    return [(row["id"], {
        "name": row["name"], "kind": row["kind"], "uri": row["uri"], "account": row["account"] or "",
        "capabilities": sorted(set(filter(None, (row["capabilities"] or "").split(",")))),
    }, row["auth_ref"] or "") for row in rows]


def secret_ref(source_id: str) -> str:
    return f"{SECRET_PREFIX}{source_id}"


def tide_collection(database: Path, code: str, *, fetch_secret, on_pulled) -> Collection:
    def read(path: Path):
        try:
            rows = _rows(path)
        except sqlite3.Error:
            return None
        items = [(uid, data) for uid, data, _auth in rows]
        return items, hashlib.sha256(json.dumps(items, sort_keys=True).encode()).digest()

    def write(path: Path, merged, expected: bytes) -> bool:
        current = read(path)
        if current is None or current[1] != expected:
            return False
        local = dict(current[0])
        wanted = dict(merged)
        for uid, data in merged:
            if local.get(uid) == data:
                continue
            reference = f"{REFERENCE_PREFIX}{uid}"
            if uid not in local:
                secret = fetch_secret(uid)
                if secret:
                    keyring_store(reference, f"Tide: {data['name']}", secret)
                    on_pulled(uid, secret)
            _model(path, code, {"op": "add", "item": {**data, "id": uid, "auth_ref": reference}})
        for uid in local:
            if uid not in wanted:
                _model(path, code, {"op": "remove", "id": uid})
                try:
                    keyring_clear(f"{REFERENCE_PREFIX}{uid}")
                except Exception:
                    pass
        return True

    return Collection("tide-sources", "music sources", lambda _env: database, read, write)


def sync_tide(*, address: str, token: str, http, state: dict, scope: str,
              environment: dict[str, str] | None, remote_changed: bool) -> str:
    from .connect_collections import sync_collection
    from .connect_sync import HubResponseError

    from .app_installs import FLATPAK, TIDE, resolve

    if resolve(TIDE, environment).kind == FLATPAK:
        # The Flatpak Tide keeps server passwords in its own sandboxed keyring,
        # which nothing outside the sandbox can write, and this release of it
        # has no server sources. Its local folders never sync anyway.
        return "music sources: Tide is installed as a Flatpak, which does not take server sources yet, skipped"
    found = library(environment)
    if found is None:
        return "music sources: no Tide library with network sources, skipped"
    database, code = found
    key = f"{scope}|tide-secrets"
    digests: dict[str, str] = dict(state.get(key, {}))

    def digest(secret: str) -> str:
        return hmac.new(token.encode(), secret.encode(), hashlib.sha256).hexdigest()

    def fetch_secret(uid: str) -> str | None:
        try:
            return str(http.get_json(f"{address}/api/hub/sync/secrets/{secret_ref(uid)}", token=token).get("secret") or "") or None
        except HubResponseError as error:
            if error.status == 404:
                return None
            raise

    def pulled(uid: str, secret: str) -> None:
        digests[uid] = digest(secret)

    status = sync_collection(tide_collection(database, code, fetch_secret=fetch_secret, on_pulled=pulled),
                             address=address, token=token, http=http, state=state, scope=scope,
                             environment=environment, remote_changed=remote_changed)

    # Passwords: send one this device changed; take one another device changed.
    sent = received = 0
    remote = {}
    if remote_changed or any(uid not in digests for uid, _d, _a in _rows(database)):
        remote = {item["ref"]: item for item in http.get_json(f"{address}/api/hub/sync/secrets", token=token).get("items", [])}
    for uid, data, auth in _rows(database):
        if not auth.startswith(REFERENCE_PREFIX):
            continue
        try:
            secret = keyring_lookup(auth)
        except Exception:
            continue  # a locked keyring: try again next time, never send a guess
        ref = secret_ref(uid)
        if secret and digest(secret) != digests.get(uid):
            http_put = getattr(http, "put_json", None)
            if http_put is None:
                break
            http_put(f"{address}/api/hub/sync/secrets/{ref}", {"secret": secret}, token=token)
            digests[uid] = digest(secret)
            sent += 1
        elif remote_changed and ref in remote and remote[ref].get("updated_by") != scope.rsplit("|", 1)[-1]:
            fresh = fetch_secret(uid)
            if fresh and digest(fresh) != digests.get(uid):
                keyring_store(auth, f"Tide: {data['name']}", fresh)
                digests[uid] = digest(fresh)
                received += 1
    state[key] = digests
    extra = [part for part in (f"sent {sent} password(s)" if sent else "", f"received {received} password(s)" if received else "") if part]
    return status + (", " + ", ".join(extra) if extra else "")
