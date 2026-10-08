# SPDX-License-Identifier: Apache-2.0
"""Messages and call history logs: off by default, mapped, incremental, tombstoned, batched."""

import json
import re
import sqlite3
import tempfile
from pathlib import Path

from prairie_apps import connect_messages as cm

HUB_ID = re.compile(r"^[A-Za-z0-9_.:@+-]{1,128}$")
HUB_ADDRESS = re.compile(r"^[A-Za-z0-9+@._ ()-]{1,64}$")
HUB_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class FakeHub:
    def __init__(self, fail_on=None, status=None):
        self.posts = []
        self.fail_on = fail_on
        self.status = status

    def post_json(self, url, payload, *, token=""):
        if self.status is not None:
            from prairie_apps.connect_sync import HubResponseError
            raise HubResponseError(self.status, "synthetic")
        if self.fail_on is not None and len(self.posts) == self.fail_on:
            raise cm.MessageSyncError("synthetic network failure")
        assert token == "tok"
        self.posts.append((url, json.loads(json.dumps(payload))))
        return {"accepted": len(payload["items"]), "changed": len(payload["items"]), "cursor": len(self.posts)}

    def get_json(self, url, *, token=""):
        if self.status is not None:
            from prairie_apps.connect_sync import HubResponseError
            raise HubResponseError(self.status, "synthetic")
        self.gets = getattr(self, "gets", []) + [url]
        pages = getattr(self, "pages", None)
        return pages.pop(0) if pages else {"items": [], "cursor": 0, "more": False}


def hub_valid(kind, payload):
    assert re.fullmatch(r"[0-9a-f-]{36}", payload["device_id"])
    assert len(payload["items"]) <= 1000
    for item in payload["items"]:
        assert HUB_ID.match(item["id"]), item["id"]
        if kind == "messages":
            assert HUB_ID.match(item["thread_id"])
            assert item["direction"] in ("in", "out")
            assert 1 <= len(item["addresses"]) <= 50 and all(HUB_ADDRESS.match(a) for a in item["addresses"])
            assert len(item["body"]) <= 65536 and not HUB_CONTROL.search(item["body"])
            assert isinstance(item["sent_at"], int) and item["sent_at"] >= 0
        else:
            assert item["direction"] in ("in", "out", "missed")
            assert HUB_ADDRESS.match(item["number"])
            assert isinstance(item["started_at"], int) and 0 <= item["duration_s"] <= 604800


