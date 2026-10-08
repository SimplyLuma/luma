# SPDX-License-Identifier: Apache-2.0
"""A note as Markdown: for Export, and for anything else that wants plain text.

Headings, quotes, bulleted and numbered lists (nested), bold, italic, links
and pictures come across. Pictures are copied next to the exported file, in a
folder named after it, and linked relatively, so the export stands on its own.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from .notes_attachments import NAME_PATTERN, picture_path
from .notes_files import FILE_NAME, file_path
from .notes_backend import Note

OBJECT = "\ufffc"
_LEVELS = ("indent-1", "indent-2", "indent-3", "indent-4")


def _escape(text: str) -> str:
    for char in "\\`*_[]":
        text = text.replace(char, "\\" + char)
    return text


def to_markdown(note: Note, pictures_folder: str = "") -> tuple[str, tuple[str, ...]]:
    """The note's Markdown, and the picture names it refers to."""
    body = note.body
    length = len(body)
    styles: list[set[str]] = [set() for _ in range(length)]
    links: dict[int, str] = {}
    pictures: dict[int, tuple[str, bool, str]] = {}
    for run in note.runs:
        start, end, style = int(run.get("start", 0)), int(run.get("end", 0)), str(run.get("style", ""))
        if style == "image" and 0 <= start < length and NAME_PATTERN.match(str(run.get("src", ""))):
            pictures[start] = (str(run["src"]), True, '')
            continue
        if style == 'file' and 0 <= start < length and FILE_NAME.fullmatch(str(run.get('src', ''))):
            pictures[start] = (str(run['src']), False, str(run.get('name', 'Attachment')))
            continue
        for index in range(max(0, start), min(length, end)):
            styles[index].add(style)
            if style == "link":
                links[index] = str(run.get("href", ""))
    lines: list[str] = []
    used: list[str] = []
    counters = [0] * 5
    offset = 0
    for line in body.split("\n"):
        first = styles[offset] if offset < length else set()
        level = max((int(name[-1]) for name in first if name in _LEVELS), default=0)
        text = _inline(body, styles, links, pictures, offset, offset + len(line), pictures_folder, used)
        if "divider" in first:
            lines.append('---')
            offset += len(line) + 1
            continue
        if "checklist" in first:
            counters = [0] * 5
            prefix = '  ' * level + ('- [x] ' if 'checked' in first else '- [ ] ')
        elif "numbered" in first:
            for deeper in range(level + 1, 5):
                counters[deeper] = 0
            counters[level] += 1
            prefix = "   " * level + f"{counters[level]}. "
        elif "bulleted" in first:
            counters = [0] * 5
            prefix = "  " * level + "- "
        else:
            counters = [0] * 5
            prefix = "# " if "heading-1" in first and text.strip() else "## " if "heading" in first and text.strip() else "> " if "quote" in first else ""
        lines.append(prefix + text if text else prefix.rstrip())
        offset += len(line) + 1
    title = note.title.strip()
    document = (f"# {_escape(title)}\n\n" if title else "") + "\n".join(lines).rstrip("\n") + "\n"
    return document, tuple(used)


def _inline(body, styles, links, pictures, start, end, folder, used) -> str:
    out: list[str] = []
    index = start
    while index < end:
        if index in pictures:
            name, image, label = pictures[index]
            if name not in used:
                used.append(name)
            target = f"{folder}/{name}" if folder else name
            out.append(('!' if image else '') + f"[{_escape(label)}]({target.replace(' ', '%20')})")
            index += 1
            continue
        if body[index] == OBJECT:
            index += 1
            continue
        current = styles[index] & {"bold", "italic", "link", "strike", "highlight"}
        href = links.get(index)
        stop = index
        while (stop < end and stop not in pictures and body[stop] != OBJECT
               and styles[stop] & {"bold", "italic", "link", "strike", "highlight"} == current and links.get(stop) == href):
            stop += 1
        text = _escape(body[index:stop])
        stripped = text.strip()
        if stripped:
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            if "italic" in current:
                stripped = f"*{stripped}*"
            if "bold" in current:
                stripped = f"**{stripped}**"
            if "strike" in current:
                stripped = f"~~{stripped}~~"
            if "highlight" in current:
                stripped = f"<mark>{stripped}</mark>"
            if "link" in current and href:
                stripped = f"[{stripped}]({href})"
            text = lead + stripped + trail
        out.append(text)
        index = stop
    return "".join(out)


def export_markdown(note: Note, destination: Path) -> Path:
    """Write the note as Markdown, with its pictures beside it. Atomic per file."""
    destination = Path(destination)
    file_names = {run['src'] for run in note.runs if run.get('style') == 'file'}
    folder_name = f"{destination.stem} {'attachments' if file_names else 'pictures'}"
    document, used = to_markdown(note, folder_name)
    if used:
        folder = destination.parent / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        for name in used:
            source = file_path(name) if name in file_names else picture_path(name)
            if not source.exists():
                if name in file_names:
                    raise FileNotFoundError(f"Attachment unavailable: {name}")
                continue
            handle, temporary = tempfile.mkstemp(prefix=".attachment-", dir=folder)
            try:
                with os.fdopen(handle, "wb") as outgoing, source.open("rb") as incoming:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.chmod(temporary, 0o644)
                os.replace(temporary, folder / name)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
    handle, temporary = tempfile.mkstemp(prefix=".export-", dir=destination.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination


def export_open_in_write(note: Note, *, data_directory: Path | None = None) -> Path:
    """An independent private copy that outlives the Notes window.

    Write can edit this file. A later handoff gets a new directory and never
    overwrites an earlier copy or the library note. No receiver lifetime is
    assumed and completed copies are never automatically deleted.
    """
    root = Path(data_directory) if data_directory is not None else (
        Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
        / 'luma' / 'notes' / 'open-in-write')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='note-', dir=root))
    try:
        return export_markdown(note, directory / 'Note.md')
    except BaseException:
        # Only the incomplete directory just created by this call is removed.
        shutil.rmtree(directory, ignore_errors=True)
        raise
