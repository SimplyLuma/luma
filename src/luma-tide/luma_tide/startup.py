# SPDX-License-Identifier: Apache-2.0
"""The riskiest part of Tide's startup, factored out of `application.py`.

`application.py` imports `gi` unconditionally at module scope (it needs
Adw/Gtk to exist just to be imported at all), so nothing that runs without a
real GTK/Adw stack available — such as this codebase's plain `unittest`
suite — can import it, let alone exercise `TideApplication.do_startup`
directly. The two steps here are exactly the ones a real production
incident showed can fail silently (see the `2.luma.19` changelog / commit
history for the full incident writeup): opening and migrating the library
database. Importing a dev-preview library lives in `preview_import.py`. Both are pure
`LibraryStore`/`logging` code with no GTK dependency of their own, so
pulling their error handling out here makes it directly unit-testable.

Both helpers take the logger to use explicitly rather than importing one of
their own, so a caller (`application.py`'s `do_startup`, or a test) controls
exactly where the log records go.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .model import LibraryStore


def open_library_store(path: str | Path | None = None, *, log: logging.Logger) -> LibraryStore:
    """Construct the real `LibraryStore`, which runs `_migrate()`
    unconditionally as part of `__init__` and therefore cannot be
    constructed at all if that raises: an unreadable/corrupt database file,
    a schema newer than this build supports, or a filesystem permission
    problem all surface here. There is nothing safe to fall back to short of
    a library-less player, so this logs the full traceback (so the failure
    is never silent, unlike the field incident that motivated this module)
    and re-raises — the caller still lets startup fail, just never quietly."""
    try:
        return LibraryStore(path)
    except Exception:
        log.exception("Tide could not open or migrate its library database; cannot start")
        raise
