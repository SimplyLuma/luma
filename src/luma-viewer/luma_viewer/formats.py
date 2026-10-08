# SPDX-License-Identifier: Apache-2.0

"""What a file is, and how much of it Viewer can honestly show.

Three tiers, and the interface says which one a file is in:

* **Tier 1** — Viewer owns it. Images, PDF, text, Markdown, source and CSV are
  rendered here, with zoom, rotate, crop and mark up.
* **Tier 2** — a faithful, read-only preview, with a bar naming the application
  that really owns the file. Viewer never implies it can edit these.
* **Tier 3** — identified, not pretended. Everything known about the file, an
  honest sentence, and a way to open it elsewhere. Never a broken render.

Backends load lazily. Opening a JPEG must not pay for a document engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import mimetypes


TIER_ONE = 1
TIER_TWO = 2
TIER_THREE = 3


IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".jpe", ".gif", ".bmp", ".tif", ".tiff",
    ".webp", ".avif", ".heic", ".heif", ".svg", ".ico", ".ppm", ".pgm",
}
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".conf", ".cfg",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".js",
    ".py", ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".sh", ".rb", ".sql",
    ".java", ".kt", ".swift", ".lua", ".pl", ".vala", ".patch", ".diff",
}
TABLE_SUFFIXES = {".csv", ".tsv"}
OFFICE_OWNERS = {
    ".docx": ("Word document", "Write"), ".doc": ("Word document", "Write"),
    ".odt": ("OpenDocument text", "Write"), ".rtf": ("Rich text document", "Write"),
    ".xlsx": ("Excel workbook", "Grid"), ".xls": ("Excel workbook", "Grid"),
    ".ods": ("OpenDocument spreadsheet", "Grid"),
    ".pptx": ("PowerPoint presentation", "Stage"),
    ".odp": ("OpenDocument presentation", "Stage"),
    ".epub": ("EPUB book", "Leaf"),
}
MEDIA_SUFFIXES = {
    ".mp3": "Tide", ".flac": "Tide", ".wav": "Tide", ".ogg": "Tide", ".m4a": "Tide",
    ".mp4": "Reel", ".mkv": "Reel", ".webm": "Reel",
    ".mov": "Reel", ".avi": "Reel",
}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".xz", ".bz2", ".zst", ".7z", ".rar"}
# A PDF compressed on its own (report.pdf.gz) is still a PDF: shared-mime-info
# calls these application/x-gzpdf, x-bzpdf and x-xzpdf, and Viewer is their
# default, so it opens them rather than calling them archives.
COMPRESSED_PDF_SUFFIXES = {".gz": "gzip", ".bz2": "bzip2", ".xz": "xz"}
# The same ceiling a plain PDF has; a small file that inflates past it is
# refused rather than allowed to fill memory.
PDF_SESSION_LIMIT = 256 * 1024 * 1024


def pdf_compression(path: str | Path) -> str | None:
    """'gzip', 'bzip2' or 'xz' for name.pdf.gz and friends, else None."""
    target = Path(path)
    suffixes = [suffix.lower() for suffix in target.suffixes[-2:]]
    if len(suffixes) == 2 and suffixes[0] == ".pdf":
        return COMPRESSED_PDF_SUFFIXES.get(suffixes[1])
    return None


@dataclass
class FileFacts:
    """Everything Viewer can say about a file without rendering it."""

    path: Path
    tier: int
    kind: str
    icon: str = "text-x-generic-symbolic"
    owner: str = ""
    owner_note: str = ""
    size: int = 0
    created: datetime | None = None
    summary: str = ""
    dimensions: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def where(self) -> str:
        home = str(Path.home())
        parent = str(self.path.parent)
        return f"~{parent[len(home):]}" if parent.startswith(home) else parent


def inspect(path: str | Path) -> FileFacts:
    """Classify a file. Cheap: a stat and a suffix, never a full parse."""
    target = Path(path).expanduser()
    suffix = target.suffix.lower()
    guessed, encoding = mimetypes.guess_type(target.name)
    try:
        stat = target.stat()
        size, created = stat.st_size, datetime.fromtimestamp(stat.st_mtime)
    except OSError:
        size, created = 0, None

    if suffix == ".pdf" or guessed == "application/pdf":
        facts = FileFacts(target, TIER_ONE, "PDF document", "x-office-document-symbolic")
    elif pdf_compression(target):
        facts = FileFacts(target, TIER_ONE, "PDF document",
                          "x-office-document-symbolic")
    elif encoding and suffix in ARCHIVE_SUFFIXES:
        # notes.txt.gz guesses as text/plain with an encoding; its bytes are
        # compressed, so it is an archive, not text to print.
        facts = FileFacts(target, TIER_THREE, "Archive", "package-x-generic-symbolic")
        facts.owner_note = "Viewer cannot display an archive's contents."
    elif suffix in IMAGE_SUFFIXES or (guessed or "").startswith("image/"):
        facts = FileFacts(target, TIER_ONE, "Image", "image-x-generic-symbolic")
    elif suffix in TABLE_SUFFIXES:
        facts = FileFacts(target, TIER_ONE, "Table", "x-office-spreadsheet-symbolic")
    elif suffix in {'.obj', '.stl'}:
        facts = FileFacts(target, TIER_TWO, '3D model', 'package-x-generic-symbolic')
        facts.owner_note = 'Read-only model preview. Drag to rotate and scroll to zoom.'
    elif suffix in TEXT_SUFFIXES or (guessed or "").startswith("text/"):
        facts = FileFacts(target, TIER_ONE, "Text", "text-x-generic-symbolic")
    elif suffix in OFFICE_OWNERS:
        kind, owner = OFFICE_OWNERS[suffix]
        facts = FileFacts(target, TIER_TWO, kind, "x-office-document-symbolic", owner=owner)
        facts.owner_note = ("Read-only book preview. Open in Leaf to read, listen and annotate."
                            if suffix == '.epub' else f"This is a {kind.lower()}. Open in {owner} for editing.")
    elif suffix in MEDIA_SUFFIXES:
        owner = MEDIA_SUFFIXES[suffix]
        kind = "Audio" if owner == "Tide" else "Video"
        facts = FileFacts(target, TIER_TWO if kind == "Audio" else TIER_THREE, kind,
                          "audio-x-generic-symbolic" if kind == "Audio"
                          else "video-x-generic-symbolic", owner=owner)
        facts.owner_note = (f"Audio preview. Open in {owner} for your music library."
                            if kind == "Audio" else f"Viewer does not play {kind.lower()}. Open in {owner}.")
    elif suffix in ARCHIVE_SUFFIXES:
        facts = FileFacts(target, TIER_THREE, "Archive", "package-x-generic-symbolic")
        facts.owner_note = "Viewer cannot display an archive's contents."
    else:
        facts = FileFacts(target, TIER_THREE, "File", "text-x-generic-symbolic")
        facts.owner_note = "Viewer cannot display this kind of file."

    facts.size = size
    facts.created = created
    return facts


# ── Renderers ────────────────────────────────────────────────────────────
#
# Each returns what the stage needs and nothing more, and each is imported at
# the moment it is used so that showing a JPEG never loads a document engine.


def load_image(path: Path):
    """An image as a pixbuf, plus its real dimensions."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path)).apply_embedded_orientation()
    return pixbuf, (pixbuf.get_width(), pixbuf.get_height())


