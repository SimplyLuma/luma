# SPDX-License-Identifier: Apache-2.0
"""Locate the same packaged Leaf assets under native and sandbox prefixes."""
from pathlib import Path
import os
import sys


def data_directory() -> Path:
    explicit = os.environ.get("LUMA_LEAF_DATA_DIR")
    if explicit:
        base = Path(explicit)
        if not base.is_absolute() or not base.is_dir():
            raise FileNotFoundError("Leaf's configured data directory is unavailable")
        return base
    source = Path(__file__).resolve().parents[1] / "data"
    if (source / "reader" / "reader.html").is_file():
        return source
    return Path(sys.prefix) / "share" / "leaf"