def make_stores(root: Path):
    messages = root / "share/prairie/messages/messages.db"
    calls = root / "share/prairie/phone/calls.db"
    messages.parent.mkdir(parents=True)
    calls.parent.mkdir(parents=True)
    m = sqlite3.connect(messages)
    m.execute("CREATE TABLE messages(uid TEXT PRIMARY KEY,address TEXT NOT NULL,body TEXT NOT NULL,timestamp INTEGER NOT NULL,direction TEXT NOT NULL,state TEXT NOT NULL,transport_id TEXT)")
    m.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,NULL)", [
        ("inline:" + "a" * 64, "+15555550100", "hello\x07 there", 1789263390, "incoming", "received"),
        ("503c3200dc9b4490a4fd6def", "+15555550100", "hi", 1789263400, "outgoing", "sent"),
        ("weird uid/with slash", "Carrier (Info)!", "read me", 1789263500, "incoming", "read"),
    ])
    m.commit(); m.close()
    c = sqlite3.connect(calls)
    c.execute("CREATE TABLE calls(uid TEXT PRIMARY KEY,address TEXT NOT NULL,direction TEXT NOT NULL,started INTEGER NOT NULL,duration INTEGER NOT NULL,outcome TEXT NOT NULL)")
    c.executemany("INSERT INTO calls VALUES(?,?,?,?,?,?)", [
        ("c1", "+15555550100", "incoming", 1789264853, 39, "completed"),
        ("c2", "+15555550101", "incoming", 1789264900, 0, "missed"),
        ("c3", "", "outgoing", 1789264950, 0, "failed"),
    ])
    c.commit(); c.close()
    return messages, calls


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        environment = {"XDG_DATA_HOME": str(root / "share"), "HOME": str(root)}
        data = root / "connect"
        data.mkdir()
        messages_db, calls_db = make_stores(root)
        kwargs = dict(address="https://hub.example", token="tok", device_id="ab317fcc-9246-4083-8598-83a485bbc72a",
                      scope="https://hub.example|dev", data_directory=data, environment=environment)

        # Off unless explicitly switched on; nothing is read into a request.
        hub, state = FakeHub(), {}
        assert cm.sync_messages_and_calls(http=hub, state=state, **kwargs) == ["messages: off", "calls: off"]
        (data / "services.json").write_text(json.dumps({"notes": True}))
        assert cm.sync_messages_and_calls(http=hub, state=state, **kwargs) == ["messages: off", "calls: off"]
        assert hub.posts == [] and state == {}

        (data / "services.json").write_text(json.dumps({"messages": True, "calls": True}))
        lines = cm.sync_messages_and_calls(http=hub, state=state, dry_run=True, **kwargs)
        assert hub.posts == [] and all("nothing sent" in line for line in lines), lines

        lines = cm.sync_messages_and_calls(http=hub, state=state, **kwargs)
        assert lines == ["messages: sent 3, deleted 0, unchanged 0", "calls: sent 3, deleted 0, unchanged 0"], lines
        (murl, mpayload), (curl, cpayload) = hub.posts
        assert murl.endswith("/api/hub/sync/messages") and curl.endswith("/api/hub/sync/calls")
        hub_valid("messages", mpayload); hub_valid("calls", cpayload)
        by_id = {i["id"]: i for i in mpayload["items"]}
        first = by_id["inline:" + "a" * 64]
        assert first["direction"] == "in" and first["read"] is False and first["body"] == "hello there"
        assert first["sent_at"] == 1789263390 * 1000
        assert by_id["503c3200dc9b4490a4fd6def"]["direction"] == "out" and by_id["503c3200dc9b4490a4fd6def"]["read"] is True
        hashed = [i for i in mpayload["items"] if i["id"].startswith("h:")]
        assert len(hashed) == 1 and hashed[0]["addresses"] == ["Carrier (Info)"] and hashed[0]["read"] is True
        # Same counterpart, same thread, on every device.
        assert first["thread_id"] == by_id["503c3200dc9b4490a4fd6def"]["thread_id"]
        calls = {i["id"]: i for i in cpayload["items"]}
        assert (calls["c1"]["direction"], calls["c1"]["answered"], calls["c1"]["duration_s"]) == ("in", True, 39)
        assert (calls["c2"]["direction"], calls["c2"]["answered"]) == ("missed", False)
        assert (calls["c3"]["direction"], calls["c3"]["number"]) == ("out", "unknown")

        # Nothing changed: nothing sent.
        hub.posts.clear()
        assert cm.sync_messages_and_calls(http=hub, state=state, **kwargs) == [
            "messages: sent 0, deleted 0, unchanged 3", "calls: sent 0, deleted 0, unchanged 3"]
        assert hub.posts == []

        # A read receipt changes one row; a local deletion becomes a content-free tombstone.
        m = sqlite3.connect(messages_db)
        m.execute("UPDATE messages SET state='read' WHERE uid=?", ("inline:" + "a" * 64,))
        m.execute("DELETE FROM messages WHERE uid='503c3200dc9b4490a4fd6def'")
        m.commit(); m.close()
        lines = cm.sync_messages_and_calls(http=hub, state=state, **kwargs)
        assert lines[0] == "messages: sent 1, deleted 1, unchanged 1", lines
        (_url, payload), = hub.posts
        hub_valid("messages", payload)
        tomb = [i for i in payload["items"] if i["deleted"]]
        assert len(tomb) == 1 and tomb[0]["id"] == "503c3200dc9b4490a4fd6def"
        assert tomb[0]["body"] == "" and tomb[0]["addresses"] == ["unknown"] and tomb[0]["sent_at"] == 1789263400 * 1000
        hub.posts.clear()
        assert cm.sync_messages_and_calls(http=hub, state=state, **kwargs)[0] == "messages: sent 0, deleted 0, unchanged 2"

        # Large history goes in 1000-item batches; a failed batch leaves earlier ones committed.
        m = sqlite3.connect(messages_db)
        m.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,NULL)",
                      [(f"bulk{n}", "+15555550199", f"m{n}", 1789000000 + n, "incoming", "read") for n in range(2500)])
        m.commit(); m.close()
        failing, batch_state = FakeHub(fail_on=1), json.loads(json.dumps(state))
        try:
            cm.sync_log("messages", cm.message_items(messages_db), http=failing, state=batch_state,
                        address="https://hub.example", token="tok", device_id=kwargs["device_id"], scope=kwargs["scope"])
            raise AssertionError("expected the second batch to fail")
        except cm.MessageSyncError:
            pass
        assert len(failing.posts) == 1 and len(failing.posts[0][1]["items"]) == 1000
        retry = FakeHub()
        line = cm.sync_log("messages", cm.message_items(messages_db), http=retry, state=batch_state,
                           address="https://hub.example", token="tok", device_id=kwargs["device_id"], scope=kwargs["scope"])
        assert line == "messages: sent 1500, deleted 0, unchanged 1002", line
        assert [len(p["items"]) for _u, p in retry.posts] == [1000, 500]
        for _u, p in retry.posts:
            hub_valid("messages", p)

        # A missing store sends nothing and never tombstones history already sent.
        calls_db.unlink()
        guarded = FakeHub()
        try:
            cm.sync_messages_and_calls(http=guarded, state=state, **kwargs)
            raise AssertionError("expected a refusal for the vanished calls store")
        except cm.MessageSyncError as error:
            assert "calls" in str(error)
        assert not any(p["items"] and any(i["deleted"] for i in p["items"]) for u, p in guarded.posts if u.endswith("/calls"))
        fresh = FakeHub()
        assert cm.sync_log("calls", cm.call_items(calls_db), http=fresh, state={}, address="https://hub.example",
                           token="tok", device_id=kwargs["device_id"], scope="s") == "calls: sent 0, deleted 0, unchanged 0"
    # The Hub refusing with 409 means its switch for this device is off: skip quietly, keep state.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_stores(root)
        gated, gate_state = FakeHub(status=409), {}
        line = cm.sync_log("calls", cm.call_items(root / "share/prairie/phone/calls.db"), http=gated, state=gate_state,
                           address="https://hub.example", token="tok", device_id="ab317fcc-9246-4083-8598-83a485bbc72a", scope="s")
        assert line == "calls: off on the hub" and gate_state == {}, (line, gate_state)
        assert issubclass(cm.MessageSyncError, __import__("prairie_apps.connect_sync", fromlist=["ConnectError"]).ConnectError)
    # The connect_sync entry points: same switch, same guarded behaviour.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); data = root / "connect"; data.mkdir()
        environment = {"XDG_DATA_HOME": str(root / "share"), "HOME": str(root)}
        make_stores(root)
        common = dict(address="https://hub.example", token="tok", scope="s", environment=environment,
                      remote_changed=False, device_id="ab317fcc-9246-4083-8598-83a485bbc72a", data_directory=data)
        hub = FakeHub()
        assert cm.sync_messages(http=hub, state={}, **common) == "messages: off" and hub.posts == []
        (data / "services.json").write_text(json.dumps({"calls": True}))
        assert cm.sync_messages(http=hub, state={}, **common) == "messages: off"
        assert cm.sync_calls(http=hub, state={}, **common) == "calls: sent 3, deleted 0, unchanged 0, received 0"
        assert len(hub.posts) == 1 and hub.posts[0][0].endswith("/calls")
    # Receiving: other devices' rows land in the read-only cache, never the local stores.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); environment = {"XDG_DATA_HOME": str(root / "share"), "HOME": str(root)}
        me, other = "ab317fcc-9246-4083-8598-83a485bbc72a", "11111111-2222-3333-4444-555555555555"
        messages_db, calls_db = make_stores(root)
        before_local = messages_db.read_bytes()
        hub = FakeHub()
        row = lambda i, **kw: {"id": i, "device_id": other, "thread_id": "t:x", "direction": "in", "sent_at": 1000 + len(i),
                               "read": False, "deleted": False, "addresses": ["+15555550100"], "body": "cloud " + i,
                               "attachments": [], "updated_at": 1, "seq": 1, **kw}
        hub.pages = [
            {"items": [row("a"), row("b"), {**row("own"), "device_id": me}], "cursor": 3, "more": True},
            {"items": [row("c", read=True)], "cursor": 4, "more": False},
        ]
        line = cm.receive_log("messages", address="https://hub.example", token="tok", device_id=me, http=hub,
                              scope="scope-A", environment=environment)
        assert line == "received 3", line
        assert hub.gets[0].endswith("after=0&limit=1000") and hub.gets[1].endswith("after=3&limit=1000")
        cache = cm.cloud_history_path(environment)
        assert (cache.stat().st_mode & 0o777) == 0o600
        assert sorted(m["id"] for m in cm.cloud_messages(environment)) == ["a", "b", "c"]
        assert messages_db.read_bytes() == before_local  # the device's own store is untouched
        # Resume from the stored cursor; read only moves forward; deletes are sticky and drop content.
        hub.gets = []
        hub.pages = [{"items": [row("a", read=True), row("b", deleted=True), row("c", read=False)], "cursor": 7, "more": False}]
        cm.receive_log("messages", address="https://hub.example", token="tok", device_id=me, http=hub, scope="scope-A", environment=environment)
        assert hub.gets[0].endswith("after=4&limit=1000")
        view = {m["id"]: m for m in cm.cloud_messages(environment)}
        assert set(view) == {"a", "c"} and view["a"]["read"] is True and view["c"]["read"] is True
        hub.pages = [{"items": [row("b")], "cursor": 8, "more": False}]
        cm.receive_log("messages", address="https://hub.example", token="tok", device_id=me, http=hub, scope="scope-A", environment=environment)
        assert "b" not in {m["id"] for m in cm.cloud_messages(environment)}
        raw = sqlite3.connect(cache).execute("SELECT deleted, body, addresses FROM messages WHERE id='b'").fetchone()
        assert raw == (1, "", "[]"), raw
        # Calls too; then another account/hub never sees the old cache.
        hub.pages = [{"items": [{"id": "k1", "device_id": other, "direction": "missed", "started_at": 5, "duration_s": 0,
                                 "answered": False, "deleted": False, "number": "+15555550101", "contact_uid": ""}], "cursor": 1, "more": False}]
        assert cm.receive_log("calls", address="https://hub.example", token="tok", device_id=me, http=hub, scope="scope-A",
                              environment=environment) == "received 1"
        assert [c["id"] for c in cm.cloud_calls(environment)] == ["k1"]
        hub.pages = []
        cm.receive_log("messages", address="https://hub.example", token="tok", device_id=me, http=hub, scope="scope-B", environment=environment)
        assert cm.cloud_messages(environment) == [] and cm.cloud_calls(environment) == []
        # 409 on receive is quiet; switching off removes only the cache.
        assert cm.receive_log("calls", address="https://hub.example", token="tok", device_id=me, http=FakeHub(status=409),
                              scope="scope-B", environment=environment) == "calls: off on the hub"
        hub.pages = [{"items": [row("z")], "cursor": 1, "more": False}]
        cm.receive_log("messages", address="https://hub.example", token="tok", device_id=me, http=hub, scope="scope-B", environment=environment)
        cm.forget_cloud_history("calls", environment)
        assert [m["id"] for m in cm.cloud_messages(environment)] == ["z"] and cache.exists()
        cm.forget_cloud_history("messages", environment)
        assert not cache.exists() and messages_db.read_bytes() == before_local and calls_db.exists()
    print("Connect messages/calls: off by default, Hub-valid mapping, incremental, tombstones, batching, retry PASS")


if __name__ == "__main__":
    main()
