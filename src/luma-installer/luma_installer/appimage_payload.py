# SPDX-License-Identifier: Apache-2.0
"""Where an AppImage keeps its application, found the way its runtime finds it.

An AppImage is a small ELF program (the runtime) followed by a filesystem
image. The runtime mounts the image that starts where its own ELF file ends:
section headers are the last thing in the ELF, so the payload begins at
e_shoff + e_shentsize * e_shnum. Searching the file for the SquashFS magic
instead finds the four bytes "hsqs" inside the runtime's own code in some
AppImages and fails with "Can't find a valid SQUASHFS superblock".

Payloads come in three kinds: SquashFS (almost all), DwarFS (newer uruntime
builds) and ISO 9660 (type 1, from before 2017). SquashFS is extracted
statically with unsquashfs; the others are extracted by the AppImage's own
runtime with --appimage-extract, inside a sandbox with no network and only a
scratch folder writable.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

from .errors import InstallerError

SQUASHFS = "squashfs"
DWARFS = "dwarfs"
ISO9660 = "iso9660"
SCAN_LIMIT = 32 * 1024 * 1024
SQUASHFS_COMPRESSORS = range(1, 7)  # gzip, lzma, lzo, xz, lz4, zstd


def elf_end(header: bytes) -> int | None:
    """The first byte after an ELF file's section header table."""
    if len(header) < 64 or header[:4] != b"\x7fELF":
        return None
    endian = "<" if header[5] == 1 else ">"
    if header[4] == 2:  # 64-bit
        shoff = struct.unpack_from(endian + "Q", header, 0x28)[0]
        shentsize, shnum = struct.unpack_from(endian + "HH", header, 0x3A)
    elif header[4] == 1:
        shoff = struct.unpack_from(endian + "I", header, 0x20)[0]
        shentsize, shnum = struct.unpack_from(endian + "HH", header, 0x2E)
    else:
        return None
    end = shoff + shentsize * shnum
    return end if end > 0 else None


def _squashfs_at(stream, offset: int, size: int) -> bool:
    """A real SquashFS 4 superblock, not four bytes that happen to spell hsqs."""
    stream.seek(offset)
    block = stream.read(96)
    if len(block) < 96 or block[:4] != b"hsqs":
        return False
    block_size = struct.unpack_from("<I", block, 12)[0]
    compressor, block_log = struct.unpack_from("<HH", block, 20)
    major = struct.unpack_from("<H", block, 28)[0]
    bytes_used = struct.unpack_from("<Q", block, 40)[0]
    return (major == 4 and compressor in SQUASHFS_COMPRESSORS and 12 <= block_log <= 20
            and block_size == 1 << block_log and 0 < bytes_used <= size - offset)


def locate(path: Path) -> tuple[str, int]:
    """The payload's kind and byte offset."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(64)
        end = elf_end(header)
        if end is not None and end < size:
            if _squashfs_at(stream, end, size):
                return SQUASHFS, end
            stream.seek(end)
            if stream.read(6) == b"DWARFS":
                return DWARFS, end
        # Runtimes that pad or append data after their sections: take the first
        # superblock that validates, never the first matching bytes.
        offset, remainder = 0, b""
        while offset < min(size, SCAN_LIMIT):
            stream.seek(offset)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            data = remainder + chunk
            base = offset - len(remainder)
            index = data.find(b"hsqs")
            while index >= 0:
                position = stream.tell()
                if _squashfs_at(stream, base + index, size):
                    return SQUASHFS, base + index
                stream.seek(position)
                index = data.find(b"hsqs", index + 1)
            offset += len(chunk)
            remainder = data[-3:]
        if len(header) >= 11 and header[8:11] == b"AI\x01":
            return ISO9660, 0
    if end is not None and end < size:
        return DWARFS if _dwarfs_anywhere(path) else ISO9660, end
    raise InstallerError("This AppImage doesn't contain an application Luma can read. "
                         "It may be damaged or only partly downloaded; download it again.")


def _dwarfs_anywhere(path: Path) -> bool:
    with path.open("rb") as stream:
        return b"DWARFS" in stream.read(SCAN_LIMIT)


def extract_with_runtime(staged: Path, destination: Path, *, timeout: int = 1800) -> None:
    """Let the AppImage's own runtime unpack a payload unsquashfs cannot read.

    The runtime runs with no network, no home and no session bus: only a
    scratch folder is writable, and it can only produce squashfs-root there.
    """
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    if bwrap is None:
        raise InstallerError("Luma's application sandbox (bubblewrap) is missing, so this AppImage can't be unpacked.")
    work = destination.parent / ".extract"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(mode=0o700, parents=True)
    runtime = work / "runtime.AppImage"
    shutil.copyfile(staged, runtime)
    runtime.chmod(0o700)
    arguments = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-all",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--bind", str(work), "/work", "--chdir", "/work", "--setenv", "HOME", "/work",
        "--", "/work/runtime.AppImage", "--appimage-extract",
    ]
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False,
                                env={"PATH": "/usr/bin:/bin", "LANG": os.environ.get("LANG", "C.UTF-8")})
        extracted = work / "squashfs-root"
        if result.returncode != 0 or not extracted.is_dir():
            raise InstallerError("This AppImage couldn't be unpacked. It may be damaged or only partly "
                                 "downloaded; download it again.")
        shutil.rmtree(destination, ignore_errors=True)
        extracted.replace(destination)
    except subprocess.TimeoutExpired as error:
        raise InstallerError("Unpacking this AppImage took too long and was stopped.") from error
    finally:
        shutil.rmtree(work, ignore_errors=True)
