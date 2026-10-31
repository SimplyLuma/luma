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

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from .connect_collections import Collection

REFERENCE_PREFIX = "secret-service:"
SECRET_PREFIX = "tide-source:"


class TideSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class FlatpakRuntime:
    app_id: str


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


def library(environment: dict[str, str] | None = None) -> tuple[Path, str | FlatpakRuntime] | None:
    """The Tide library that can hold network sources, and the PYTHONPATH of
    the Tide code that goes with it ("" for the installed Tide).

    An explicit development override wins. Otherwise the installed app's
    canonical library wins over retained preview libraries, regardless of
    modification times. A preview is used only if no native library exists.
    """
    env = os.environ if environment is None else environment
    candidates: list[tuple[Path, str]] = []
    override = env.get("LUMA_TIDE_LIBRARY", "")
    if override:
        path = Path(override)
        if path.is_file() and not _remote_capable(path):
            raise TideSyncError("The selected Tide library does not support music servers.")
        return path, env.get("LUMA_TIDE_PYTHONPATH", "")
    from .app_installs import FLATPAK, TIDE, resolve
    installation = resolve(TIDE, environment)
    if installation.kind == FLATPAK:
        return installation.data_home / "luma-tide" / "library.db", FlatpakRuntime(TIDE)
    native = _data_home(environment) / "luma-tide" / "library.db"
    if native.is_file():
        return native, ""
    candidates.append((native, ""))
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
import json, os, sys
from pathlib import Path
request = json.load(sys.stdin)
from luma_tide.model import LibraryStore
path = (Path(os.environ["XDG_DATA_HOME"]) / "luma-tide/library.db"
        if request.get("sandbox") else Path(request["database"]))
store = LibraryStore(path)
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


def _run_tide(script: str, request: dict, code: str | FlatpakRuntime, *, timeout: int):
    env = dict(os.environ)
    if isinstance(code, FlatpakRuntime):
        command = ["flatpak", "run", "--command=python3", code.app_id, "-c", script]
        request = {**request, "sandbox": True}
    else:
        command = [sys.executable, "-c", script]
        if code:
            env["PYTHONPATH"] = code + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(command, input=json.dumps(request), capture_output=True,
                          text=True, timeout=timeout, env=env)


