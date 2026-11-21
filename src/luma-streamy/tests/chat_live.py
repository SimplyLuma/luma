# SPDX-License-Identifier: Apache-2.0
"""Read real chat for a few seconds (network; not run in CI).

    python3 tests/chat_live.py SECONDS LINK [LINK ...]

Prints each source's state changes and how many messages arrived; exit status 1 when no source
connected.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gi.repository import GLib  # noqa: E402

from luma_streamy.chat import Connection, Feed, parse_link  # noqa: E402

seconds, links = int(sys.argv[1]), sys.argv[2:]
feed = Feed()
seen: dict[str, int] = {}
states: dict[str, list] = {}


def heard(message):
    if message is None:
        for key, (state, words) in feed.states.items():
            if not states.get(key) or states[key][-1] != (state, words):
                states.setdefault(key, []).append((state, words))
                print(f"{key}: {state} {words}", flush=True)
        return
    key = f"{message.platform}:{message.channel}"
    seen[key] = seen.get(key, 0) + 1
    if seen[key] <= 3:
        print(f"{key} <{message.author}> {message.text[:60]}", flush=True)


feed.listeners.append(heard)
connections = [Connection(parse_link(link), feed) for link in links]
for connection in connections:
    connection.open()
loop = GLib.MainLoop()
GLib.timeout_add_seconds(seconds, loop.quit)
loop.run()
for connection in connections:
    connection.close()
print("messages:", seen)
sys.exit(0 if any(s and s[-2:][0][0] == "live" for s in states.values()) or seen else 1)
