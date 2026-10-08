#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Shared lists and photos against an in-process fake source of truth.

The fake implements the same merge rules as the hub (last arrival wins,
tombstoned deletes, identity de-duplication, reorder only on the latest
revision), so two simulated devices can be driven through real exchanges.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

from prairie_apps.connect_collections import COLLECTIONS, changes_since, sync_collection
from prairie_apps.connect_photos import PhotoSyncError, sync_photos
from prairie_apps.connect_sync import HubResponseError

PLACES = next(c for c in COLLECTIONS if c.name == "weather-places")
CLOCKS = next(c for c in COLLECTIONS if c.name == "world-clocks")


class FakeHub:
    def __init__(self):
        self.lists = {}
        self.photos = {}
        self.holders = {}
        self.calls = []

    def _list(self, name):
        return self.lists.setdefault(name, {"revision": 0, "items": [], "gone": set()})

    def _identity(self, name, data):
        if name == "weather-places":
            return f"{data['latitude']:.3f},{data['longitude']:.3f}"
        return f"{data['zone']}|{data['label'].strip().lower()}"

    def get_json(self, url, *, token="", timeout=None):
        self.calls.append(("GET", url))
        if "/collections/" in url:
            current = self._list(url.rsplit("/", 1)[1])
            return {"revision": current["revision"], "items": [dict(i) for i in current["items"]]}
        if url.endswith("/photos"):
            return {"items": [dict(meta) for sha, meta in self.photos.items() if meta.get("stored") and self.holders.get(sha)]}
        raise AssertionError(url)

    def post_json(self, url, payload, *, token=""):
        self.calls.append(("POST", url))
        if url.endswith("/photos/manifest"):
            device = payload["device_id"]
            for sha in list(self.holders):
                self.holders[sha].discard(device)
            for item in payload["items"]:
                self.photos.setdefault(item["sha256"], {**item, "stored": False})
                self.holders.setdefault(item["sha256"], set()).add(device)
            return {"missing": [i["sha256"] for i in payload["items"] if not self.photos[i["sha256"]]["stored"]]}
        name = url.rsplit("/", 1)[1]
        current = self._list(name)
        items = current["items"]
        changed = False
        for change in payload["changes"]:
            existing = next((i for i in items if i["uid"] == change["uid"]), None)
            if change["op"] == "delete":
                if existing:
                    items.remove(existing)
                    changed = True
                continue
            if existing:
                if existing["data"] != change["data"]:
                    existing["data"] = change["data"]
                    changed = True
                continue
            if any(self._identity(name, i["data"]) == self._identity(name, change["data"]) for i in items):
                continue
            index = change.get("index")
            items.insert(len(items) if index is None else min(index, len(items)), {"uid": change["uid"], "data": change["data"]})
            changed = True
        if payload.get("order") and payload["base_revision"] == current["revision"]:
            rank = {uid: n for n, uid in enumerate(payload["order"])}
            reordered = sorted(items, key=lambda i: rank.get(i["uid"], len(rank)))
            if reordered != items:
                items[:] = reordered
                changed = True
        if changed:
            current["revision"] += 1
        return {"revision": current["revision"], "items": [dict(i) for i in items]}

    def put_file(self, url, path, size, *, token=""):
        self.calls.append(("PUT", url))
        sha = url.rsplit("/", 1)[1]
        data = Path(path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha and len(data) == size
        self.photos[sha].update(stored=True, bytes=data)

    def download(self, url, destination, *, token=""):
        self.calls.append(("DOWNLOAD", url))
        Path(destination).write_bytes(self.photos[url.rsplit("/", 1)[1]]["bytes"])


def device_env(root, name):
    home = root / name
    (home / "Pictures").mkdir(parents=True)
    return {"HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "XDG_PICTURES_DIR": str(home / "Pictures")}


def sync(collection, env, hub, state, *, remote_changed=True):
    return sync_collection(collection, address="https://hub.test", token="t", http=hub, state=state,
                           scope=env["HOME"], environment=env, remote_changed=remote_changed)


def places(env):
    path = PLACES.path(env)
    return [p["name"] for p in json.loads(path.read_text())] if path.exists() else []


def write_places(env, rows):
    path = PLACES.path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"uid": uid, "name": name, "region": "", "country": "US", "latitude": lat,
                                 "longitude": lon, "timezone": "America/Chicago"} for uid, name, lat, lon in rows]))


