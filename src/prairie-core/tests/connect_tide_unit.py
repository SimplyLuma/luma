#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Tide sources: only network sources travel, passwords go to the keyring
under Tide's schema, and library writes go through Tide's model."""
import sqlite3
import tempfile
from pathlib import Path

import prairie_apps.connect_tide as tide

with tempfile.TemporaryDirectory() as directory:
    database = Path(directory) / "library.db"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT, kind TEXT, uri TEXT UNIQUE, local INTEGER, capabilities TEXT, state TEXT, auth_ref TEXT, created_at INTEGER, last_seen INTEGER, account TEXT)")
    db.execute("INSERT INTO sources VALUES('local-1','This device','local-folder','file:///home/n/Music',1,'browse,play','online',NULL,0,0,NULL)")
    db.commit()
    model_calls, keyring = [], {}

    def model(path, code, request):
        model_calls.append(request)
        if request["op"] == "add":
            item = request["item"]
            with sqlite3.connect(path) as c:
                c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,0,?,'online',?,0,0,?)",
                          (item["id"], item["name"], item["kind"], item["uri"], ",".join(item["capabilities"]), item["auth_ref"], item["account"]))
        else:
            with sqlite3.connect(path) as c:
                c.execute("DELETE FROM sources WHERE id=?", (request["id"],))

    tide._model = model
    tide.keyring_store = lambda reference, label, secret: keyring.__setitem__(reference, secret)
    tide.keyring_clear = lambda reference: keyring.pop(reference, None)
    pulled = {}
    collection = tide.tide_collection(database, "", fetch_secret=lambda uid: "hunter2", on_pulled=lambda uid, s: pulled.__setitem__(uid, s))
    items, raw = collection.read(database)
    assert items == [], "a local folder never syncs"
    navidrome = ("abc", {"name": "Navidrome", "kind": "subsonic", "uri": "https://music.example", "account": "nick", "capabilities": ["play", "stream"]})
    assert collection.write(database, [navidrome], raw)
    assert keyring == {"secret-service:abc": "hunter2"} and pulled == {"abc": "hunter2"}
    assert model_calls[-1]["op"] == "add" and model_calls[-1]["item"]["auth_ref"] == "secret-service:abc"
    items, raw = collection.read(database)
    assert items == [navidrome]
    assert not collection.write(database, [], b"stale"), "a library changed since reading is never written"
    assert collection.write(database, [], raw)
    assert model_calls[-1] == {"op": "remove", "id": "abc"} and keyring == {}
print("Connect Tide: local folders stay local, network source added through Tide's model with its password in the keyring, removal clears both, stale writes refused PASS")
