# SPDX-License-Identifier: MPL-2.0
import copy
import json
import unittest

import _paths  # noqa: F401
from luma_audio_devices.classify import describe
from luma_audio_devices.graph import ChunkDecoder, Graph


def load_capture():
    return json.loads((_paths.FIXTURES / "thinkpad-x1-sof-graph.json").read_text(encoding="utf-8"))


class RealLaptopGraph(unittest.TestCase):
    """A ThinkPad X1 (Intel SOF, UCM split PCMs) with one display on HDMI 1,
    nothing in the headphone jack and two AirPlay receivers found by
    module-raop-discover. Personal names and addresses are replaced."""

    def setUp(self):
        self.capture = load_capture()
        self.graph = Graph()
        self.graph.apply(self.capture)

    def test_unplugged_ports_are_not_outputs(self):
        nodes = self.graph.output_nodes()
        usable = sorted(name for name, node in nodes.items() if node.available)
        self.assertIn("alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Speaker__sink", usable)
        self.assertIn("alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__HDMI1__sink", usable)
        for unplugged in ("Headphones", "HDMI2", "HDMI3"):
            name = f"alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__{unplugged}__sink"
            self.assertIn(name, nodes)
            self.assertFalse(nodes[name].available, unplugged)

    def test_display_is_named_after_the_monitor(self):
        node = self.graph.output_nodes()["alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__HDMI1__sink"]
        output = describe(node.props, node.route)
        self.assertEqual(output.name, "LG ULTRAWIDE")
        self.assertEqual(output.key, "display:pci-0000:00:1f.3-platform-sof_sdw:LG ULTRAWIDE")
        self.assertEqual(output.icon, "video-display-symbolic")
        self.assertTrue(output.local)

    def test_speaker_reads_as_speaker(self):
        node = self.graph.output_nodes()["alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Speaker__sink"]
        self.assertEqual(describe(node.props, node.route).name, "Speaker")

    def test_discovered_airplay_outputs_are_hidden(self):
        hidden = self.graph.hidden_output_names()
        self.assertEqual(len(hidden), 2)
        self.assertTrue(all(name.startswith("raop_sink.") for name in hidden))

    def test_default_from_metadata(self):
        self.assertEqual(self.graph.default_sink(),
                         "alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Speaker__sink")

    def test_headphones_plugged_in_becomes_available(self):
        device = next(o for o in self.capture if o["id"] == 71)
        update = copy.deepcopy(device)
        routes = update["info"]["params"]["EnumRoute"]
        headphones = next(r for r in routes if r["name"] == "[Out] Headphones")
        headphones["available"] = "yes"
        active = copy.deepcopy(headphones)
        active["device"] = 1
        update["info"]["params"] = {"EnumRoute": routes,
                                    "Route": device["info"]["params"]["Route"] + [active]}
        self.graph.apply([update])
        node = self.graph.output_nodes()["alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Headphones__sink"]
        self.assertTrue(node.available)
        self.assertTrue(describe(node.props, node.route).personal)

    def test_removal(self):
        self.graph.apply([{"id": 80, "info": None}])
        self.assertNotIn("alsa_output.pci-0000_00_1f.3-platform-sof_sdw.HiFi__HDMI1__sink",
                         self.graph.output_nodes())

    def test_metadata_deltas_merge(self):
        metadata = next(o for o in self.capture if o["type"].endswith("Metadata"))
        delta = {"id": metadata["id"], "type": metadata["type"], "props": metadata["props"],
                 "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
                               "value": {"name": "luma_airplay.aabbccddeeff"}}]}
        self.graph.apply([delta])
        self.assertEqual(self.graph.default_sink(), "luma_airplay.aabbccddeeff")
        self.assertEqual(self.graph.default_sink(configured=True),
                         "alsa_output.usb-Example_USB_Audio-00.analog-stereo")


class Decoder(unittest.TestCase):
    def test_split_documents_across_reads(self):
        decoder = ChunkDecoder()
        text = json.dumps([{"id": 1}]) + "\n" + json.dumps([{"id": 2, "info": None}])
        self.assertEqual(decoder.feed(text[:7]), [])
        documents = decoder.feed(text[7:])
        self.assertEqual(documents, [[{"id": 1}], [{"id": 2, "info": None}]])


if __name__ == "__main__":
    unittest.main()
