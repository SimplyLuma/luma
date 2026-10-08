# SPDX-License-Identifier: Apache-2.0
import json
import socket
import threading
import time
import unittest

from gi.repository import GLib

from luma_media_bridge import kodi
from luma_media_bridge.mpris import NO_TRACK, State, Track


class Labels(unittest.TestCase):
    def test_skin_markup_is_removed_and_lines_split(self):
        label = "[COLORdodgerblue]NFL[COLORwhite] |[B][I] Denver Broncos at Kansas City Chiefs[/B][/I]\n  [COLORred]XYZ | [/COLOR]"
        self.assertEqual(kodi.split_label(label), ("NFL | Denver Broncos at Kansas City Chiefs", "XYZ"))
        self.assertEqual(kodi.clean("[B]Song[/B]"), "Song")

    def test_times_round_trip(self):
        value = {"hours": 1, "minutes": 2, "seconds": 3, "milliseconds": 456}
        self.assertEqual(kodi.to_microseconds(value), 3_723_456_000)
        self.assertEqual(kodi.from_microseconds(3_723_456_000), value)

    def test_image_urls_unwrap(self):
        self.assertEqual(kodi.image_url("image://https%3a%2f%2fexample.com%2fthumb.jpg/"), "https://example.com/thumb.jpg")

    def test_episodes_name_the_show(self):
        track = kodi.track_from({"title": "Pilot", "showtitle": "The Show", "season": 1, "episode": 2, "file": "x"},
                                {"totaltime": {"hours": 0, "minutes": 42, "seconds": 0, "milliseconds": 0}})
        self.assertEqual((track.title, track.artists, track.album, track.length_us),
                         ("Pilot", ["The Show"], "Season 1, episode 2", 42 * 60 * 1_000_000))

    def test_metadata_is_mpris(self):
        self.assertEqual(set(Track().metadata()), {"mpris:trackid"})
        metadata = Track(track_id="/org/projectluma/MediaBridge/kodi/1", title="T", artists=["A"],
                         length_us=5, art_url="file:///x.jpg").metadata()
        self.assertEqual(metadata["xesam:title"].unpack(), "T")
        self.assertEqual(metadata["mpris:artUrl"].unpack(), "file:///x.jpg")

    def test_position_advances_only_while_playing(self):
        state = State(status="Playing", track=Track(track_id="/t", length_us=10_000_000), position_us=1_000_000,
                      measured_at=time.monotonic() - 2)
        self.assertGreaterEqual(state.position_now(), 3_000_000)
        state.status = "Paused"
        self.assertEqual(state.position_now(), 1_000_000)


class FakeKodi:
    """Just enough of Kodi's JSON-RPC server: one player, notifications, commands."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.commands = []
        self.client = None
        self.lock = threading.Lock()
        threading.Thread(target=self._serve, daemon=True).start()

    def send(self, message):
        # Kodi writes messages back to back with no separator, sometimes split.
        data = json.dumps(message).encode()
        with self.lock:
            self.client.sendall(data[:7])
            time.sleep(0.01)
            self.client.sendall(data[7:])

    def _serve(self):
        self.client, _ = self.server.accept()
        decoder, buffer = json.JSONDecoder(), ""
        while True:
            chunk = self.client.recv(65536)
            if not chunk:
                return
            buffer += chunk.decode()
            while buffer:
                try:
                    request, end = decoder.raw_decode(buffer)
                except ValueError:
                    break
                buffer = buffer[end:]
                self.commands.append(request["method"])
                result = {
                    "Player.GetActivePlayers": [{"playerid": 1, "type": "video"}],
                    "Player.GetProperties": {"speed": 1, "time": {"minutes": 1}, "totaltime": {"hours": 1},
                                             "canseek": True, "playlistid": -1},
                    "Player.GetItem": {"item": {"label": "[B]Match[/B]\nChannel", "title": "[B]Match[/B]\nChannel"}},
                    "Textures.GetTextures": {"textures": []},
                }.get(request["method"], "OK")
                self.send({"id": request["id"], "jsonrpc": "2.0", "result": result})


class Adapter(unittest.TestCase):
    def test_follows_playback_and_sends_commands(self):
        fake = FakeKodi()
        kodi.PORT = fake.port
        present, states = [], []
        adapter = kodi.Kodi(present.append, lambda state, seeked: states.append(state))
        adapter.start()
        self.addCleanup(adapter.shutdown)
        context = GLib.MainContext.default()

        def wait(predicate, what):
            end = time.monotonic() + 5
            while not predicate():
                context.iteration(False)
                if time.monotonic() > end:
                    self.fail("timed out: " + what)
                time.sleep(0.01)
        wait(lambda: present == [True], "connected")
        wait(lambda: states and states[-1].track.title == "Match", "playing item")
        self.assertEqual(states[-1].status, "Playing")
        self.assertEqual(states[-1].track.artists, ["Channel"])
        adapter.play_pause()
        wait(lambda: "Player.PlayPause" in fake.commands, "play/pause sent")
        fake.send({"jsonrpc": "2.0", "method": "Player.OnStop", "params": {"data": {}}})
        wait(lambda: states[-1].track.track_id == NO_TRACK, "stopped")


if __name__ == "__main__":
    unittest.main()
