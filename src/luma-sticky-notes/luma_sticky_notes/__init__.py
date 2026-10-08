# SPDX-License-Identifier: Apache-2.0
"""Sticky Notes: colour-coded notes docked against the screen edge."""

from .store import COLOURS, DEFAULT_COLOUR, Note, StickyNotesStore, data_directory

__all__ = [
    "COLOURS",
    "DEFAULT_COLOUR",
    "Note",
    "StickyNotesStore",
    "data_directory",
]

__version__ = "0.1.0"
