# SPDX-License-Identifier: Apache-2.0
"""Asynchronous, bounded local-source scanning for Tide."""
from __future__ import annotations

import concurrent.futures
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote, urlparse

from .metadata import CancelledScan, MetadataReader, default_reader
from .model import LibraryStore, SourceState

AUDIO_EXTENSIONS = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mp3",
    ".oga", ".ogg", ".opus", ".wav", ".wave", ".wma",
}


@dataclass(frozen=True, slots=True)
class ScanResult:
    source_id: str
    discovered: int
    indexed: int
    unchanged: int
    failed: int
    missing: int
    cancelled: bool
    errors: tuple[str, ...]


class LibraryIndexer:
    def __init__(
        self,
        store: LibraryStore,
        reader: MetadataReader | None = None,
        *,
        workers: int | None = None,
    ) -> None:
        self.store = store
        self.reader = reader or default_reader()
        worker_count = workers or min(4, max(1, (os.cpu_count() or 2) // 2))
        self._worker_count = worker_count
        self._workers = concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="tide-metadata"
        )
        self._coordinator = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tide-scan"
        )
        self._cancel = threading.Event()

    def close(self) -> None:
        self.cancel()
        self._coordinator.shutdown(wait=True, cancel_futures=True)
        self._workers.shutdown(wait=True, cancel_futures=True)

    def cancel(self) -> None:
        self._cancel.set()

    def scan_async(
        self,
        source_id: str,
        progress: Callable[[int, int], None] | None = None,
        *,
        refresh_paths: Iterable[Path] = (),
        force: bool = False,
    ) -> concurrent.futures.Future[ScanResult]:
        """Scan one local source. Ordinarily a file whose size and mtime
        haven't changed since the last scan is left alone (`unchanged_copy`)
        rather than re-read. `force=True` skips that fast path and re-reads
        every file's tags regardless — used for the one-time re-index after
        a schema migration that recovers structured artist data the old
        reader used to throw away, since an on-disk file that hasn't
        changed still needs to be re-read with the fixed code to recover
        what was already lost before it ever reached the database."""
        self._cancel = threading.Event()
        return self._coordinator.submit(
            self._scan,
            source_id,
            progress,
            frozenset(path.resolve() for path in refresh_paths),
            force,
        )

    @staticmethod
    def _paths(root: Path) -> Iterable[Path]:
        for directory, names, files in os.walk(root, followlinks=False):
            names[:] = [name for name in names if not name.startswith(".")]
            for name in files:
                path = Path(directory, name)
                if path.suffix.casefold() in AUDIO_EXTENSIONS and not path.is_symlink():
                    yield path

    def _scan(
        self,
        source_id: str,
        progress: Callable[[int, int], None] | None,
        refresh_paths: frozenset[Path] = frozenset(),
        force: bool = False,
    ) -> ScanResult:
        source = self.store.source(source_id)
        parsed = urlparse(source.uri)
        if not source.local or parsed.scheme != "file":
            raise ValueError("the built-in indexer only scans local file sources")
        root = Path(unquote(parsed.path))
        if not root.is_dir():
            self.store.set_source_state(source_id, SourceState.OFFLINE)
            return ScanResult(source_id, 0, 0, 0, 0, 0, False, (f"Source is unavailable: {root}",))
        self.store.set_source_state(source_id, SourceState.SYNCING)
        paths = list(self._paths(root))
        unchanged = 0
        seen: set[str] = set()
        changed: list[Path] = []
        for path in paths:
            if force:
                changed.append(path)
                continue
            try:
                stat = path.stat()
            except OSError:
                changed.append(path)
                continue
            copy_id = self.store.unchanged_copy(
                source_id, path, size=stat.st_size, mtime_ns=stat.st_mtime_ns
            )
            # Explicitly opened files reread tags even when bytes are unchanged.
            # Ordinary source/monitor scans retain their size/mtime fast path.
            if copy_id is None or path.resolve() in refresh_paths:
                changed.append(path)
            else:
                seen.add(copy_id)
                unchanged += 1
        path_iterator = iter(changed)
        futures: dict[concurrent.futures.Future, Path] = {}

        def fill() -> None:
            while len(futures) < self._worker_count * 2:
                try:
                    path = next(path_iterator)
                except StopIteration:
                    return
                futures[self._workers.submit(self.reader.read, path, self._cancel)] = path

        fill()
        indexed = 0
        failed = 0
        errors: list[str] = []
        completed = 0
        while futures:
            completed_set, _pending = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )
            future = completed_set.pop()
            path = futures.pop(future)
            if self._cancel.is_set():
                break
            try:
                metadata = future.result()
                _track, copy_id = self.store.upsert_copy(source_id, metadata)
                seen.add(copy_id)
                indexed += 1
            except CancelledScan:
                self._cancel.set()
                break
            except Exception as error:  # one corrupt track must not abort a library
                failed += 1
                if len(errors) < 20:
                    errors.append(f"{path.name}: {error}")
            completed += 1
            if progress is not None:
                progress(completed + unchanged, len(paths))
            fill()
        cancelled = self._cancel.is_set()
        for future in futures:
            if not future.done():
                future.cancel()
        missing = 0
        if not cancelled:
            missing = self.store.finish_source_scan(source_id, seen)
        else:
            self.store.set_source_state(source_id, SourceState.ONLINE)
        return ScanResult(
            source_id=source_id,
            discovered=len(paths),
            indexed=indexed,
            unchanged=unchanged,
            failed=failed,
            missing=missing,
            cancelled=cancelled,
            errors=tuple(errors),
        )
