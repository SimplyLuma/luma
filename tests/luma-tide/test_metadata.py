# SPDX-License-Identifier: Apache-2.0
"""Multi-artist tag ingestion: metadata.py must carry every value a tag
format structurally, genuinely gives it (Vorbis/FLAC repeated fields,
MP4 multi-value atoms, ID3v2.4 null-separated text frames) through to
`MediaMetadata.artists`/`album_artists`, and must never guess apart a tag
that only ever had one value.

Mutagen is a hard runtime dependency of luma-tide (see
packaging/rpm/luma-tide.spec: `Requires: python3-mutagen`), but this
sandbox may not have it installed, so every test here skips cleanly
rather than erroring when it is unavailable — matching how metadata.py
itself already treats mutagen as absent-tolerant via `default_reader()`.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luma_tide.metadata import MutagenMetadataReader, _multi_text, _text

try:
    import mutagen  # noqa: F401

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

skip_without_mutagen = unittest.skipUnless(
    MUTAGEN_AVAILABLE, "mutagen is not installed in this environment"
)


def _flac_streaminfo(
    *, sample_rate: int = 44100, channels: int = 2, bits_per_sample: int = 16,
    total_samples: int = 44100 * 2,
) -> bytes:
    """The 34-byte STREAMINFO metadata block payload every FLAC file must
    start with. Built by hand (no `flac`/`ffmpeg` binary exists in this
    sandbox) by packing the fields the format spec defines, not guessed:
    https://xiph.org/flac/format.html#metadata_block_streaminfo
    """
    block = (4096).to_bytes(2, "big") * 2  # min/max blocksize (unused by tag reading)
    block += (0).to_bytes(3, "big") * 2  # min/max framesize: 0 means "unknown"
    packed = (
        (sample_rate << 44)
        | ((channels - 1) << 41)
        | ((bits_per_sample - 1) << 36)
        | total_samples
    )
    block += packed.to_bytes(8, "big")
    block += b"\x00" * 16  # MD5 signature: unchecked by mutagen's tag reading
    assert len(block) == 34
    return block


def _make_flac(path: Path) -> "mutagen.flac.FLAC":  # noqa: F821
    from mutagen.flac import FLAC

    payload = _flac_streaminfo()
    # is_last=1 (0x80), block type 0 (STREAMINFO): the only metadata block,
    # with no audio frames following — mutagen never decodes audio to read
    # or write tags, only the metadata blocks that precede it.
    block = bytes([0x80]) + len(payload).to_bytes(3, "big") + payload
    path.write_bytes(b"fLaC" + block)
    return FLAC(path)


def _make_mp3(path: Path) -> None:
    """A real, syncable (if silent) MPEG-1 Layer III stream: MP3's info
    parser requires an actual frame sync to open the file at all (unlike
    FLAC, an ID3-only file with no audio frames raises `HeaderNotFoundError`
    — verified against real mutagen behavior, not assumed). The header
    below (0xFF 0xFB 0x90 0x64) is a standard 128kbps/44100Hz/stereo/no-CRC
    frame header; the 417-byte frame length matches that bitrate/rate combo.
    """
    header = bytes([0xFF, 0xFB, 0x90, 0x64])
    frame = header + b"\x00" * (417 - len(header))
    path.write_bytes(frame * 20)


class MultiTextTests(unittest.TestCase):
    """Direct coverage of the extraction primitive without any file I/O."""

    def test_a_single_scalar_value_is_one_artist_not_split_apart(self) -> None:
        # "Earth, Wind & Fire" is one artist. metadata.py must never guess
        # a single tag value apart on delimiters like ',' or '&'.
        self.assertEqual(_multi_text("Earth, Wind & Fire"), ("Earth, Wind & Fire",))

    def test_a_plain_list_is_kept_in_order(self) -> None:
        self.assertEqual(_multi_text(["Artist A", "Artist B"]), ("Artist A", "Artist B"))

    def test_blank_entries_are_dropped(self) -> None:
        self.assertEqual(_multi_text(["Artist A", "  ", ""]), ("Artist A",))

    def test_none_and_empty_string_produce_no_artists(self) -> None:
        self.assertEqual(_multi_text(None), ())
        self.assertEqual(_multi_text(""), ())


class MetadataTests(unittest.TestCase):
    def test_empty_tags_use_fallback(self):
        for value in (None, "", "  ", [], [""], ["  "]):
            with self.subTest(value=value):
                self.assertEqual(_text(value, "6 Money"), "6 Money")

    def test_real_title_is_preserved(self):
        self.assertEqual(_text(["  Actual %20 title  "], "6 Money"), "Actual %20 title")


@skip_without_mutagen
class Mp4TagShapeTests(unittest.TestCase):
    """MP4's multi-value atoms, verified against a real `mutagen.mp4.MP4Tags`
    object (the exact type `MutagenMetadataReader.read()` would receive as
    `media.tags` for an M4A/MP4 file) rather than a hand-built container:
    there is no audio encoder in this sandbox to produce a real .m4a file,
    so `mutagen.File` is monkeypatched to hand back a stand-in object whose
    `.tags` is a genuine MP4Tags instance and whose `.info` mimics
    `mutagen.mp4.MP4Info`'s public attributes — the reader cannot tell the
    difference for tag-extraction purposes, since it only ever touches
    `media.tags` and `media.info`'s duration/bitrate/sample_rate/channels.
    """

    def _read_via_fake_mp4(self, path: Path, tags: dict) -> "MediaMetadata":  # noqa: F821
        from mutagen.mp4 import MP4Tags

        mp4_tags = MP4Tags()
        mp4_tags.update(tags)

        class FakeInfo:
            length = 180.0
            bitrate = 256000
            sample_rate = 44100
            channels = 2
            codec = None

        class FakeMedia:
            tags = mp4_tags
            info = FakeInfo()
            pictures = ()

        reader = MutagenMetadataReader(cache_root=path.parent / "artwork")
        with mock.patch("mutagen.File", return_value=FakeMedia()):
            return reader.read(path)

    def test_multi_value_artist_and_album_artist_atoms_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.m4a"
            path.write_bytes(b"")  # content_digest() just hashes the bytes
            metadata = self._read_via_fake_mp4(
                path,
                {
                    "\xa9nam": ["Multi Artist Song"],
                    "\xa9ART": ["Artist A", "Artist B"],
                    "aART": ["Various Artists"],
                    "\xa9alb": ["A Compilation"],
                },
            )
        self.assertEqual(metadata.title, "Multi Artist Song")
        self.assertEqual(metadata.artists, ("Artist A", "Artist B"))
        self.assertEqual(metadata.artist, "Artist A")
        self.assertEqual(metadata.album_artists, ("Various Artists",))
        self.assertEqual(metadata.album_artist, "Various Artists")

    def test_single_value_atom_stays_a_single_artist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.m4a"
            path.write_bytes(b"")
            metadata = self._read_via_fake_mp4(path, {"\xa9ART": ["Solo Artist"]})
        self.assertEqual(metadata.artists, ("Solo Artist",))
        self.assertEqual(metadata.artist, "Solo Artist")


@skip_without_mutagen
class FlacVorbisCommentTests(unittest.TestCase):
    """FLAC's tag block is a Vorbis comment block (the same `VCommentDict`
    machinery OGG/Opus use), read and written through a real on-disk file
    built by hand above — genuine mutagen parsing end to end, not a mock."""

    def test_repeated_artist_fields_are_all_kept_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.flac"
            flac = _make_flac(path)
            flac["title"] = ["Low Tide"]
            flac["artist"] = ["Artist A", "Artist B"]
            flac["albumartist"] = ["Various Artists"]
            flac.save()

            reader = MutagenMetadataReader(cache_root=path.parent / "artwork")
            metadata = reader.read(path)

        self.assertEqual(metadata.title, "Low Tide")
        self.assertEqual(metadata.artists, ("Artist A", "Artist B"))
        self.assertEqual(metadata.artist, "Artist A")
        self.assertEqual(metadata.album_artists, ("Various Artists",))

    def test_a_single_repeated_field_with_one_value_is_one_artist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.flac"
            flac = _make_flac(path)
            flac["artist"] = ["Solo Artist"]
            flac.save()

            reader = MutagenMetadataReader(cache_root=path.parent / "artwork")
            metadata = reader.read(path)

        self.assertEqual(metadata.artists, ("Solo Artist",))
        self.assertEqual(metadata.artist, "Solo Artist")

    def test_compilation_album_shares_album_artist_but_keeps_distinct_real_artists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reader = MutagenMetadataReader(cache_root=root / "artwork")
            results = []
            for index, artists in enumerate([["Artist A"], ["Artist B", "Artist C"]]):
                path = root / f"track{index}.flac"
                flac = _make_flac(path)
                flac["title"] = [f"Track {index}"]
                flac["artist"] = artists
                flac["albumartist"] = ["Various Artists"]
                flac.save()
                results.append(reader.read(path))

        self.assertEqual([m.album_artist for m in results], ["Various Artists", "Various Artists"])
        self.assertEqual(results[0].artists, ("Artist A",))
        self.assertEqual(results[1].artists, ("Artist B", "Artist C"))


@skip_without_mutagen
class Id3TextFrameTests(unittest.TestCase):
    """ID3v2.4 TPE1/TPE2 carry multiple values as one null-byte-separated
    string; mutagen already splits this into `TextFrame.text` (verified
    directly below), but the *frame object itself* (`tags["TPE1"]`) is not
    a plain list, so the fix has to reach into `.text` explicitly or it
    silently regresses to `str(frame)`, which re-joins with an embedded
    '\\x00' instead of a comma — worse than the original one-artist bug."""

    def test_mutagen_id3_frame_text_is_a_list_not_the_frame_itself(self) -> None:
        from mutagen.id3 import TPE1

        frame = TPE1(encoding=3, text=["Artist A", "Artist B"])
        self.assertIsInstance(frame.text, list)
        self.assertEqual(str(frame), "Artist A\x00Artist B")  # the trap this fix avoids

    def test_null_separated_multi_value_tpe1_is_split_into_artists(self) -> None:
        from mutagen.id3 import ID3, TIT2, TPE1, TPE2

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.mp3"
            _make_mp3(path)
            id3 = ID3()
            id3.add(TIT2(encoding=3, text=["Low Tide"]))
            id3.add(TPE1(encoding=3, text=["Artist A", "Artist B"]))
            id3.add(TPE2(encoding=3, text=["Various Artists"]))
            id3.save(path, v2_version=4)

            reader = MutagenMetadataReader(cache_root=path.parent / "artwork")
            metadata = reader.read(path)

        self.assertEqual(metadata.title, "Low Tide")
        self.assertEqual(metadata.artists, ("Artist A", "Artist B"))
        self.assertEqual(metadata.artist, "Artist A")
        self.assertEqual(metadata.album_artists, ("Various Artists",))
        # No embedded null byte anywhere in the display string.
        self.assertNotIn("\x00", metadata.artist)

    def test_featured_artist_in_a_single_tpe1_value_is_not_split(self) -> None:
        """A single TPE1 value of "Artist A feat. Artist B" has no
        structural multi-value marker (it is one string, not two frame
        values), so it is correctly one credited artist — splitting on
        "feat." would be exactly the rejected heuristic-delimiter guess."""
        from mutagen.id3 import ID3, TIT2, TPE1

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.mp3"
            _make_mp3(path)
            id3 = ID3()
            id3.add(TIT2(encoding=3, text=["Low Tide"]))
            id3.add(TPE1(encoding=3, text=["Artist A feat. Artist B"]))
            id3.save(path, v2_version=4)

            reader = MutagenMetadataReader(cache_root=path.parent / "artwork")
            metadata = reader.read(path)

        self.assertEqual(metadata.artists, ("Artist A feat. Artist B",))
        self.assertEqual(metadata.artist, "Artist A feat. Artist B")


if __name__ == "__main__":
    unittest.main()
