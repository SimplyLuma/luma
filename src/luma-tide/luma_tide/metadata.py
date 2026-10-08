# SPDX-License-Identifier: Apache-2.0
"""Bounded, off-main-thread media metadata and embedded artwork extraction."""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Protocol

from .model import MediaMetadata

_log = logging.getLogger("tide")

READ_CHUNK = 1024 * 1024


class CancelledScan(RuntimeError):
    pass


class MetadataReader(Protocol):
    def read(self, path: Path, cancel: threading.Event | None = None) -> MediaMetadata: ...


def content_digest(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(READ_CHUNK):
            if cancel is not None and cancel.is_set():
                raise CancelledScan("library scan cancelled")
            digest.update(block)
    return digest.hexdigest()


def _number(value: object) -> int:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else 0
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


def _multi_text(value: object) -> tuple[str, ...]:
    """The full, ordered set of values a tag genuinely, structurally
    carries — never a heuristic split of one string. Handles:

    - A plain list/tuple, as mutagen already hands back for a repeated
      Vorbis comment field (FLAC/OGG) or a multi-value MP4 atom.
    - A raw `mutagen.id3` text frame object (e.g. TPE1/TPE2): its `.text`
      attribute is mutagen's own split of the frame's ID3v2.4
      null-byte-separated value, so that is used instead of `str(frame)`
      (which would otherwise glue the values back together with an
      embedded `\\x00`).
    - A single scalar, which becomes a one-item tuple — a tag that only
      ever had one value is correctly one artist, not something to guess
      apart.

    Blank/whitespace-only entries are dropped rather than kept as spurious
    "artists".
    """
    frame_text = getattr(value, "text", None)
    if frame_text is not None and isinstance(frame_text, (list, tuple)):
        value = frame_text
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip() if value is not None else ""
    return (text,) if text else ()


def _text(value: object, fallback: str = "") -> str:
    values = _multi_text(value)
    return values[0] if values else fallback


class BasicMetadataReader:
    """Dependency-free fallback with exact identity and WAV duration support."""

    def read(self, path: Path, cancel: threading.Event | None = None) -> MediaMetadata:
        stat = path.stat()
        duration_ns = 0
        sample_rate = 0
        channels = 0
        bitrate = 0
        if path.suffix.casefold() in {".wav", ".wave"}:
            with wave.open(str(path), "rb") as audio:
                sample_rate = audio.getframerate()
                channels = audio.getnchannels()
                duration_ns = int(audio.getnframes() / max(1, sample_rate) * 1_000_000_000)
                bitrate = sample_rate * channels * audio.getsampwidth() * 8
        return MediaMetadata(
            uri=path.resolve().as_uri(),
            content_digest=content_digest(path, cancel),
            title=path.stem,
            duration_ns=duration_ns,
            format=path.suffix.lstrip(".").upper(),
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )


class MutagenMetadataReader(BasicMetadataReader):
    """Reads common tags and artwork with Mutagen, retaining a safe fallback."""

    def __init__(self, cache_root: Path | None = None) -> None:
        if cache_root is None:
            cache_home = Path(
                os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
            )
            cache_root = cache_home / "luma-tide/artwork"
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def available() -> bool:
        try:
            import mutagen  # noqa: F401
        except ImportError:
            return False
        return True

    def read(self, path: Path, cancel: threading.Event | None = None) -> MediaMetadata:
        try:
            import mutagen
        except ImportError:
            return super().read(path, cancel)
        stat = path.stat()
        media = mutagen.File(path, easy=False)
        if media is None:
            return super().read(path, cancel)
        tags = media.tags or {}

        def tag(*names: str, default: object = "") -> object:
            # `names` mixes key spellings from every container this reader
            # supports (raw ID3 frame IDs, Vorbis comment field names, MP4
            # atom names) since a single `tags` mapping is always exactly
            # one of those. A Vorbis comment dict raises ValueError (not a
            # plain miss) for a key outside its own syntax — e.g. an MP4
            # atom name's non-ASCII "\xa9" — so a name from another
            # container's alphabet must be treated as simply absent rather
            # than left to propagate.
            for name in names:
                try:
                    if name in tags:
                        return tags[name]
                except (ValueError, TypeError, KeyError):
                    continue
                lowered = name.casefold()
                try:
                    keys = list(tags.keys())
                except (ValueError, TypeError):
                    continue
                for key in keys:
                    if str(key).casefold() == lowered:
                        return tags[key]
            return default

        info = getattr(media, "info", None)
        duration_ns = int(float(getattr(info, "length", 0) or 0) * 1_000_000_000)
        bitrate = int(getattr(info, "bitrate", 0) or 0)
        sample_rate = int(getattr(info, "sample_rate", 0) or 0)
        channels = int(getattr(info, "channels", 0) or 0)
        # Aliases cover the three tag containers Tide reads with `easy=False`
        # (needed so artwork frames/atoms are still reachable): raw ID3 frame
        # names (TIT2/TPE1/...), Vorbis comment field names (FLAC/OGG, which
        # mutagen already lowercases), and MP4 atom names ("\xa9nam"/"\xa9ART"/...).
        title = _text(tag("title", "TIT2", "\xa9nam"), path.stem)
        artists = _multi_text(tag("artist", "TPE1", "\xa9ART"))
        artist = artists[0] if artists else "Unknown artist"
        album = _text(tag("album", "TALB", "\xa9alb"), "Unknown album")
        album_artists = _multi_text(tag("albumartist", "album artist", "TPE2", "aART"))
        album_artist = album_artists[0] if album_artists else artist
        recording_id = _text(
            tag(
                "musicbrainz_trackid",
                "musicbrainz recording id",
                "UFID:http://musicbrainz.org",
            )
        ) or None
        replaygain = _text(tag("replaygain_track_gain"))
        replaygain_value: float | None = None
        if replaygain:
            with __import__("contextlib").suppress(ValueError):
                replaygain_value = float(replaygain.lower().replace("db", "").strip())
        artwork_uri = self._extract_artwork(media, tags)
        mime = getattr(info, "codec", None) or mimetypes.guess_type(path.name)[0] or path.suffix.lstrip(".")
        return MediaMetadata(
            uri=path.resolve().as_uri(),
            content_digest=content_digest(path, cancel),
            title=title,
            artist=artist,
            album=album,
            album_artist=album_artist,
            artists=artists,
            album_artists=album_artists,
            duration_ns=duration_ns,
            disc_number=_number(tag("discnumber", "disk", "TPOS")),
            track_number=_number(tag("tracknumber", "track", "TRCK")),
            year=_number(tag("date", "year", "TDRC")),
            genre=_text(tag("genre", "TCON", "\xa9gen")),
            format=str(mime).upper(),
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            artwork_uri=artwork_uri,
            recording_id=recording_id,
            replaygain_track_gain=replaygain_value,
        )

    def _extract_artwork(self, media: Any, tags: Any) -> str | None:
        candidates: list[tuple[bytes, str]] = []
        for picture in getattr(media, "pictures", ()):
            data = bytes(getattr(picture, "data", b""))
            if data:
                candidates.append((data, str(getattr(picture, "mime", "image/jpeg"))))
        values = tags.values() if hasattr(tags, "values") else ()
        for value in values:
            if value.__class__.__name__ == "APIC":
                data = bytes(getattr(value, "data", b""))
                if data:
                    candidates.append((data, str(getattr(value, "mime", "image/jpeg"))))
        cover = tags.get("covr") if hasattr(tags, "get") else None
        for value in cover if isinstance(cover, (list, tuple)) else ():
            data = bytes(value)
            if data:
                candidates.append((data, "image/jpeg"))
        if not candidates:
            return None
        data, mime = candidates[0]
        if len(data) > 20 * 1024 * 1024:
            return None
        digest = hashlib.sha256(data).hexdigest()
        extension = {"image/png": ".png", "image/webp": ".webp"}.get(mime.casefold(), ".jpg")
        destination = self.cache_root / f"{digest}{extension}"
        if destination.exists():
            return destination.as_uri()
        # The cache is content-addressed, so every track of an album shares one
        # file. Metadata workers run in parallel, so each writes its own
        # uniquely named temporary file and renames it into place; a rename
        # onto an identical file another worker just wrote is harmless. A name
        # derived from the digest and the process id alone was shared by every
        # thread, and the second track to reach it failed to index.
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{digest}.", suffix=".tmp", dir=self.cache_root)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                temporary.replace(destination)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        except OSError as error:
            # Artwork is decoration: a full disk or unwritable cache must not
            # keep the track itself out of the library.
            _log.warning("artwork cache write failed for %s: %s", digest[:12], error)
            return destination.as_uri() if destination.exists() else None
        return destination.as_uri()


def default_reader() -> MetadataReader:
    return MutagenMetadataReader() if MutagenMetadataReader.available() else BasicMetadataReader()

