# SPDX-License-Identifier: Apache-2.0
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from luma_streamy import output_graph as og  # noqa: E402
from luma_streamy.output_graph import Preset, RowStats  # noqa: E402


class OutputGraphTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(og.kind_of("rtmps://live.twitch.tv/app/k"), "rtmp")
        self.assertEqual(og.kind_of("srt://h:9000?streamid=x"), "srt")
        self.assertEqual(og.kind_of("https://whip.example/endpoint"), "whip")
        with self.assertRaises(ValueError):
            og.kind_of("example.org")

    def test_stream_encoders_hardware_first(self):
        self.assertEqual(og.choose({"openh264enc", "vah264enc", "fdkaacenc"}, "h264").video, "vah264enc")
        self.assertIsNone(og.choose({"fdkaacenc"}, "h264"))

    def test_recording_never_takes_software_av1_over_hardware_h264(self):
        both = {"svtav1enc", "vah264lpenc", "fdkaacenc"}
        self.assertEqual(og.choose_record(both).video, "vah264lpenc")
        self.assertEqual(og.choose_record(both | {"vaav1enc"}).video, "vaav1enc")
        self.assertEqual(og.choose_record({"svtav1enc", "openh264enc"}).codec, "av1")
        self.assertEqual(og.choose_record(both, "av1").video, "svtav1enc")

    def test_encode_ends_in_muxable_caps(self):
        choice = og.choose({"openh264enc", "fdkaacenc"}, "h264")
        video = og.encode_video(choice, Preset("landscape", "h264", 6000, 160))
        self.assertEqual(video[-1].factory, "appsink")
        self.assertIn("stream-format=avc", video[-2].props["caps"])
        self.assertEqual(video[-3].props, {})  # no in-band SPS/PPS in the shared encode
        sound = og.encode_sound(choice, Preset("landscape", "h264", 6000, 160))
        self.assertEqual([e.factory for e in sound][-3:], ["aacparse", "capsfilter", "appsink"])

    def test_row_sinks(self):
        self.assertEqual([e.factory for e in og.row_sink("a", "rtmp", "rtmp://x/app/k")], ["flvmux", "rtmp2sink"])
        self.assertEqual(og.row_sink("a", "rtmp", "rtmp://x", codec="av1")[0].factory, "eflvmux")
        self.assertEqual([e.factory for e in og.row_sink("a", "srt", "srt://x")], ["mpegtsmux", "srtsink"])
        self.assertEqual(og.row_sink("a", "file", "/t/x.mp4", container="mp4")[0].factory, "mp4mux")
        self.assertEqual([e.factory for e in og.video_adapter("a", "srt", "h264")], ["h264parse", "capsfilter"])
        self.assertEqual(og.video_adapter("a", "rtmp", "h264"), [])
        with self.assertRaises(ValueError):
            og.row_sink("a", "ftp", "x")

    def test_words(self):
        stats = RowStats(state="live", bitrate_kbps=6012)
        self.assertEqual(og.state_words(stats), "Live · 6.0 Mb/s")
        self.assertEqual(og.error_words("Could not connect to 1.2.3.4: Connection refused", "rtmp"),
                         "The server refused the connection")
        self.assertEqual(og.error_words("No space left on device", "file"), "The disk is full")


if __name__ == "__main__":
    unittest.main()
