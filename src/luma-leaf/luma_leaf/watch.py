# SPDX-License-Identifier: Apache-2.0
"""Noticing that a folder Leaf reads has changed.

A GIO directory monitor tells Leaf when a book arrives in ~/Books or when Luma
Connect writes reading from another device into the library. Creating one can
fail: inotify limits the instances and watches each user may hold, and some
file systems cannot be watched at all. Leaf must still start and show the
library then. It says so once, and looks at the folder again whenever its
window becomes active or the library is shown, comparing names, sizes and
modification times so an unchanged folder costs a listing rather than a
rescan. No timer runs, whether the monitor works or not.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger("leaf")

Fingerprint = frozenset[tuple[str, int, int]]


class FolderWatch:
    """Calls ``changed`` when the folder's contents change."""

    def __init__(self, path: Path, changed: Callable[[], object], *,
                 flags: Gio.FileMonitorFlags = Gio.FileMonitorFlags.NONE, recursive: bool = False) -> None:
        self.path = Path(path)
        self.changed = changed
        self.recursive = recursive
        self.monitor: Gio.FileMonitor | None = None
        self._fingerprint: Fingerprint | None = None
        try:
            self.monitor = Gio.File.new_for_path(str(self.path)).monitor_directory(flags, None)
        except GLib.Error as error:
            log.warning("Leaf cannot watch %s for changes (%s); it will look for changes whenever its "
                        "window becomes active or the library is shown.", self.path, error.message)
            self._fingerprint = self._take_fingerprint()
        else:
            self.monitor.connect("changed", self._monitor_changed)

    @property
    def watching(self) -> bool:
        return self.monitor is not None

    def check(self) -> bool:
        """Without a monitor, report a change made since the last look.

        Returns whether ``changed`` was called. With a working monitor this
        does nothing: the monitor has already reported every change.
        """
        if self.monitor is not None:
            return False
        current = self._take_fingerprint()
        if current == self._fingerprint:
            return False
        self._fingerprint = current
        self.changed()
        return True

    def cancel(self) -> None:
        if self.monitor is not None:
            self.monitor.cancel()

    def _monitor_changed(self, *_args) -> None:
        self.changed()

    def _take_fingerprint(self) -> Fingerprint:
        entries: set[tuple[str, int, int]] = set()

        def add(path: str) -> None:
            try:
                status = os.stat(path)
            except OSError:
                return
            entries.add((os.path.relpath(path, self.path), status.st_size, status.st_mtime_ns))

        try:
            if self.recursive:
                # The same tree the importer reads: hidden folders are skipped.
                for directory, names, files in os.walk(self.path):
                    names[:] = [name for name in names if not name.startswith(".")]
                    for name in names + files:
                        add(os.path.join(directory, name))
            else:
                with os.scandir(self.path) as found:
                    for entry in found:
                        add(entry.path)
        except OSError:
            pass
        return frozenset(entries)
