# SPDX-License-Identifier: Apache-2.0

"""Account-wide lists shared by every device: Weather cities, world clocks and Leaf's reading.

Unlike notes and contacts, which a device owns and snapshots, these lists
belong to the account. Each sync compares the local file with the list as it
was after the previous sync (the *base*) to work out what this device added,
edited, removed or reordered, sends only that, and writes back whatever the
source of truth answers. The source of truth does the merging, so the same
exchange works whether it is the Luma Hub or, later, a machine of the user's.

The app files are only ever replaced atomically, and never when the app wrote
to them after they were read: that round is abandoned and the next one (the
settle loop repeats while files keep changing) starts again from the file.

Each list is read from the install of its app that is in use, the system
package's or the Flatpak's (app_installs). When that changes, the list the
previous install held is not this device's history any more, so the next
exchange starts over as a first sync — offering everything and deleting
nothing — instead of reading every difference as a removal.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

from .app_installs import CLOCK, LEAF, WEATHER, app_environment


def _data_home(environment: dict[str, str] | None) -> Path:
    env = os.environ if environment is None else environment
    return Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME", str(Path.home()))) / ".local" / "share")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")), path.read_bytes()
    except FileNotFoundError:
        return default, b""
    except (OSError, ValueError):
        return None, None


def _replace(path: Path, content: object, expected: bytes) -> bool:
    """Atomically write ``content`` unless the file changed since it was read."""
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        current = b""
    if current != expected:
        return False
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.stem}-sync-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(content, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


@dataclass(frozen=True)
class Collection:
    name: str
    label: str
    path: Callable[[dict[str, str] | None], Path]
    read: Callable[[Path], tuple[list[tuple[str, dict]], bytes] | None]
    write: Callable[[Path, list[tuple[str, dict]], bytes], bool]
    # Where the list was before sync knew about Flatpak installs: what a base
    # recorded without a path was read from.
    legacy_path: Callable[[dict[str, str] | None], Path] | None = None


# ── Weather: prairie/weather/places.json is a list of Place records ────────

PLACE_FIELDS = ("name", "region", "country", "latitude", "longitude", "timezone")


def _places_read(path: Path):
    payload, raw = _read_json(path, [])
    if raw is None:
        return None
    records = payload if isinstance(payload, list) else payload.get("places", []) if isinstance(payload, dict) else []
    items = []
    for record in records:
        try:
            items.append((str(record["uid"]), {
                "name": str(record["name"]), "region": str(record.get("region", "")),
                "country": str(record.get("country", "")), "latitude": float(record.get("latitude", 0.0)),
                "longitude": float(record.get("longitude", 0.0)), "timezone": str(record.get("timezone", "")),
            }))
        except (KeyError, TypeError, ValueError):
            continue
    return items, raw


def _places_write(path: Path, items, expected: bytes) -> bool:
    return _replace(path, [{"uid": uid, **{field: data[field] for field in PLACE_FIELDS}} for uid, data in items], expected)


# ── Clock: prairie/clock/state.json holds world clocks beside alarms ───────
# Alarms and timers stay on the device; only "world" is replaced.

def _clocks_read(path: Path):
    payload, raw = _read_json(path, {})
    if raw is None or not isinstance(payload, dict):
        return None
    items = []
    for record in payload.get("world", []) or []:
        try:
            items.append((str(record["uid"]), {"label": str(record["label"]), "zone": str(record["zone"])}))
        except (KeyError, TypeError):
            continue
    return items, raw


def _clocks_write(path: Path, items, expected: bytes) -> bool:
    payload, raw = _read_json(path, {})
    if raw is None or raw != expected or not isinstance(payload, dict):
        return False
    payload = {"alarms": [], "timers": [], **payload}
    payload["world"] = [{"uid": uid, "label": data["label"], "zone": data["zone"]} for uid, data in items]
    return _replace(path, payload, expected)


def _leaf_path(environment):
    from .connect_leaf import database
    return database(_data_home(app_environment(LEAF, environment)))


def _leaf_legacy_path(environment):
    from .connect_leaf import database
    return database(_data_home(environment))


def _leaf_read(path: Path):
    from .connect_leaf import read
    return read(path)


def _leaf_write(path: Path, items, expected: bytes) -> bool:
    from .connect_leaf import write
    return write(path, items, expected)


COLLECTIONS = (
    Collection("weather-places", "weather places",
               lambda env: _data_home(app_environment(WEATHER, env)) / "prairie" / "weather" / "places.json",
               _places_read, _places_write,
               lambda env: _data_home(env) / "prairie" / "weather" / "places.json"),
    Collection("world-clocks", "world clocks",
               lambda env: _data_home(app_environment(CLOCK, env)) / "prairie" / "clock" / "state.json",
               _clocks_read, _clocks_write,
               lambda env: _data_home(env) / "prairie" / "clock" / "state.json"),
    # Leaf: where each book is up to, and what was marked in it (connect_leaf.py).
    Collection("leaf-books", "books", _leaf_path, _leaf_read, _leaf_write, _leaf_legacy_path),
)


def changes_since(base: list[tuple[str, dict]] | None, local: list[tuple[str, dict]]):
    """What this device did to the list since the last sync."""
    if base is None:
        # First sync: offer everything; the source of truth drops duplicates.
        return [{"op": "upsert", "uid": uid, "data": data, "index": index}
                for index, (uid, data) in enumerate(local)], None
    before = dict(base)
    now = dict(local)
    changes = []
    for index, (uid, data) in enumerate(local):
        if uid not in before:
            changes.append({"op": "upsert", "uid": uid, "data": data, "index": index})
        elif data != before[uid]:
            changes.append({"op": "upsert", "uid": uid, "data": data})
    changes.extend({"op": "delete", "uid": uid} for uid, _data in base if uid not in now)
    kept_before = [uid for uid, _data in base if uid in now]
    kept_now = [uid for uid, _data in local if uid in before]
    order = [uid for uid, _data in local] if kept_before != kept_now else None
    return changes, order


def fingerprint(items: list[tuple[str, dict]]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()


def sync_collection(collection: Collection, *, address: str, token: str, http, state: dict,
                    scope: str, environment: dict[str, str] | None, remote_changed: bool) -> str:
    """One exchange for one list. Returns a short, content-free status line."""
    path = collection.path(environment)
    read = collection.read(path)
    if read is None:
        return f"{collection.label}: unreadable, skipped"
    local, raw = read
    key = f"{scope}|collection|{collection.name}"
    record = state.get(key)
    if record:
        recorded = record.get("path")
        if recorded is None and collection.legacy_path is not None:
            recorded = str(collection.legacy_path(environment))
        if recorded is not None and recorded != str(path):
            # A different install's file: start over rather than delete
            # everything the previous one held from the whole account.
            record = None
    base = [(uid, data) for uid, data in record["items"]] if record else None
    changes, order = changes_since(base, local)
    url = f"{address}/api/hub/sync/collections/{collection.name}"
    if not changes and order is None and record and not remote_changed:
        return f"{collection.label}: unchanged"
    if changes or order is not None:
        revision = int(record["revision"]) if record else int(http.get_json(url, token=token)["revision"])
        reply = http.post_json(url, {"base_revision": revision, "changes": changes, "order": order}, token=token)
    else:
        reply = http.get_json(url, token=token)
    merged = [(str(item["uid"]), dict(item["data"])) for item in reply.get("items", [])]
    if merged != local and not collection.write(path, merged, raw):
        # The app wrote meanwhile. Keep the old base so its edit is still
        # seen as a change on the next round.
        return f"{collection.label}: changed during sync, will retry"
    state[key] = {"revision": int(reply.get("revision", 0)), "items": [[uid, data] for uid, data in merged],
                  "path": str(path)}
    return f"{collection.label}: {len(merged)} synced" + (f", sent {len(changes)} change(s)" if changes else "")


def source_directories(environment: dict[str, str] | None = None) -> tuple[Path, ...]:
    return tuple(collection.path(environment).parent for collection in COLLECTIONS)
