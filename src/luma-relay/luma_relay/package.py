from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import RelayError


PE_MACHINES = {
    0x014C: "x86",
    0x8664: "x86_64",
    0x01C4: "armv7",
    0xAA64: "arm64",
}
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
INSTALLER_WORDS = ("setup", "install", "installer", "bootstrap", "deploy")


@dataclass(frozen=True)
class WindowsPackage:
    path: str
    filename: str
    byte_size: int
    sha256: str
    package_format: str
    processor: str
    suggested_action: str
    authenticode_present: bool
    dotnet_metadata_present: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _pe_metadata(fd: int, size: int) -> tuple[str, bool]:
    if size < 64 or os.pread(fd, 2, 0) != b"MZ":
        raise RelayError("This file is not a valid Windows executable.")
    pe_offset = struct.unpack("<I", os.pread(fd, 4, 0x3C))[0]
    if pe_offset < 64 or pe_offset + 24 > size:
        raise RelayError("The Windows executable header is malformed.")
    header = os.pread(fd, 24, pe_offset)
    if header[:4] != b"PE\0\0":
        raise RelayError("This file does not contain a valid Windows PE header.")
    machine = PE_MACHINES.get(struct.unpack("<H", header[4:6])[0], "unknown")
    optional_size = struct.unpack("<H", header[20:22])[0]
    optional = os.pread(fd, optional_size, pe_offset + 24)
    signed = False
    if len(optional) >= 136:
        magic = struct.unpack("<H", optional[:2])[0]
        directories = 96 if magic == 0x10B else 112 if magic == 0x20B else -1
        security = directories + 8 * 4
        if directories >= 0 and security + 8 <= len(optional):
            certificate_offset, certificate_size = struct.unpack(
                "<II", optional[security : security + 8]
            )
            signed = (
                certificate_offset > 0
                and certificate_size >= 8
                and certificate_offset + certificate_size <= size
            )
    return machine, signed


def inspect_windows_package(path: Path, max_bytes: int) -> WindowsPackage:
    path = path.expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise RelayError(f"The package could not be opened safely: {error.strerror}.") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RelayError("The package must be a regular local file.")
        if info.st_size <= 0:
            raise RelayError("The package is empty.")
        if info.st_size > max_bytes:
            raise RelayError("The Windows package exceeds Relay's configured size limit.")

        first = os.pread(fd, 64, 0)
        suffix = path.suffix.lower()
        if first.startswith(OLE_MAGIC) and suffix == ".msi":
            package_format = "msi"
            processor = "determined by Windows Installer"
            signed = False
        else:
            processor, signed = _pe_metadata(fd, info.st_size)
            package_format = "exe"

        digest = hashlib.sha256()
        dotnet = False
        overlap = b""
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            probe = (overlap + block).lower()
            if b"mscoree.dll" in probe or b"bsjb" in probe:
                dotnet = True
            overlap = block[-32:]

        stem = path.stem.lower()
        action = "install" if package_format == "msi" or any(
            word in stem for word in INSTALLER_WORDS
        ) else "open"
        return WindowsPackage(
            path=str(path),
            filename=path.name,
            byte_size=info.st_size,
            sha256=digest.hexdigest(),
            package_format=package_format,
            processor=processor,
            suggested_action=action,
            authenticode_present=signed,
            dotnet_metadata_present=dotnet,
        )
    finally:
        os.close(fd)


def host_supports(package: WindowsPackage, host_machine: str, *, fex_available: bool = False) -> tuple[bool, str]:
    if package.package_format == "msi":
        return True, "Windows Installer will validate the package architecture."
    if host_machine == "x86_64" and package.processor in {"x86", "x86_64"}:
        return True, "Runs through Wine 11 WoW64 without CPU emulation."
    if host_machine in {"aarch64", "arm64"} and fex_available:
        if package.processor in {"x86", "x86_64"}:
            return True, "Uses the optional FEX and Wine ARM preview; performance and power use vary."
        return False, "This FEX backend runs x86 Windows applications, not ARM64 Windows executables."
    if host_machine in {"aarch64", "arm64"} and package.processor == "arm64":
        return True, "Uses the native ARM64 Windows ABI when the runner is available."
    if package.processor == "unknown":
        return False, "Relay could not identify this executable's processor architecture."
    return False, (
        f"This {package.processor} Windows application needs a Relay processor "
        f"backend that is not available on this {host_machine} device."
    )
