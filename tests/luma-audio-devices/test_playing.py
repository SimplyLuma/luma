# SPDX-License-Identifier: MPL-2.0
"""The playing list: one row per app, how long it stays, and what it plays.

Covers grouping streams by app, a row leaving LINGER_SECONDS after the app's
last stream, an app without a media player, MPRIS matching, the other sounds
and what outputs are called."""

import unittest

import _paths  # noqa: F401
from luma_audio_devices import classify, mpris
from luma_audio_devices.graph import Graph
from luma_audio_devices.mixer import LINGER_SECONDS, Mixer

STREAM = "Stream/Output/Audio"


class _Control:
    app_audio_ready = True

    def __init__(self):
        self.commands = []

    def app_audio_command(self, key, value):
        self.commands.append((key, value))
        return True


class _Index:
    """A desktop index of three apps, one of them a browser."""

    ENTRIES = {
        "org.projectluma.Tide": ("Tide", "org.projectluma.Tide", False),
        "com.rhyme.viola": ("Viola", "com.rhyme.viola", True),
        "org.example.Game": ("Game", "org.example.Game", False),
    }

    def identify(self, key, props, client=None):
        from luma_audio_devices import apps
        if key == apps.SYSTEM_KEY:
            return apps.SYSTEM_IDENTITY
        binary = props.get("application.process.binary") or (client or {}).get("application.process.binary")
        for stem, (name, icon, _browser) in self.ENTRIES.items():
            if key == stem or (binary or "").rsplit("/", 1)[-1] == stem.rsplit(".", 1)[-1].lower():
                return apps.AppIdentity(key, name, icon, stem + ".desktop")
        return apps.AppIdentity(key, props.get("application.name") or key, apps.FALLBACK_ICON, "")

    def is_browser(self, desktop_id):
        entry = self.ENTRIES.get(desktop_id.removesuffix(".desktop"))
        return bool(entry and entry[2])


def stream(oid, props, state="running", volume=1.0):
    return {"id": oid, "type": "PipeWire:Interface:Node",
            "info": {"state": state, "props": dict(props, **{"media.class": STREAM, "object.serial": oid + 1000}),
                     "params": {"Props": [{"channelVolumes": [volume, volume], "mute": False}]}}}


def gone(oid):
    return {"id": oid, "info": None}


def listed(mixer):
    return [row for row in mixer.rows() if row["listed"]]


class Grouping(unittest.TestCase):
    def test_one_row_per_app_not_per_stream(self):
        graph = Graph()
        graph.apply([
            stream(40, {"application.id": "org.projectluma.Tide"}),
            stream(41, {"application.id": "org.projectluma.Tide"}, state="idle"),
            stream(42, {"application.id": "org.projectluma.Tide"}, state="suspended"),
            stream(43, {"pipewire.access.portal.app_id": "org.example.Game", "application.name": "SDL"}),
            stream(44, {"application.process.binary": "/usr/bin/viola"}),
            stream(45, {"application.process.binary": "/opt/viola/viola"}),
        ])
        mixer = Mixer(_Index(), _Control())
        mixer.update(graph, 0.0)
        rows = listed(mixer)
        self.assertEqual([(r["id"], r["name"], r["streams"]) for r in rows], [
            ("org.projectluma.Tide", "Tide", [40, 41, 42]),
            ("org.example.Game", "Game", [43]),
            ("viola", "Viola", [44, 45]),
        ])

    def test_a_corked_app_is_listed_and_not_playing(self):
        graph = Graph()
        graph.apply([stream(40, {"application.id": "org.projectluma.Tide"}, state="idle")])
        mixer = Mixer(_Index(), _Control())
        mixer.update(graph, 0.0)
        (row,) = listed(mixer)
        self.assertFalse(row["playing"])
        self.assertEqual(row["streams"], [40])

    def test_an_app_without_a_stream_is_never_listed(self):
        mixer = Mixer(_Index(), _Control())
        player = mpris.Player("org.mpris.MediaPlayer2.tide", desktop_entry="org.projectluma.Tide",
                              status="Paused", title="A song")
        mixer.update(Graph(), 0.0, [player])
        self.assertEqual(listed(mixer), [])
        # The other sounds are there, silent, and not in the playing list.
        self.assertEqual([r["id"] for r in mixer.rows() if r["other"]],
                         ["@system", "org.projectluma.Messages", "org.projectluma.Phone"])

    def test_rows_keep_the_order_they_started_in(self):
        graph = Graph()
        mixer = Mixer(_Index(), _Control())
        graph.apply([stream(50, {"application.name": "Zeta"})])
        mixer.update(graph, 0.0)
        graph.apply([stream(40, {"application.name": "Alpha"})])
        mixer.update(graph, 1.0)
        self.assertEqual([r["id"] for r in listed(mixer)], ["Zeta", "Alpha"])


