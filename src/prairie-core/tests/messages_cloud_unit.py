# SPDX-License-Identifier: Apache-2.0
"""Luma Cloud history is shown alongside, never written into, the local message store."""
from __future__ import annotations

import tempfile
from pathlib import Path

from prairie_apps import connect_messages
from prairie_apps.messages_backend import MessageStore
from prairie_apps.messages_cloud import CLOUD_LABEL, CloudHistory, is_cloud, merge_thread, merge_threads


def row(hub_id, address, body, seconds, direction="in", read=False):
    return {"id": hub_id, "device_id": "phone", "thread_id": "t", "direction": direction, "sent_at": seconds * 1000,
            "read": read, "addresses": [address], "body": body, "attachments": []}


with tempfile.TemporaryDirectory() as root:
    store = MessageStore(Path(root) / "messages.db")
    mine = store.add("+15551230001", "hello from here", direction="outgoing", state="sent", timestamp=1000, uid="local-1")
    store.add("+15551230001", "same text, other id", direction="incoming", state="read", timestamp=1100, uid="phone-copy")
    before = store.thread("+15551230001")
    rows = [
        row("local-1", "+15551230001", "hello from here", 1000, "out"),          # same stable id
        row("hub-dup", "+15551230001", "same text, other id", 1101),              # fingerprint within 2 s
        row("hub-new", "+15551230001", "only on the phone", 1200),
        row("hub-other", "(555) 123-0002", "cloud-only thread", 1300),
        row("hub-group", "+15551230003", "group", 1400) | {"addresses": ["+15551230003", "+15551230004"]},
        row("hub-bad", "unknown", "no dialable address", 1500),
    ]
    cloud = CloudHistory(reader=lambda: rows).by_address(store.canonical_address)
    assert set(cloud) == {"+15551230001", store.canonical_address("5551230002")}, cloud

    merged = merge_thread(store.thread("+15551230001"), cloud["+15551230001"])
    assert [m.uid for m in merged] == ["local-1", "phone-copy", "cloud:hub-new"], merged
    assert merged[0] is not None and merged[0].send_status != "cloud" and is_cloud(merged[2])
    assert merged[2].direction == "incoming" and merged[2].state == "received" and merged[2].timestamp == 1200
    assert merge_thread(before, ()) is before

    threads = merge_threads(store.threads(), cloud, store.thread)
    assert [t.address for t in threads][:2] == [store.canonical_address("5551230002"), "+15551230001"], threads
    assert threads[0].preview == f"{CLOUD_LABEL} · cloud-only thread" and threads[0].unread == 1
    assert threads[1].preview == "only on the phone" and threads[1].updated == 1200
    assert [t.address for t in merge_threads(store.threads("zzz"), cloud, store.thread, "zzz")] == []
    assert [t.address for t in merge_threads((), cloud, store.thread, "cloud-only")] == [store.canonical_address("5551230002")]

    # Once every cloud row is already local, the list is exactly the local one.
    covered = {"+15551230001": tuple(m for m in cloud["+15551230001"] if m.uid != "cloud:hub-new")}
    assert merge_threads(store.threads(), covered, store.thread) == store.threads()

    # Nothing was written to the local store.
    assert store.thread("+15551230001") == before
    assert store.thread(store.canonical_address("5551230002")) == ()

    # End to end through the real cache file; re-read only when it changes.
    environment = {"XDG_DATA_HOME": str(Path(root) / "data"), "HOME": root}
    history = CloudHistory(environment)
    assert history.by_address() == {}
    connection = connect_messages._open_cache(environment, "scope-a")
    connect_messages._apply(connection, "messages", row("hub-file", "+15551230009", "from the file", 2000) | {"deleted": False})
    connection.commit(); connection.close()
    first = history.by_address()
    assert [m.uid for m in first["+15551230009"]] == ["cloud:hub-file"], first
    assert history.by_address() is first
    connect_messages.forget_cloud_history("all", environment)
    assert history.by_address() == {}

print("messages cloud history tests PASS")

from prairie_apps.messages_cloud import cloud_call_records, merge_calls
from prairie_apps.phone_backend import CallRecord

local_calls = (CallRecord("call-1", "+15551230001", "outgoing", 5000, 60, "completed"),)
cloud_calls = cloud_call_records([
    {"id": "call-1", "number": "+15551230001", "direction": "out", "started_at": 5000, "duration_s": 60, "answered": True},
    {"id": "hub-near", "number": "5551230001", "direction": "out", "started_at": 5001, "duration_s": 60, "answered": True},
    {"id": "hub-missed", "number": "+15551230002", "direction": "missed", "started_at": 6000, "duration_s": 0, "answered": False},
    {"id": "hub-in", "number": "+15551230003", "direction": "in", "started_at": 4000, "duration_s": 30, "answered": True},
    {"id": "hub-bad", "number": "unknown", "direction": "in", "started_at": 4500, "duration_s": 1, "answered": True},
])
merged_calls = merge_calls(local_calls, cloud_calls)
assert [(c.uid, c.direction, c.outcome) for c in merged_calls] == [
    ("cloud:hub-missed", "incoming", "missed"), ("call-1", "outgoing", "completed"), ("cloud:hub-in", "incoming", "completed")], merged_calls
assert merge_calls(local_calls, ()) is local_calls
print("phone cloud call history tests PASS")
