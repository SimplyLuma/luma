# SPDX-License-Identifier: Apache-2.0
"""Viewer: open a file, fast, and look at it."""

__all__ = ["main"]


def main() -> int:
    from .application import main as run

    return run()
