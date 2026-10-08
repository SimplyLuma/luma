# SPDX-License-Identifier: Apache-2.0
"""Kernel arguments a release declares in ``/usr/lib/bootc/kargs.d``.

bootc applies these when it installs or updates a system from a container
image; rpm-ostree does not when it deploys an OSTree ref, which is how
luma-update stages releases. The agent therefore reads the staged release's
files and asks rpm-ostree to add what is missing (see Engine and
docs/os/luma-update.md, "Kernel arguments").

The format is bootc's (``bootc/lib/src/kargs.rs``): every ``*.toml`` in the
directory, in file-name order, with ``kargs``, a list of arguments, and
optionally ``match-architectures``, a list of architecture names in Rust's
spelling (``x86_64``, ``aarch64``) that limits the file to those machines.
A file that cannot be parsed makes the whole release's arguments unknown: the
update is not staged rather than booted without them.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

__all__ = ("KARGS_DIR", "KargsError", "declared")

KARGS_DIR = Path("usr/lib/bootc/kargs.d")
MAX_FILE_BYTES = 64 * 1024


class KargsError(Exception):
    error_class = "transaction"


def _usable(argument) -> bool:
    return (isinstance(argument, str) and bool(argument)
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in argument))


def declared(root: Path, arch: str) -> list[str]:
    """The arguments the tree at ``root`` declares for ``arch``, in order, without repeats."""
    directory = Path(root) / KARGS_DIR
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise KargsError(f"{KARGS_DIR} is not a directory")
    wanted: list[str] = []
    for path in sorted(directory.glob("*.toml")):
        name = path.name
        try:
            data = path.read_bytes()
        except OSError as error:
            raise KargsError(f"kargs.d/{name} cannot be read: {error}") from None
        if len(data) > MAX_FILE_BYTES:
            raise KargsError(f"kargs.d/{name} is larger than {MAX_FILE_BYTES} bytes")
        try:
            document = tomllib.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise KargsError(f"kargs.d/{name} is not valid TOML: {error}") from None
        arches = document.get("match-architectures")
        if arches is not None:
            if not isinstance(arches, list) or not all(isinstance(item, str) for item in arches):
                raise KargsError(f"kargs.d/{name}: match-architectures must be a list of strings")
            if arch not in arches:
                continue
        arguments = document.get("kargs", [])
        if not isinstance(arguments, list):
            raise KargsError(f"kargs.d/{name}: kargs must be a list of strings")
        for argument in arguments:
            if not _usable(argument):
                raise KargsError(f"kargs.d/{name}: unusable kernel argument {argument!r}")
            if argument not in wanted:
                wanted.append(argument)
    return wanted
