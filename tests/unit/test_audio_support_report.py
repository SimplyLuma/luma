# SPDX-License-Identifier: MPL-2.0
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/support/audio-report.py"
spec = importlib.util.spec_from_file_location("audio_report", PATH)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class AudioSupportReportTests(unittest.TestCase):
    def test_hardware_report_excludes_apps_serials_and_other_defaults(self):
        graph = [
            {"id": 43, "info": {"props": {"device.api": "alsa", "device.serial": "PRIVATE"},
                              "params": {"Route": [{"name": "Speaker", "props": {"mute": False,
                                          "channelVolumes": [1.0, 1.0], "secret": "PRIVATE"}}]}}},
            {"id": 53, "info": {"props": {"media.class": "Audio/Sink", "device.id": 43,
                                          "node.name": "alsa.speaker", "application.name": "PRIVATE"}}},
            {"id": 99, "info": {"props": {"media.class": "Stream/Output/Audio", "media.name": "PRIVATE"}}},
            {"props": {"metadata.name": "default"}, "metadata": [
                {"key": "default.audio.sink", "value": '{"name":"alsa.speaker"}'},
                {"key": "default.audio.source", "value": {"name": "bluez.PRIVATE"}},
                {"key": "unrelated", "value": "PRIVATE"}]},
        ]
        result = report.audio_graph(graph)
        self.assertNotIn("PRIVATE", str(result))
        self.assertEqual([x["id"] for x in result["hardware"]], [43, 53])
        self.assertEqual(result["defaults"]["default.audio.sink"], "alsa.speaker")
        self.assertEqual(result["hardware"][0]["parameters"]["Route"][0]["props"],
                         {"mute": False, "channelVolumes": [1.0, 1.0]})

    def test_report_private_and_does_not_overwrite_or_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            report.save(path, {"changed_audio_settings": False})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                report.save(path, {"overwrite": True})
            link = Path(directory) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                report.save(link, {})
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
