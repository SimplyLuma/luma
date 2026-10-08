from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

from .errors import RelayError


WINE_RUNTIME_ROOTS = (
    Path("/usr/lib64/wine-wow64/wine"),
    Path("/usr/lib/wine-wow64/wine"),
    Path("/usr/lib64/wine"),
    Path("/usr/lib/wine"),
)

BRIDGE_FILES = (
    ("x86_64-windows/luma_relay.dll", "drive_c/windows/system32/luma_relay.dll"),
    ("i386-windows/luma_relay.dll", "drive_c/windows/syswow64/luma_relay.dll"),
)


def wine_runtime_root() -> Path | None:
    for root in WINE_RUNTIME_ROOTS:
        if root.is_dir():
            return root
    return None


def relay_bridge_root() -> Path | None:
    for root in WINE_RUNTIME_ROOTS:
        if all((root / relative).is_file() for relative in (
            "x86_64-windows/luma_relay.dll",
            "i386-windows/luma_relay.dll",
            "x86_64-unix/luma_relay.so",
        )):
            return root
    return None


def native_notification_files_available() -> bool:
    explorer_root = Path("/usr/libexec/luma-relay/wine")
    if not all(
        (explorer_root / architecture / "explorer.exe").is_file()
        for architecture in ("x86_64-windows", "i386-windows")
    ):
        return False
    return relay_bridge_root() is not None


def _safe_parent(capsule: Path, relative: str) -> Path:
    capsule = capsule.absolute()
    parent = capsule / "prefix" / Path(relative).parent
    if not parent.is_dir() or parent.is_symlink():
        raise RelayError("Relay's private Windows environment is not structurally valid.")
    try:
        parent.resolve(strict=True).relative_to(capsule.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise RelayError("Relay refused an unsafe Windows environment path.") from error
    return parent


def _atomic_trusted_copy(source: Path, destination: Path, capsule: Path) -> None:
    source_stat = source.stat()
    if not stat.S_ISREG(source_stat.st_mode) or source.is_symlink():
        raise RelayError("Relay's native notification component is not a regular file.")
    parent = _safe_parent(capsule, str(destination.relative_to(capsule / "prefix")))
    input_fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=".luma-relay-", dir=parent)
    except Exception:
        os.close(input_fd)
        raise
    temporary = Path(temporary_name)
    try:
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(input_fd, "rb", closefd=True) as input_stream:
            with os.fdopen(temporary_fd, "wb", closefd=True) as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        try:
            os.close(input_fd)
        except OSError:
            pass
        try:
            os.close(temporary_fd)
        except OSError:
            pass
        raise


def sync_relay_bridge(capsule: Path) -> list[tuple[Path, Path]]:
    """Refresh trusted PE bridge files and return host-to-sandbox bindings."""
    root = relay_bridge_root()
    if root is None or not native_notification_files_available():
        return []
    bindings: list[tuple[Path, Path]] = []
    for source_relative, prefix_relative in BRIDGE_FILES:
        source = root / source_relative
        destination = capsule / "prefix" / prefix_relative
        _atomic_trusted_copy(source, destination, capsule)
        bindings.append((source, Path("/relay/prefix") / prefix_relative))
    return bindings


def relay_bridge_bindings(capsule: Path) -> list[tuple[Path, Path]]:
    root = relay_bridge_root()
    if root is None:
        return []
    bindings: list[tuple[Path, Path]] = []
    for source_relative, prefix_relative in BRIDGE_FILES:
        source = root / source_relative
        destination = capsule / "prefix" / prefix_relative
        if not destination.is_file() or destination.is_symlink():
            return []
        bindings.append((source, Path("/relay/prefix") / prefix_relative))
    return bindings