class Linger(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.mixer = Mixer(_Index(), _Control())
        self.graph.apply([stream(40, {"application.id": "org.projectluma.Tide"}, volume=0.125),
                          stream(41, {"application.name": "Other"})])
        self.mixer.update(self.graph, 100.0)

    def test_row_stays_ten_seconds_after_the_last_stream(self):
        self.assertEqual(LINGER_SECONDS, 10.0)
        self.assertIsNone(self.mixer.next_deadline())
        self.graph.apply([gone(40)])
        self.assertTrue(self.mixer.update(self.graph, 105.0))
        (tide, _other) = listed(self.mixer)
        self.assertEqual((tide["id"], tide["streams"], tide["playing"]), ("org.projectluma.Tide", [], False))
        self.assertAlmostEqual(tide["volume"], 0.5, places=3)
        self.assertEqual(self.mixer.next_deadline(), 115.0)
        self.assertFalse(self.mixer.update(self.graph, 114.9))
        self.assertEqual(len(listed(self.mixer)), 2)
        self.assertTrue(self.mixer.update(self.graph, 115.0))
        self.assertEqual([r["id"] for r in listed(self.mixer)], ["Other"])
        self.assertIsNone(self.mixer.next_deadline())

    def test_a_closing_stream_without_a_volume_keeps_the_level(self):
        # pw-dump can show a stream once more, stripped of its Props, as it leaves.
        self.graph.apply([{"id": 40, "type": "PipeWire:Interface:Node", "info": {
            "state": "idle", "props": {"media.class": STREAM, "application.id": "org.projectluma.Tide",
                                       "object.serial": 1040}, "params": {"Props": []}}}])
        self.mixer.update(self.graph, 104.0)
        self.assertAlmostEqual(listed(self.mixer)[0]["volume"], 0.5, places=3)
        self.graph.apply([gone(40)])
        self.mixer.update(self.graph, 105.0)
        self.assertAlmostEqual(listed(self.mixer)[0]["volume"], 0.5, places=3)

    def test_a_lingering_row_shows_the_remembered_level(self):
        # Its last stream may report anything while it is torn down.
        self.graph.apply([stream(40, {"application.id": "org.projectluma.Tide"}, volume=1.0),
                          {"id": 9, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "luma-app-audio"},
                           "metadata": [{"subject": 0, "key": "app.state:org.projectluma.Tide",
                                         "value": '{"volume": 0.125, "mute": false}'}]}])
        self.mixer.update(self.graph, 104.0)
        self.graph.apply([gone(40)])
        self.mixer.update(self.graph, 105.0)
        self.assertAlmostEqual(listed(self.mixer)[0]["volume"], 0.5, places=3)
        self.assertIn("org.projectluma.Tide", self.mixer.saved_state_keys())
        self.assertEqual(self.mixer.saved_state_keys()[:2], ["org.projectluma.Messages", "org.projectluma.Phone"])

    def test_the_next_track_keeps_the_row(self):
        self.graph.apply([gone(40)])
        self.mixer.update(self.graph, 105.0)
        self.graph.apply([stream(44, {"application.id": "org.projectluma.Tide"})])
        self.mixer.update(self.graph, 107.0)
        self.assertIsNone(self.mixer.next_deadline())
        self.assertEqual([(r["id"], r["streams"]) for r in listed(self.mixer)],
                         [("org.projectluma.Tide", [44]), ("Other", [41])])
        # A later gap starts a new ten seconds, not the rest of the first.
        self.graph.apply([gone(44)])
        self.mixer.update(self.graph, 120.0)
        self.assertEqual(self.mixer.next_deadline(), 130.0)

    def test_one_stream_of_two_closing_does_not_start_the_clock(self):
        self.graph.apply([stream(42, {"application.id": "org.projectluma.Tide"})])
        self.mixer.update(self.graph, 101.0)
        self.graph.apply([gone(40)])
        self.mixer.update(self.graph, 102.0)
        self.assertIsNone(self.mixer.next_deadline())