def _model(database: Path, code: str | FlatpakRuntime, request: dict) -> None:
    try:
        result = _run_tide(_MODEL_SCRIPT, {**request, "database": str(database)}, code, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise TideSyncError("Tide could not update its library. Try syncing again.") from None
    if result.returncode != 0:
        raise TideSyncError("Tide could not update its library. Update Tide and sync again.")


# ── The keyring, through the matching Tide implementation ────────────────

_KEYRING_SCRIPT = r"""
import json, sys
request = json.load(sys.stdin)
try:
    from luma_tide.credentials import SecretServiceStore
    store = SecretServiceStore()
    operation = request["op"]
    result = None
    if operation == "lookup":
        background = getattr(store, "lookup_background", None)
        if background is not None:
            result = background(request["reference"])
        else:
            # Older independently updated Tide builds expose their schema but
            # predate the background-read API. Search their actual schemas in
            # their own sandbox, without requesting libsecret to unlock them.
            Secret = store.Secret
            service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION, None)
            schemas = [store.schema]
            if getattr(store, "_legacy_schema", None) is not None:
                schemas.append(store._legacy_schema)
            for schema in schemas:
                locked = False
                for item in service.search_sync(schema, {"reference": request["reference"]}, Secret.SearchFlags.ALL, None):
                    if item.get_locked():
                        locked = True
                        continue
                    item.load_secret_sync(None)
                    if item.get_locked():
                        locked = True
                        continue
                    value = item.get_secret()
                    if value is None:
                        raise ValueError("Unavailable credential")
                    result = value.get_text()
                    if not isinstance(result, str):
                        raise ValueError("Invalid credential")
                    break
                if result is not None:
                    break
                if locked:
                    raise ValueError("Locked credential")
    elif operation == "store":
        store.store(request["reference"], request["label"], request["secret"])
    elif operation == "clear":
        store.clear(request["reference"])
    else:
        raise ValueError("Unknown credential operation")
    print(json.dumps({"ok": True, "value": result}))
except Exception:
    # Never forward a provider exception or traceback containing credentials.
    print(json.dumps({"ok": False}))
    sys.exit(1)
"""


def _keyring(request: dict, code: str | FlatpakRuntime = ""):
    try:
        result = _run_tide(_KEYRING_SCRIPT, request, code, timeout=10)
        reply = json.loads(result.stdout) if result.returncode == 0 else {}
        if reply.get("ok"):
            return reply.get("value")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    raise TideSyncError("Tide could not access its saved sign-in. Unlock the keyring and sync again.")


def keyring_lookup(reference: str, code: str | FlatpakRuntime = "") -> str | None:
    return _keyring({"op": "lookup", "reference": reference}, code)


def keyring_store(reference: str, label: str, secret: str, code: str | FlatpakRuntime = "") -> None:
    _keyring({"op": "store", "reference": reference, "label": label, "secret": secret}, code)


def keyring_clear(reference: str, code: str | FlatpakRuntime = "") -> None:
    _keyring({"op": "clear", "reference": reference}, code)


def source_marker(environment: dict[str, str] | None = None) -> Path:
    found = library(environment)
    database = found[0] if found else _data_home(environment) / "luma-tide" / "library.db"
    return database.with_name("sources-changed")


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


def tide_collection(database: Path, code: str | FlatpakRuntime, *, fetch_secret, on_pulled) -> Collection:
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
        auth_refs = {uid: auth for uid, _data, auth in _rows(path)}
        wanted = dict(merged)
        for uid, data in merged:
            if local.get(uid) == data:
                continue
            reference = auth_refs.get(uid) or f"{REFERENCE_PREFIX}{uid}"
            if uid not in local:
                secret = fetch_secret(uid)
                if secret:
                    keyring_store(reference, f"Tide: {data['name']}", secret, code)
                    on_pulled(uid, secret)
            _model(path, code, {"op": "add", "item": {**data, "id": uid, "auth_ref": reference}})
        for uid in local:
            if uid not in wanted:
                _model(path, code, {"op": "remove", "id": uid})
                try:
                    keyring_clear(auth_refs.get(uid) or f"{REFERENCE_PREFIX}{uid}", code)
                except Exception:
                    pass
        return True

    return Collection("tide-sources", "music sources", lambda _env: database, read, write)


def sync_tide(*, address: str, token: str, http, state: dict, scope: str,
              environment: dict[str, str] | None, remote_changed: bool) -> str:
    from .connect_collections import sync_collection
    from .connect_sync import HubResponseError

    from .app_installs import NONE, TIDE, resolve

    installation = resolve(TIDE, environment)
    if installation.kind == NONE and not (environment or os.environ).get("LUMA_TIDE_LIBRARY"):
        return "music sources: Tide is not installed, skipped"
    found = library(environment)
    if found is None:
        # A receiving device need not launch Tide before its first sync.
        found = (_data_home(environment) / "luma-tide" / "library.db", "")
    database, code = found
    if not database.is_file() or not _remote_capable(database):
        _model(database, code, {"op": "init"})
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
    sent = received = unavailable = 0
    remote = {}
    rows = _rows(database)
    local_secrets = {uid: keyring_lookup(auth, code) for uid, _data, auth in rows
                     if auth.startswith(REFERENCE_PREFIX)}
    if remote_changed or any(uid not in digests or not local_secrets.get(uid) for uid, _d, _a in rows):
        remote = {item["ref"]: item for item in http.get_json(f"{address}/api/hub/sync/secrets", token=token).get("items", [])}
    for uid, data, auth in rows:
        if not auth.startswith(REFERENCE_PREFIX):
            unavailable += 1
            continue
        secret = local_secrets[uid]
        ref = secret_ref(uid)
        if secret and digest(secret) != digests.get(uid):
            http_put = getattr(http, "put_json", None)
            if http_put is None:
                break
            http_put(f"{address}/api/hub/sync/secrets/{ref}", {"secret": secret}, token=token)
            digests[uid] = digest(secret)
            sent += 1
        elif ref in remote and (not secret or remote_changed and
                remote[ref].get("updated_by") != scope.rsplit("|", 1)[-1]):
            fresh = fetch_secret(uid)
            if fresh and (not secret or digest(fresh) != digests.get(uid)):
                keyring_store(auth, f"Tide: {data['name']}", fresh, code)
                digests[uid] = digest(fresh)
                received += 1
                secret = fresh
        if not secret:
            unavailable += 1
    state[key] = digests
    if unavailable:
        raise TideSyncError(f"{unavailable} music server(s) need a saved sign-in. Sign into the server in Tide and sync again.")
    extra = [part for part in (f"sent {sent} password(s)" if sent else "", f"received {received} password(s)" if received else "") if part]
    return status + (", " + ", ".join(extra) if extra else "")
