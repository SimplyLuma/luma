# SPDX-License-Identifier: Apache-2.0
import unittest

from luma_streamy import graph
from luma_streamy.model import Project


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.project = Project.starter()
        self.scene = self.project.active_scene
        self.camera = self.project.add_widget(self.scene.id, "camera")
        self.camera.effects.shadow_blur = 20
        self.camera.effects.shadow_y = 8
        self.camera.effects.shadow_opacity = 0.5
        self.camera.effects.stroke_color = "1F6FE0"

    def test_frame_and_pad(self):
        props = graph.frame_props(self.camera)
        self.assertEqual(props["margin"], 39.0)
        self.assertEqual(props["stroke-color"], 0xFF1F6FE0)
        self.assertEqual(props["shadow-color"], 0x80000000)
        self.assertEqual(props["box-width"], round(self.camera.width))
        pad = graph.mixer_pad_props(self.camera, 3)
        self.assertEqual(pad["xpos"], round(self.camera.x - 39))
        self.assertEqual(pad["width"], round(self.camera.width + 78))
        self.assertEqual(pad["zorder"], 3)

    def test_picture_chains(self):
        live = graph.picture_chain(self.camera, 1, fd=7, node="42")
        src = live.first
        self.assertEqual((src.factory, src.props["fd"], src.props["target-object"]), ("pipewiresrc", 7, "42"))
        self.assertEqual(live.last.factory, "tee")
        layer = graph.layer_chain(self.camera, 1, "w-x-g1", "vertical")
        self.assertEqual(layer.last.factory, "lumaframe")
        self.assertEqual(layer.target, "mixer-vertical")
        self.assertEqual(graph.widget_of(layer.last.name), "x")
        self.assertIsNotNone(live.element("decodebin"))  # cameras may send MJPEG
        self.assertIsNotNone(live.element("videoconvert"))  # software decode unless asked for the GPU
        gpu = graph.picture_chain(self.camera, 1, fd=7, node="42", hw_decode=True)
        self.assertIsNone(gpu.element("videoconvert"))
        at = gpu.elements.index(gpu.element("decodebin"))
        self.assertEqual([e.factory for e in gpu.elements[at:at + 3]], ["decodebin", "vapostproc", "capsfilter"])
        self.assertEqual(gpu.elements[at + 2].props["caps"], "video/x-raw(memory:DMABuf)")
        display = self.project.add_widget(self.scene.id, "display")
        self.assertIsNone(graph.picture_chain(display, 0, fd=7).element("decodebin"))
        fake = graph.picture_chain(display, 0, fake=True)
        self.assertEqual(fake.first.factory, "videotestsrc")
        names = [e.name for e in live.elements]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(n.startswith(f"w-{self.camera.id}-") for n in names))

    def test_sound_chains(self):
        mic = self.project.add_widget(self.scene.id, "microphone")
        mic.volume_db = -6
        chain = graph.sound_chain(mic)
        self.assertEqual(chain.first.factory, "pulsesrc")
        self.assertAlmostEqual(chain.element("volume").props["volume"], 10 ** (-6 / 20))
        self.assertEqual(chain.target, "audiomixer")
        desk = self.project.add_widget(self.scene.id, "desktop-audio")
        self.assertEqual(graph.sound_chain(desk).first.props["device"], "@DEFAULT_MONITOR@")
        app = self.project.add_widget(self.scene.id, "app-audio")
        self.assertEqual(graph.sound_chain(app, fd=9, node="7").first.props["fd"], 9)
        mic.volume_db, mic.muted = -60, True
        self.assertEqual(graph.volume_props(mic), {"volume": 0.0, "mute": True})

    def test_encoder_choice(self):
        self.assertEqual(graph.choose({"vah264lpenc", "openh264enc", "fdkaacenc"}, "h264").video, "vah264lpenc")
        self.assertEqual(graph.choose({"openh264enc"}, "h264").audio, None)
        self.assertIsNone(graph.choose({"openh264enc"}, "av1"))
        self.assertEqual(graph.choose({"vaav1enc", "fdkaacenc"}, "av1").video, "vaav1enc")

    def test_encoder_order(self):
        """VA H.264, then openh264; hardware AV1, then SVT-AV1; each codec falls back within itself."""
        self.assertEqual(graph.H264 + graph.AV1, ("vah264lpenc", "vah264enc", "openh264enc",
                                                  "vaav1lpenc", "vaav1enc", "svtav1enc"))
        everything = set(graph.H264 + graph.AV1)
        self.assertEqual(graph.choose(everything, "h264").video, "vah264lpenc")
        self.assertEqual(graph.choose(everything - {"vah264lpenc"}, "h264").video, "vah264enc")
        self.assertEqual(graph.choose({"openh264enc", "svtav1enc"}, "h264").video, "openh264enc")
        self.assertEqual(graph.choose(everything, "av1").video, "vaav1lpenc")
        self.assertEqual(graph.choose(everything - {"vaav1lpenc"}, "av1").video, "vaav1enc")
        self.assertEqual(graph.choose({"openh264enc", "svtav1enc"}, "av1").video, "svtav1enc")
        self.assertIsNone(graph.choose({"svtav1enc"}, "h264"))

    def test_encoder_chains(self):
        va = graph.video_encoder_chain(graph.EncoderChoice("vah264lpenc", "fdkaacenc", "h264"), 6000, "stream")
        enc = next(e for e in va if e.name == "stream-encoder")
        self.assertEqual(enc.props, {"rate-control": "cbr", "bitrate": 6000, "key-int-max": 60, "b-frames": 0})
        self.assertEqual(va[-1].factory, "h264parse")
        soft = graph.video_encoder_chain(graph.EncoderChoice("openh264enc", None, "h264"), 6000, "record")
        enc = next(e for e in soft if e.name == "record-encoder")
        self.assertEqual(enc.props["bitrate"], 6_000_000)
        self.assertEqual(soft[1].factory, "videoconvert")
        av1 = graph.video_encoder_chain(graph.EncoderChoice("vaav1enc", None, "av1"), 8000, "record")
        self.assertEqual(av1[-1].factory, "av1parse")
        self.assertNotIn("b-frames", av1[-2].props)
        svt = graph.video_encoder_chain(graph.EncoderChoice("svtav1enc", None, "av1"), 8000, "record")
        self.assertEqual([e.factory for e in svt[1:]], ["videoconvert", "capsfilter", "svtav1enc", "av1parse"])
        self.assertEqual(svt[-2].props, {"preset": graph.SVTAV1_PRESET, "target-bitrate": 8000, "crf": -1,
                                         "intra-period-length": 59})
        self.assertEqual(graph.audio_encoder_chain(graph.EncoderChoice("x", None, "h264"), 160, "s"), [])

    def test_sinks_and_urls(self):
        self.assertEqual(graph.record_sink("mp4", "/tmp/a.mp4")[0].props["fragment-duration"], 2000)
        self.assertEqual(graph.record_sink("mkv", "/tmp/a.mkv")[0].factory, "matroskamux")
        self.assertEqual(graph.stream_sink("rtmp://h/app/k", enhanced=True)[0].factory, "eflvmux")
        self.assertEqual(graph.stream_url("rtmps://live.twitch.tv/app/", "key"), "rtmps://live.twitch.tv/app/key")
        with self.assertRaises(ValueError):
            graph.stream_url("http://example.com", "k")
        self.assertEqual([graph.backoff(n) for n in (1, 2, 3)], [2.0, 3.0, 4.5])
        self.assertEqual(graph.backoff(40), 900.0)

    def test_program(self):
        caps = graph.program((1080, 1920))[1].props["caps"]
        self.assertIn("width=1080,height=1920", caps)
        self.assertEqual(graph.tap_video()[-1].factory, "appsink")


if __name__ == "__main__":
    unittest.main()
