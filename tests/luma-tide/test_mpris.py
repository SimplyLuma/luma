# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import unittest

from luma_tide.model import RepeatMode
from luma_tide.identity import APP_ID, PREVIEW_APP_ID, mpris_name, validate_application_id
from luma_tide.mpris import MprisService
from luma_tide.playback import PlaybackSnapshot, PlaybackState


class _Variant:
    def __init__(self, signature: str, value: object) -> None:
        self.signature = signature
        self.value = value


class _GLib:
    Variant = _Variant


class MprisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MprisService.__new__(MprisService)
        self.service.GLib = _GLib

    def test_open_uri_capabilities_are_not_advertised(self) -> None:
        schemes = self.service._root_property("SupportedUriSchemes")
        mime_types = self.service._root_property("SupportedMimeTypes")
        self.assertEqual((schemes.signature, schemes.value), ("as", []))
        self.assertEqual((mime_types.signature, mime_types.value), ("as", []))

    def test_preview_publishes_its_own_desktop_identity(self):
        self.assertEqual(self.service._root_property('DesktopEntry').value, APP_ID)
        self.service.application_id = PREVIEW_APP_ID
        self.assertEqual(self.service._root_property('DesktopEntry').value, PREVIEW_APP_ID)
        self.assertEqual(self.service._root_property('Identity').value, 'Tide (LumaUI preview)')
        self.assertEqual(mpris_name(APP_ID), 'org.mpris.MediaPlayer2.Tide')
        self.assertEqual(mpris_name(PREVIEW_APP_ID), 'org.mpris.MediaPlayer2.Tide.LumaUIPreview')

    def test_unknown_application_identity_is_rejected(self):
        for identifier in ('', 'org.projectluma.Photos', 'org.projectluma.Tide.OtherPreview'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                validate_application_id(identifier)

    def test_playback_and_loop_status_map_to_mpris_values(self) -> None:
        self.assertEqual(
            self.service._status(PlaybackSnapshot(state=PlaybackState.PLAYING)),
            "Playing",
        )
        self.assertEqual(
            self.service._status(PlaybackSnapshot(state=PlaybackState.ERROR)),
            "Stopped",
        )
        self.assertEqual(
            self.service._loop(PlaybackSnapshot(repeat=RepeatMode.ONE)),
            "Track",
        )


if __name__ == "__main__":
    unittest.main()
