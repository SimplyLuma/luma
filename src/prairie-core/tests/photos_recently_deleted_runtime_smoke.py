#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Exercise Recently Deleted and slideshow state through the real GTK UI."""

from __future__ import annotations

import binascii
from pathlib import Path
import struct
import tempfile
import time
import zlib

from gi.repository import GLib

from prairie_apps.photos import PhotosApplication, PhotosWindow
from prairie_apps.photos_backend import PhotoLibrary


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = b"".join(b"\0" + b"\x68\x8f\xc8" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def settle(seconds: float = 0.5) -> None:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="luma-photos-deleted-") as temporary:
        root = Path(temporary)
        pictures = root / "Pictures"
        pictures.mkdir()
        fixture = pictures / "Recoverable fixture.png"
        fixture.write_bytes(_png(12, 8))
        library = PhotoLibrary(root / "library.sqlite3")
        source = library.add_source(pictures)
        assert library.scan_source(source.id).added == 1
        copy_id = library.assets()[0].copies[0].id
        trash_path = library.trash_copy(copy_id, root / "data")
        assert trash_path.is_file() and not fixture.exists()

        application = PhotosApplication()
        assert application.register(None)
        window = PhotosWindow(application, library=library)
        window.set_default_size(1024, 600)
        window.present()
        settle()
        window.show_recently_deleted()
        settle()

        assert window.collection == "deleted"
        assert len(window.records) == 1
        record = window.records[0]
        assert record.deleted and record.copies[0].original_path == fixture.resolve()
        window.selected_ids = {record.id}
        window.request_restore()
        settle(1.0)
        assert fixture.is_file()
        assert library.assets(collection="deleted") == ()

        window.collection = "all"
        window._refresh_view()
        window._open_asset(record.id)
        window._toggle_slideshow()
        settle(0.1)
        assert window._slideshow_timeout
        window._toggle_slideshow()
        assert not window._slideshow_timeout
        window.close()
        settle(0.1)

    print("photos-recently-deleted-runtime-smoke: restore and slideshow ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