with tempfile.TemporaryDirectory(prefix="luma-shared-sync-") as directory:
    root = Path(directory)
    hub = FakeHub()
    phone, desk = device_env(root, "phone"), device_env(root, "desk")
    phone_state, desk_state = {}, {}

    # --- a city added on the phone appears on the desk ---------------------
    write_places(phone, [("kc", "Kansas City", 39.0997, -94.5786)])
    sync(PLACES, phone, hub, phone_state)
    sync(PLACES, desk, hub, desk_state)
    assert places(desk) == ["Kansas City"], places(desk)

    # --- the desk adds Palo Alto and the same Kansas City under its own id --
    write_places(desk, [("kc", "Kansas City", 39.0997, -94.5786), ("pa", "Palo Alto", 37.4419, -122.143)])
    sync(PLACES, desk, hub, desk_state)
    sync(PLACES, phone, hub, phone_state)
    assert places(phone) == ["Kansas City", "Palo Alto"]
    fresh = device_env(root, "tablet")
    write_places(fresh, [("kc-tablet", "Kansas City", 39.09971, -94.57861)])
    sync(PLACES, fresh, hub, {})
    assert places(fresh) == ["Kansas City", "Palo Alto"], "a duplicate city is kept once"
    assert json.loads(PLACES.path(fresh).read_text())[0]["uid"] == "kc", "the device adopts the shared id"

    # --- removal and reorder travel; nothing is sent when nothing changed ---
    write_places(phone, [("pa", "Palo Alto", 37.4419, -122.143)])
    sync(PLACES, phone, hub, phone_state)
    sync(PLACES, desk, hub, desk_state)
    assert places(desk) == ["Palo Alto"]
    hub.calls.clear()
    assert sync(PLACES, desk, hub, desk_state, remote_changed=False).endswith("unchanged")
    assert hub.calls == []

    # --- an app write during sync is never overwritten ---------------------
    write_places(phone, [("pa", "Palo Alto", 37.4419, -122.143), ("sf", "San Francisco", 37.77, -122.42)])
    original = PLACES.write

    def racing_write(path, items, expected):
        write_places(desk, [("pa", "Palo Alto", 37.4419, -122.143), ("ny", "New York", 40.71, -74.0)])
        return original(path, items, expected)

    sync(PLACES, phone, hub, phone_state)
    object.__setattr__(PLACES, "write", racing_write)
    try:
        assert "retry" in sync(PLACES, desk, hub, desk_state)
    finally:
        object.__setattr__(PLACES, "write", original)
    assert places(desk) == ["Palo Alto", "New York"], "the app's edit survives"
    sync(PLACES, desk, hub, desk_state)
    assert places(desk) == ["Palo Alto", "San Francisco", "New York"] or places(desk) == ["Palo Alto", "New York", "San Francisco"], places(desk)
    sync(PLACES, phone, hub, phone_state)
    assert sorted(places(phone)) == ["New York", "Palo Alto", "San Francisco"]

    # --- clocks replace only the world list; alarms stay local --------------
    clock_path = CLOCKS.path(desk)
    clock_path.parent.mkdir(parents=True, exist_ok=True)
    clock_path.write_text(json.dumps({"world": [], "alarms": [{"uid": "a1", "hour": 6, "minute": 30}], "timers": []}))
    phone_clock = CLOCKS.path(phone)
    phone_clock.parent.mkdir(parents=True, exist_ok=True)
    phone_clock.write_text(json.dumps({"world": [{"uid": "c1", "label": "Kansas City", "zone": "America/Chicago"}], "alarms": [], "timers": []}))
    sync(CLOCKS, phone, hub, phone_state)
    sync(CLOCKS, desk, hub, desk_state)
    desk_clock = json.loads(clock_path.read_text())
    assert [c["label"] for c in desk_clock["world"]] == ["Kansas City"]
    assert desk_clock["alarms"] == [{"uid": "a1", "hour": 6, "minute": 30}], "alarms never sync"
    assert not any("alarm" in url for _m, url in hub.calls)

    assert changes_since([("a", {"x": 1}), ("b", {"x": 2})], [("b", {"x": 2}), ("a", {"x": 1})]) == ([], ["b", "a"])

    # --- photos: upload once, download elsewhere, never re-download deletes --
    picture = b"\x89PNG\r\n\x1a\n" + os.urandom(3000)
    (Path(phone["XDG_PICTURES_DIR"]) / "IMG_0001.png").write_bytes(picture)
    (Path(phone["XDG_PICTURES_DIR"]) / "notes.txt").write_text("not a photo")

    def photos(env, state, remote_changed=True):
        return sync_photos(address="https://hub.test", token="t", device_id=env["HOME"], http=hub, state=state,
                           scope=env["HOME"], environment=env, remote_changed=remote_changed)

    hub.calls.clear()
    assert "uploaded 1" in photos(phone, phone_state)
    assert "uploaded 0" in photos(phone, phone_state, remote_changed=False)
    assert [m for m, _u in hub.calls].count("PUT") == 1, "bytes are sent once"
    assert "downloaded 1" in photos(desk, desk_state)
    copy = Path(desk["XDG_PICTURES_DIR"]) / "Luma Hub" / "IMG_0001.png"
    assert copy.read_bytes() == picture
    photos(desk, desk_state)  # sends the manifest that now includes the copy
    copy.unlink()
    assert "downloaded 0" in photos(desk, desk_state), "a photo you deleted is not brought back"

    (Path(phone["XDG_PICTURES_DIR"]) / "IMG_0002.png").write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(500))
    (Path(phone["XDG_PICTURES_DIR"]) / "IMG_0001.png").unlink()
    assert "downloaded 0" in photos(phone, phone_state), "a photo deleted where it was taken does not come back"
    shutil.rmtree(phone["XDG_PICTURES_DIR"])
    try:
        photos(phone, phone_state)
    except PhotoSyncError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("a missing Pictures library was synced as empty")
    assert any(hub.holders.values()), "the hub still lists the phone's remaining photo"

print("Connect shared sync: list merge across devices, duplicate cities, removal and reorder, "
      "no-op without network, app writes preserved, alarms kept local, photo upload once, "
      "download, deleted copies stay deleted, missing library refused PASS")
