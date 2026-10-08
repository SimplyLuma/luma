# SPDX-License-Identifier: Apache-2.0
"""Pictures kept with notes: one file per picture, named by its content.

A picture pasted or dropped into a note is stored once under
``<notes data>/attachments/<sha256>.<ext>`` and the note refers to it by that
name. The same picture pasted into ten notes is one file, and a sync that has
sent a name once never has to send it again. Files are written to a temporary
name, flushed and renamed, so a crash leaves either the whole picture or none.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .notes_backend import notes_data_directory

MAX_PICTURE_BYTES = 20 * 1024 * 1024
NAME_PATTERN = re.compile(r"^[0-9a-f]{64}\.(png|jpg|gif|webp)$")
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)
MIME_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


class PictureError(ValueError):
    """The bytes are not a picture Notes keeps, or are too large."""


def attachments_directory(environment: dict[str, str] | None = None) -> Path:
    return notes_data_directory(environment) / "attachments"


def picture_kind(data: bytes) -> str | None:
    """The file extension a picture's own bytes declare, or None."""
    for signature, kind in _SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def store_picture(data: bytes, directory: Path | None = None) -> str:
    """Keep a picture and return its name. Storing the same bytes twice is free."""
    if len(data) > MAX_PICTURE_BYTES:
        raise PictureError("This picture is larger than 20 MB.")
    kind = picture_kind(data)
    if kind is None:
        raise PictureError("Notes can keep PNG, JPEG, GIF and WebP pictures.")
    name = f"{hashlib.sha256(data).hexdigest()}.{kind}"
    folder = directory or attachments_directory()
    target = folder / name
    if target.exists() and target.stat().st_size == len(data):
        return name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".picture-", dir=folder)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        directory_handle = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory_handle)
        finally:
            os.close(directory_handle)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return name


def picture_path(name: str, directory: Path | None = None) -> Path:
    if not NAME_PATTERN.match(name):
        raise PictureError("not a picture name")
    return (directory or attachments_directory()) / name


def picture_digest(name: str) -> str:
    """The sha256 a picture's name carries."""
    return name.split(".", 1)[0]


def referenced_pictures(runs) -> tuple[str, ...]:
    """Every picture a note's formatting refers to, once each, in order."""
    seen: list[str] = []
    for run in runs:
        source = run.get("src") if run.get("style") == "image" else None
        if isinstance(source, str) and NAME_PATTERN.match(source) and source not in seen:
            seen.append(source)
    return tuple(seen)


# ------------------------------------------------------------ sync status
#
# The sync service writes which pictures a sync host would not take, and why,
# to one small file beside the notes; Notes reads it to mark those pictures.
# The reasons are the host's own stable codes.

STORAGE_FULL = "note_storage_full"
TOO_LARGE = "note_image_too_large"
SYNC_STATUS_NAME = "picture-sync.json"


def sync_status_path(directory: Path | None = None) -> Path:
    # In a folder of its own: the sync service is started by changes to the
    # notes folder itself, and its own status must not start it again.
    return (directory or notes_data_directory()) / "sync" / SYNC_STATUS_NAME


def read_sync_status(directory: Path | None = None) -> dict:
    """{"pictures": {sha256: reason}, "quota_bytes": int|None, "used_bytes": int|None}."""
    try:
        decoded = json.loads(sync_status_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        decoded = {}
    pictures = decoded.get("pictures") if isinstance(decoded, dict) else None
    pictures = {sha: reason for sha, reason in (pictures or {}).items()
                if isinstance(sha, str) and reason in (STORAGE_FULL, TOO_LARGE)}
    usage = {key: decoded.get(key) if isinstance(decoded.get(key), int) else None
             for key in ("quota_bytes", "used_bytes")} if isinstance(decoded, dict) else {}
    return {"pictures": pictures, **{"quota_bytes": None, "used_bytes": None, **usage}}


def write_sync_status(status: dict, directory: Path | None = None) -> bool:
    """Write the status atomically, and only when it changed. True if written."""
    path = sync_status_path(directory)
    text = json.dumps(status, sort_keys=True, indent=1)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".picture-sync-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def shrink_picture(data: bytes, limit: int = MAX_PICTURE_BYTES) -> bytes:
    """The picture re-encoded to fit under ``limit`` bytes.

    Pictures with transparency stay PNG and are scaled down; others become
    JPEG, first at a high quality and then smaller, until they fit. Raises
    PictureError when even a small version does not fit.
    """
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        raise PictureError("This picture could not be read.") from None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        raise PictureError("This picture could not be read.")
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    transparent = pixbuf.get_has_alpha() and not _opaque(pixbuf)
    if pixbuf.get_has_alpha() and not transparent:
        # An alpha channel nothing uses: JPEG can carry it.
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pixbuf.get_width(), pixbuf.get_height())
        pixbuf.composite_color(flat, 0, 0, pixbuf.get_width(), pixbuf.get_height(), 0, 0, 1, 1,
                               GdkPixbuf.InterpType.NEAREST, 255, 0, 0, 64, 0xFFFFFF, 0xFFFFFF)
        pixbuf = flat
    budget = limit - 64 * 1024
    scale = 1.0
    for _attempt in range(12):
        width = max(1, int(pixbuf.get_width() * scale))
        height = max(1, int(pixbuf.get_height() * scale))
        frame = pixbuf if scale == 1.0 else pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.HYPER)
        if transparent:
            ok, encoded = frame.save_to_bufferv("png", ["compression"], ["9"])
        else:
            ok, encoded = frame.save_to_bufferv("jpeg", ["quality"], ["88"])
        if ok and len(encoded) <= budget:
            return bytes(encoded)
        scale *= 0.75
    raise PictureError("This picture could not be made small enough.")


def _opaque(pixbuf) -> bool:
    """Whether every pixel of a pixbuf with an alpha channel is fully opaque."""
    pixels = pixbuf.get_pixels()
    stride, width = pixbuf.get_rowstride(), pixbuf.get_width()
    for row in range(pixbuf.get_height()):
        alpha = pixels[row * stride + 3:row * stride + width * 4:4]
        if alpha.count(255) != len(alpha):
            return False
    return True
