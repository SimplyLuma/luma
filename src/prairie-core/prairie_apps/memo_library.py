# SPDX-License-Identifier: Apache-2.0
"""Ordinary recording files and bounded, recoverable sidecar metadata.

The UI and media pipeline share this module. It does not open a microphone,
start a model, or keep a service alive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import uuid


FORMATS = frozenset({'.ogg', '.opus', '.flac', '.wav', '.m4a'})
MAX_SIDECAR_BYTES = 8 * 1024 * 1024


def atomic_json(path: Path, value: object) -> None:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_SIDECAR_BYTES:
        raise ValueError('Recording metadata is too large.')
    fd, temporary = tempfile.mkstemp(prefix='.memo-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIDECAR_BYTES:
                return {}
            raw = stream.read(MAX_SIDECAR_BYTES + 1)
        if len(raw) > MAX_SIDECAR_BYTES:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def sidecar(path: Path) -> Path:
    return path.with_name(path.name + '.memo.json')


@dataclass(frozen=True)
class Recording:
    path: Path
    title: str
    modified: float
    size: int
    duration: float | None
    source: str
    transcript: tuple[dict, ...]
    transcript_state: str

    def match(self, query: str) -> str | None:
        needle = query.casefold().strip()
        if not needle or needle in self.title.casefold():
            return ''
        for cue in self.transcript:
            text = cue['text']
            if needle in text.casefold():
                return text
        return None


def validated_cues(value: object) -> tuple[dict, ...]:
    if not isinstance(value, list):
        return ()
    result = []
    previous = -1.0
    for cue in value[:50000]:
        if not isinstance(cue, dict):
            continue
        try:
            start, end = float(cue['start']), float(cue['end'])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        text = cue.get('text')
        if (not isinstance(text, str) or len(text) > 10000 or
                not math.isfinite(start) or not math.isfinite(end) or
                start < 0 or end < start or start < previous):
            continue
        result.append({'start': start, 'end': end, 'text': text})
        previous = start
    return tuple(result)


class RecordingLibrary:
    def __init__(self, root: Path | None = None, legacy: Path | None = None):
        self.root = root or Path.home() / 'Recordings'
        self.legacy = legacy or Path.home() / 'Music/Voice Memos'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.deleted = self.root / '.Recently Deleted'

    def checked(self, path: Path) -> Path:
        path = Path(path)
        if path.is_symlink() or path.suffix.lower() not in FORMATS or not path.is_file():
            raise ValueError('This is not a recording file.')
        parent = path.parent.resolve()
        if parent not in {self.root.resolve(), self.legacy.resolve()}:
            raise ValueError('This recording is outside the library.')
        return path

    def recordings(self) -> tuple[Recording, ...]:
        result = []
        seen = set()
        for root in (self.root, self.legacy):
            if not root.is_dir() or root.resolve() in seen:
                continue
            seen.add(root.resolve())
            for path in root.iterdir():
                if path.is_symlink() or path.suffix.lower() not in FORMATS or not path.is_file():
                    continue
                info = path.stat()
                meta = read_json(sidecar(path))
                duration = meta.get('duration')
                if (not isinstance(duration, (int, float)) or
                        not math.isfinite(duration) or duration < 0):
                    duration = None
                title = meta.get('title')
                source = meta.get('source')
                result.append(Recording(path, title if isinstance(title,str) and title else path.stem,
                    info.st_mtime, info.st_size, duration,
                    source if isinstance(source,str) else '',
                    validated_cues(meta.get('transcript')),
                    str(meta.get('transcript_state','unavailable'))))
        return tuple(sorted(result, key=lambda r: (-r.modified, r.path.name)))

    def update(self, path: Path, **changes) -> None:
        path = self.checked(path)
        meta = read_json(sidecar(path))
        meta.update(changes)
        meta['version'] = 1
        atomic_json(sidecar(path), meta)

    def rename(self, path: Path, title: str) -> None:
        # Metadata title avoids a multi-file rename transaction and keeps links
        # from Filer/export stable. The ordinary audio filename stays intact.
        self.update(path, title=title.strip()[:500] or path.stem)

    def trash(self, path: Path) -> str:
        path = self.checked(path)
        if self.deleted.is_symlink():
            raise ValueError('Recently Deleted is not a private recording directory.')
        self.deleted.mkdir(mode=0o700, exist_ok=True)
        identifier = uuid.uuid4().hex
        bundle = self.deleted / identifier
        bundle.mkdir(mode=0o700)
        atomic_json(bundle/'entry.json', {'original': str(path.absolute()),
            'deleted_at': datetime.now(timezone.utc).timestamp(), 'state': 'moving'})
        moved = []
        try:
            for source in (path, sidecar(path), path.with_name(path.name+'.peaks.json')):
                if source.is_file() and not source.is_symlink():
                    destination = bundle/source.name
                    source.rename(destination)
                    moved.append((destination,source))
            atomic_json(bundle/'entry.json', {'original': str(path.absolute()),
                'deleted_at': datetime.now(timezone.utc).timestamp(), 'state': 'deleted'})
        except OSError:
            for source,destination in reversed(moved):
                source.rename(destination)
            raise
        return identifier

    def restore(self, identifier: str) -> Path:
        if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Invalid deleted recording.')
        bundle = self.deleted/identifier
        if bundle.is_symlink():
            raise ValueError('Invalid deleted recording.')
        entry = read_json(bundle/'entry.json')
        target = Path(str(entry.get('original','')))
        if target.parent.resolve() not in {self.root.resolve(), self.legacy.resolve()}:
            raise ValueError('Original recording location is unavailable.')
        if target.suffix.lower() not in FORMATS:
            raise ValueError('Invalid recording format.')
        pairs = [(bundle/name,target.with_name(name)) for name in
                 (target.name,target.name+'.memo.json',target.name+'.peaks.json')]
        if any(dst.exists() or dst.is_symlink() for _,dst in pairs):
            raise FileExistsError('A file already exists at the original location.')
        if not pairs[0][0].is_file() or pairs[0][0].is_symlink():
            raise ValueError('The deleted recording is unavailable.')
        moved = []
        try:
            for source,destination in pairs:
                if source.is_file() and not source.is_symlink():
                    source.rename(destination)
                    moved.append((destination,source))
        except OSError:
            for source,destination in reversed(moved):
                source.rename(destination)
            raise
        (bundle/'entry.json').unlink()
        bundle.rmdir()
        return target


def reduce_peaks(peaks: list[float], columns: int) -> tuple[float, ...]:
    """Stable max-envelope reduction. Never generate a decorative waveform."""
    if columns < 1 or not peaks:
        return ()
    values = [min(1.0, max(0.0, p)) if math.isfinite(p) else 0.0 for p in peaks]
    return tuple(max(values[i*len(values)//columns:
                            max(i*len(values)//columns+1,(i+1)*len(values)//columns)])
                 for i in range(columns))
