#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Exercise the real GTK/GStreamer video viewer with a generated fixture."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile
import time

from gi.repository import GLib

from prairie_apps.photos import PhotosApplication, PhotosWindow
from prairie_apps.photos_backend import PhotoLibrary


def settle(seconds: float = 0.8) -> None:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    if not args.fixture.is_file():
        raise FileNotFoundError(args.fixture)

    with tempfile.TemporaryDirectory(prefix="luma-photos-video-") as temporary:
        root = Path(temporary)
        pictures = root / "Pictures"
        pictures.mkdir()
        fixture = pictures / "Generated video.webm"
        shutil.copyfile(args.fixture, fixture)
        library = PhotoLibrary(root / "library.sqlite3")
        source = library.add_source(pictures)
        result = library.scan_source(source.id)
        assert result.added == 1
        record = library.assets()[0]
        assert record.is_video

        application = PhotosApplication()
        assert application.register(None)
        window = PhotosWindow(application, library=library)
        window.set_default_size(1024, 600)
        window.present()
        settle()
        window._refresh_view()
        window._open_asset(record.id)
        settle()
        assert window.viewer_media.get_visible_child_name() == "video"
        stream = window.viewer_video.get_media_stream()
        assert stream is not None
        stream.play()
        settle(0.25)
        assert not stream.get_error()
        stream.pause()
        window.close()
        settle(0.1)
    print("photos-video-runtime-smoke: gstreamer playback ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
