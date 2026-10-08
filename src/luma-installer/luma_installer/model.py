from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PackageReport:
    path: Path
    kind: str
    title: str
    summary: str
    byte_size: int
    sha256: str
    architecture: str = "unknown"
    identity: str = "Unverified local package"
    destination: str = "Applications"
    permissions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, str] = field(default_factory=dict)
    requires_system_change: bool = False
    requires_restart: bool = False
    verified: bool = False
    version: str = ""
    publisher: str = ""
    icon: str = "application-x-executable"


SUPPORTED_SUFFIXES = {
    ".apk": "android",
    ".apks": "android",
    ".xapk": "android",
    ".apkm": "android",
    ".exe": "windows",
    ".msi": "windows",
    ".rpm": "rpm",
    ".deb": "deb",
    ".appimage": "appimage",
    ".flatpak": "flatpak",
    ".flatpakref": "flatpakref",
    ".flatpakrepo": "flatpakrepo",
    ".snap": "snap",
}

SUPPORTED_CONTENT_TYPES = {
    "application/vnd.android.package-archive",
    "application/vnd.projectluma.android-package-set",
    "application/x-ms-dos-executable",
    "application/x-msdownload",
    "application/x-msi",
    "application/vnd.microsoft.portable-executable",
    "application/x-rpm",
    "application/vnd.debian.binary-package",
    "application/vnd.appimage",
    "application/vnd.flatpak",
    "application/vnd.flatpak.ref",
    "application/vnd.flatpak.repo",
    "application/vnd.snap",
}


def kind_for_path(path: Path) -> str | None:
    """The package format, from the name alone.

    Applications released as a compressed folder (.tar.gz, .zip, ...) and the
    folder a download manager already unpacked are both "portable": the
    inspector decides from the contents whether there is an application in it.
    """
    from .archive_app import is_archive
    if is_archive(path) or path.is_dir():
        return "portable"
    return SUPPORTED_SUFFIXES.get(path.suffix.lower())


def supports_content_type(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() in SUPPORTED_CONTENT_TYPES