class WithoutAPlayer(unittest.TestCase):
    def test_stream_without_mpris_is_the_app_itself(self):
        graph = Graph()
        graph.apply([stream(43, {"pipewire.access.portal.app_id": "org.example.Game"}, volume=0.125)])
        mixer = Mixer(_Index(), _Control())
        other = mpris.Player("org.mpris.MediaPlayer2.tide", desktop_entry="org.projectluma.Tide",
                             status="Playing", title="A song", can_pause=True)
        mixer.update(graph, 0.0, [other])
        (row,) = listed(mixer)
        self.assertEqual((row["name"], row["icon"]), ("Game", "org.example.Game"))
        self.assertEqual((row["player"], row["title"], row["artist"], row["art"], row["status"]),
                         ("", "", "", "", ""))
        self.assertFalse(row["can_play"] or row["can_pause"] or row["can_go_next"] or row["can_go_previous"])
        self.assertTrue(row["playing"])
        self.assertAlmostEqual(row["volume"], 0.5, places=3)
        self.assertEqual(mixer.player_of("org.example.Game"), "")


class Players(unittest.TestCase):
    def test_match_by_desktop_entry_then_bus_name_then_process(self):
        by_entry = mpris.Player("org.mpris.MediaPlayer2.Player", desktop_entry="org.projectluma.Tide")
        by_name = mpris.Player("org.mpris.MediaPlayer2.viola.instance4321")
        by_pid = mpris.Player("org.mpris.MediaPlayer2.x77", pid=900)
        players = [by_entry, by_name, by_pid]
        self.assertIs(mpris.match(players, key="org.projectluma.Tide", desktop_id="org.projectluma.Tide.desktop"),
                      by_entry)
        self.assertIs(mpris.match(players, key="viola", desktop_id="com.rhyme.viola.desktop", binary="viola"),
                      by_name)
        self.assertIs(mpris.match(players, key="Game", pids={900}), by_pid)
        self.assertIsNone(mpris.match(players, key="Nothing", desktop_id="org.none.desktop", pids={1}))

    def test_of_two_players_the_one_playing_wins(self):
        paused = mpris.Player("org.mpris.MediaPlayer2.viola.instance1", status="Paused", changed=9)
        playing = mpris.Player("org.mpris.MediaPlayer2.viola.instance2", status="Playing", changed=1)
        self.assertIs(mpris.match([paused, playing], key="viola"), playing)

    def test_properties(self):
        player = mpris.Player("org.mpris.MediaPlayer2.tide")
        self.assertTrue(mpris.apply_properties(player, mpris.PLAYER_INTERFACE, {
            "PlaybackStatus": "Playing", "CanPause": True, "CanGoNext": True,
            "Metadata": {"xesam:title": "God Only Knows", "xesam:artist": ["The Beach Boys"],
                         "mpris:artUrl": "file:///tmp/a.jpg"}}))
        self.assertFalse(mpris.apply_properties(player, mpris.PLAYER_INTERFACE, {"PlaybackStatus": "Playing"}))
        row = mpris.describe(player)
        self.assertEqual((row["title"], row["artist"], row["art"], row["status"]),
                         ("God Only Knows", "The Beach Boys", "file:///tmp/a.jpg", "playing"))
        self.assertEqual((row["can_pause"], row["can_go_next"], row["can_go_previous"]), (True, True, False))
        player.can_control = False
        self.assertFalse(mpris.describe(player)["can_pause"])

    def test_row_takes_title_artist_art_and_transport(self):
        graph = Graph()
        graph.apply([stream(40, {"application.id": "org.projectluma.Tide"}),
                     stream(44, {"application.process.binary": "viola"})])
        tide = mpris.Player("org.mpris.MediaPlayer2.tide", desktop_entry="org.projectluma.Tide",
                            status="Playing", title="God Only Knows", artist="The Beach Boys",
                            art_url="file:///a.jpg", can_pause=True, can_go_next=True, can_go_previous=True)
        viola = mpris.Player("org.mpris.MediaPlayer2.viola.instance7", status="Paused", title="Luma launch film",
                             url="https://www.simplyluma.com/film", can_play=True, position=842_000_000)
        mixer = Mixer(_Index(), _Control())
        mixer.update(graph, 0.0, [tide, viola])
        first, second = listed(mixer)
        self.assertEqual((first["title"], first["artist"], first["art"], first["player"]),
                         ("God Only Knows", "The Beach Boys", "file:///a.jpg", "org.mpris.MediaPlayer2.tide"))
        self.assertTrue(first["playing"] and first["can_pause"] and first["can_go_next"])
        # A browser without an artist is named by its site; a paused player
        # is paused though its stream still runs, and says where it stopped.
        self.assertEqual((second["title"], second["artist"]), ("Luma launch film", "simplyluma.com"))
        self.assertEqual((second["playing"], second["status"], second["position"]), (False, "paused", 842_000_000))
        self.assertEqual(mixer.player_of("viola"), "org.mpris.MediaPlayer2.viola.instance7")
        # A change of track is a change of the row.
        tide.title = "Sloop John B"
        self.assertTrue(mixer.update(graph, 1.0, [tide, viola]))

    def test_site_host(self):
        self.assertEqual(mpris.site_host("https://www.simplyluma.com/a?b"), "simplyluma.com")
        self.assertEqual(mpris.site_host("file:///home/a.mp3"), "")
        self.assertEqual(mpris.site_host(""), "")


