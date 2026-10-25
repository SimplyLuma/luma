# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prairie_apps import audio_backend as audio


def device(name, *, default=False, monitor=False):
    values = {'device.class': 'monitor' if monitor else 'sound', 'is-default': default}
    properties = SimpleNamespace(get_string=values.get, get_value=values.get,
                                 has_field=lambda key: key in values)
    return SimpleNamespace(get_display_name=lambda: name, get_properties=lambda: properties)


class RecordingCapabilityTests(unittest.TestCase):
    def probe(self, devices, *, started=True, failure=None):
        monitor = Mock()
        monitor.start.return_value = started
        monitor.get_devices.return_value = devices
        monitor.get_devices.side_effect = failure
        gst = SimpleNamespace(ElementFactory=Mock(), DeviceMonitor=Mock())
        gst.DeviceMonitor.new.return_value = monitor
        with patch.object(audio, 'gst', return_value=gst), patch('subprocess.run', side_effect=AssertionError('host command invoked')):
            result = audio.inspect_recording_capability()
        monitor.stop.assert_called_once()
        return result

    def test_sandbox_detects_default_microphone_without_host_tools(self):
        result = self.probe([device('Speakers', monitor=True), device('Mic', default=True)])
        self.assertTrue(result.available)
        self.assertEqual(result.source, 'Mic')

    def test_speaker_monitor_is_not_a_microphone(self):
        self.assertFalse(self.probe([device('Speakers', monitor=True)]).available)

    def test_unavailable_service_is_reported_and_monitor_stopped(self):
        self.assertFalse(self.probe([], started=False).available)

    def test_probe_failure_releases_monitor(self):
        self.assertFalse(self.probe([], failure=OSError('disconnected')).available)


if __name__ == '__main__':
    unittest.main()
