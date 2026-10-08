#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Load real runtime modules exclusively from the installed package tree."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    modules = ("charlie_luma", "charlie_luma.window", "charlie_luma.application",
               "charlie_luma.html_reader", "charlie_luma.fixture_v71", "charlie_luma.mail_agent")
    for name in modules:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        if not path.is_relative_to(root):
            raise AssertionError(f"runtime import escaped installed tree: {name}: {path}")
        print(f"installed import PASS: {name}")


if __name__ == "__main__":
    main()