def image_thumbnail(path: Path, size: int = 76):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True).apply_embedded_orientation()


def open_pdf(path: Path):
    """The document handle; pages are rendered one at a time, on demand."""
    import gi

    gi.require_version("Poppler", "0.18")
    from gi.repository import Gio, Poppler

    return Poppler.Document.new_from_gfile(Gio.File.new_for_path(str(path)), None, None)


def read_pdf_bytes(path: Path, limit: int = PDF_SESSION_LIMIT) -> bytes:
    """The whole PDF, decompressed when it is a .pdf.gz, .pdf.bz2 or .pdf.xz.

    Capped at ``limit`` bytes after decompression, so an archive bomb is
    refused with the same message as an oversized PDF.
    """
    too_large = ValueError("This PDF exceeds Viewer's 256 MB session limit")
    compression = pdf_compression(path)
    if compression is None:
        if path.stat().st_size > limit:
            raise too_large
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    else:
        import bz2
        import gzip
        import lzma

        opener = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}[compression]
        try:
            with opener(path, "rb") as handle:
                data = handle.read(limit + 1)
        except (OSError, EOFError, lzma.LZMAError) as error:
            raise ValueError("This compressed PDF is damaged and cannot be read") from error
    if len(data) > limit:
        raise too_large
    return data


def pdf_page_surface(document, index: int, width: int):
    """One page, rendered to the width the stage has for it."""
    import cairo

    page = document.get_page(index)
    if page is None:
        return None, (0, 0)
    page_width, page_height = page.get_size()
    scale = max(width, 1) / max(page_width, 1)
    surface = cairo.ImageSurface(
        cairo.FORMAT_ARGB32, int(page_width * scale), int(page_height * scale)
    )
    context = cairo.Context(surface)
    context.set_source_rgb(1, 1, 1)
    context.paint()
    context.scale(scale, scale)
    page.render(context)
    return surface, (page_width, page_height)


def read_text(path: Path, limit: int = 400_000) -> str:
    """Text, capped. An enormous log opens to its first pages, not to a stall."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(limit)


def read_table(path: Path, limit: int = 500) -> tuple[list[str], list[list[str]]]:
    import csv

    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = []
        for index, row in enumerate(csv.reader(handle, delimiter=delimiter)):
            if index > limit:
                break
            rows.append(row)
    if not rows:
        return [], []
    return rows[0], rows[1:]