class OutputNames(unittest.TestCase):
    def label(self, props, route=None, battery=None):
        return (classify.output_kind(props, route), classify.output_label(props, route),
                classify.output_detail(props, route, battery))

    def test_named_by_what_it_is(self):
        self.assertEqual(self.label({"device.api": "alsa", "device.bus": "pci", "node.description":
                                     "Built-in Audio Analog Stereo", "device.profile.description": "Analog Stereo"},
                                    {"port.type": "speaker"}), ("speakers", "Speakers", "Built in"))
        self.assertEqual(self.label({"device.api": "alsa", "device.bus": "pci",
                                     "device.profile.description": "Analog Output"}),
                         ("speakers", "Speakers", "Built in"))
        self.assertEqual(self.label({"device.api": "alsa", "device.bus": "pci"}, {"port.type": "headphones"}),
                         ("headphones", "Headphones", "Wired"))
        self.assertEqual(self.label({"device.api": "bluez5", "device.description": "WH-1000XM5",
                                     "device.form-factor": "headphone", "api.bluez5.address": "AA:BB"}, None, 80),
                         ("headphones", "WH-1000XM5", "Bluetooth · 80%"))
        self.assertEqual(self.label({"device.api": "bluez5", "device.description": "Kitchen speaker"}),
                         ("speakers", "Kitchen speaker", "Bluetooth"))
        self.assertEqual(self.label({"device.api": "alsa", "device.bus": "usb",
                                     "device.product.name": "Scarlett 2i2 USB"}), ("usb", "Scarlett 2i2", "USB"))
        self.assertEqual(self.label({"node.name": "luma_airplay.1", "luma.airplay.id": "1",
                                     "node.description": "Living room"}), ("airplay", "Living room", "AirPlay"))
        self.assertEqual(self.label({"device.api": "alsa", "device.bus": "pci", "node.nick": "HDMI 1"},
                                    {"port.type": "hdmi", "device.product.name": "Studio Display"}),
                         ("display", "Studio Display", "Display"))

    def test_never_a_card_or_profile_name(self):
        for props in (
            {"device.api": "alsa", "device.bus": "usb", "device.product.name": "QEMU USB Audio",
             "node.description": "QEMU USB Audio Analog Output", "device.profile.description": "Analog Output"},
            {"device.api": "alsa", "device.bus": "usb", "device.product.name": "USB Audio Device"},
            {"device.api": "alsa", "device.bus": "pci", "device.profile.description": "Digital Stereo (IEC958)"},
        ):
            label = classify.output_label(props, {})
            for word in ("Analog", "QEMU", "USB Audio", "IEC958", "Digital Stereo"):
                self.assertNotIn(word, label)

    def test_card_name_only_tells_same_named_outputs_apart(self):
        entries = [{"name": "a", "label": "Speakers", "detail": "Built in"},
                   {"name": "b", "label": "Speakers", "detail": "USB"},
                   {"name": "c", "label": "WH-1000XM5", "detail": "Bluetooth"}]
        classify.label_outputs(entries, {"a": "Analog Output", "b": "QEMU USB Audio", "c": "WH-1000XM5"})
        self.assertEqual([e["detail"] for e in entries],
                         ["Built in · Analog Output", "USB · QEMU USB Audio", "Bluetooth"])
        self.assertEqual([e["label"] for e in entries], ["Speakers", "Speakers", "WH-1000XM5"])


if __name__ == "__main__":
    unittest.main()
