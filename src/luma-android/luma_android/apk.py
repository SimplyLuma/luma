from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import ApkValidationError


MAX_ZIP_MEMBERS = 100_000
MAX_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
SUPPORTED_PACKAGE_SUFFIXES = frozenset({".apk", ".apks", ".xapk", ".apkm"})
SUPPORTED_PACKAGE_MIME_TYPES = frozenset(
    {
        "application/vnd.android.package-archive",
        "application/vnd.projectluma.android-package-set",
    }
)
ANDROID_NATIVE_ABIS = frozenset(
    {
        "arm64-v8a",
        "armeabi-v7a",
        "armeabi",
        "x86_64",
        "x86",
        "mips64",
        "mips",
    }
)
ABI_LABELS = {
    "arm64-v8a": "ARM64",
    "armeabi-v7a": "32-bit ARM",
    "armeabi": "legacy ARM",
    "x86_64": "Intel/AMD 64-bit",
    "x86": "Intel/AMD 32-bit",
}


def architecture_notice(abis: tuple[str, ...], machine: str | None = None) -> str:
    """Explain a foreign native build without guessing runtime translation.

    The runtime's advertised ABIs remain the installation authority. The host
    family is useful earlier, while the package is being inspected, including
    when Android has not started yet.
    """
    machine = (platform.machine() if machine is None else machine).lower()
    families = {
        "x86_64": ({"x86", "x86_64"}, "x86 (Intel/AMD)"),
        "amd64": ({"x86", "x86_64"}, "x86 (Intel/AMD)"),
        "aarch64": ({"arm64-v8a", "armeabi-v7a", "armeabi"}, "ARM"),
        "arm64": ({"arm64-v8a", "armeabi-v7a", "armeabi"}, "ARM"),
    }
    if not abis or machine not in families:
        return ""
    native, host = families[machine]
    if native.intersection(abis):
        return ""
    choices = ", ".join(ABI_LABELS.get(abi, abi) for abi in abis)
    return (
        f"This computer uses {host}. This package contains {choices} native code. "
        f"Choose an {host} or universal version from the publisher when available. "
        "Installation can continue only if Android supports the package’s architecture."
    )


def processor_summary(abis: tuple[str, ...]) -> str:
    if not abis:
        return (
            "Universal or architecture-neutral package. This is preferred "
            "when the publisher offers it."
        )
    machine = platform.machine().lower()
    native = {
        "aarch64": "arm64-v8a",
        "arm64": "arm64-v8a",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    labels = ", ".join(ABI_LABELS.get(abi, abi) for abi in abis)
    if native in abis:
        return (
            f"Native match for this device ({ABI_LABELS.get(native, native)}). "
            f"Package choices: {labels}; Luma selects the native set."
        )
    host = ABI_LABELS.get(native, machine or "this device")
    return (
        f"Package choices: {labels}. This {host} device needs a matching or "
        "universal build; Luma will reject incompatible native code."
    )


def local_apk_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ApkValidationError("Only a local APK file can be installed.")
    path = Path(unquote(parsed.path))
    if path.suffix.lower() not in SUPPORTED_PACKAGE_SUFFIXES:
        raise ApkValidationError(
            "This installer accepts APK, APKS, XAPK, and APKM Android packages."
        )
    return path


def supports_content_type(content_type: str) -> bool:
    return content_type in SUPPORTED_PACKAGE_MIME_TYPES


def native_abis_for_apk(path: Path) -> tuple[str, ...]:
    """Return the native-library ABIs carried by one validated APK.

    Android packages without ``lib/<abi>/`` members are architecture-neutral.
    Reading the ZIP directory is sufficient here: Android remains the authority
    for package identity, signatures, and the final installation transaction.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            abis = _native_abis_from_members(archive.infolist())
    except (OSError, zipfile.BadZipFile) as error:
        raise ApkValidationError("The selected file is not a valid APK archive.") from error
    return tuple(sorted(abis))


@dataclass(frozen=True)
class ApkInspection:
    source_path: str
    display_name: str
    byte_size: int
    sha256: str
    zip_members: int
    expanded_bytes: int
    has_manifest: bool
    has_v1_signature_files: bool
    source_origin: str
    package_format: str = "apk"
    apk_entries: tuple[str, ...] = ()
    native_abis: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _origin_for(path: Path) -> str:
    downloads = Path.home() / "Downloads"
    try:
        path.resolve().relative_to(downloads.resolve())
        return "Downloads"
    except ValueError:
        return "Local file"


def inspect_apk(path: Path, max_bytes: int) -> ApkInspection:
    path = path.expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise ApkValidationError(f"Cannot safely open the APK: {error.strerror}") from error

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ApkValidationError("The selected APK is not a regular file.")
        if info.st_size <= 0:
            raise ApkValidationError("The selected APK is empty.")
        if info.st_size > max_bytes:
            raise ApkValidationError(
                f"The APK is larger than the configured {max_bytes} byte limit."
            )

        digest = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb", closefd=True) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)

        with os.fdopen(os.dup(fd), "rb", closefd=True) as stream:
            try:
                with zipfile.ZipFile(stream) as archive:
                    members = archive.infolist()
                    if len(members) > MAX_ZIP_MEMBERS:
                        raise ApkValidationError("The APK contains an unreasonable number of files.")
                    expanded = sum(member.file_size for member in members)
                    if expanded > MAX_EXPANDED_BYTES:
                        raise ApkValidationError("The APK's expanded content is unreasonably large.")
                    names = {member.filename for member in members}
            except zipfile.BadZipFile as error:
                raise ApkValidationError("The selected file is not a valid APK archive.") from error

        suffix = path.suffix.lower()
        package_format = suffix.removeprefix(".")
        apk_entries: tuple[str, ...] = ()
        if suffix == ".apk":
            has_manifest = "AndroidManifest.xml" in names
            if not has_manifest:
                raise ApkValidationError("The archive has no Android application manifest.")
            signature = _has_signature_metadata(names)
            native_abis = _native_abis_from_members(members)
        elif suffix in SUPPORTED_PACKAGE_SUFFIXES:
            apk_entries = _bundle_apk_entries(members)
            if not apk_entries:
                raise ApkValidationError("The Android bundle contains no APK payloads.")
            signature = False
            native_abis = _inspect_nested_apks(path, apk_entries, max_bytes)
            has_manifest = True
        else:
            raise ApkValidationError("This is not a supported Android package format.")
        return ApkInspection(
            source_path=str(path.absolute()),
            display_name=path.name,
            byte_size=info.st_size,
            sha256=digest.hexdigest(),
            zip_members=len(members),
            expanded_bytes=expanded,
            has_manifest=True,
            has_v1_signature_files=signature,
            source_origin=_origin_for(path),
            package_format=package_format,
            apk_entries=apk_entries,
            native_abis=native_abis,
        )
    finally:
        os.close(fd)


def stage_apk(path: Path, inspection: ApkInspection) -> Path:
    configured_root = os.environ.get("LUMA_ANDROID_STAGING_ROOT")
    if configured_root:
        staging_root = Path(configured_root)
    else:
        state_root = Path(
            os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
        )
        staging_root = state_root / "luma-android" / "install"
    # Large split bundles can expand well beyond the small tmpfs commonly
    # mounted at XDG_RUNTIME_DIR. Use private, disk-backed state storage so a
    # valid APKM/APKS transaction cannot exhaust the login session's memory.
    staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging_root.chmod(0o700)
    transaction = staging_root / str(uuid.uuid4())
    transaction.mkdir(mode=0o700, parents=True, exist_ok=False)
    destination = transaction / f"package.{inspection.package_format}"
    try:
        source_fd = os.open(
            path.expanduser(),
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            with os.fdopen(os.dup(source_fd), "rb") as source, destination.open("xb") as target:
                os.chmod(destination, 0o600)
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
        finally:
            os.close(source_fd)
        # Policy inspection already bounded and validated the source archive.
        # At this point the security question is whether the exact bytes that
        # were confirmed are the bytes we privately staged. Reusing the outer
        # archive's compressed byte size as an inspection limit is incorrect:
        # a valid APKM/XAPK can contain an APK member whose uncompressed size
        # is larger than the compressed package-set file.
        staged_size = destination.stat().st_size
        staged_digest = hashlib.sha256()
        with destination.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                staged_digest.update(chunk)
        if (
            staged_digest.hexdigest() != inspection.sha256
            or staged_size != inspection.byte_size
        ):
            raise ApkValidationError("The APK changed while it was being staged.")
        return destination
    except Exception:
        shutil.rmtree(transaction, ignore_errors=True)
        raise


def stage_package_payloads(path: Path, inspection: ApkInspection) -> list[Path]:
    staged_archive = stage_apk(path, inspection)
    if inspection.package_format == "apk":
        return [staged_archive]

    payload_root = staged_archive.parent / "payloads"
    payload_root.mkdir(mode=0o700)
    payloads: list[Path] = []
    try:
        with zipfile.ZipFile(staged_archive) as archive:
            for index, member_name in enumerate(inspection.apk_entries):
                destination = payload_root / f"{index:04d}.apk"
                member = archive.getinfo(member_name)
                with archive.open(member) as source, destination.open("xb") as target:
                    os.chmod(destination, 0o600)
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                # Re-run the complete APK inspector after extraction. This
                # makes every transaction member independently bounded and
                # proves it retained an Android manifest.
                inspect_apk(destination, member.file_size)
                payloads.append(destination)
        if not any(Path(name).name == "base.apk" for name in inspection.apk_entries):
            # Some XAPK producers name the base payload after the package.
            # Accept exactly one manifest-bearing APK as the base, but reject
            # ambiguous multi-APK archives without canonical split metadata.
            if len(payloads) != 1:
                raise ApkValidationError(
                    "The Android bundle has no identifiable base APK."
                )
        return payloads
    except Exception:
        shutil.rmtree(staged_archive.parent, ignore_errors=True)
        raise


def staged_package_root(payloads: list[Path]) -> Path:
    if not payloads:
        raise ApkValidationError("The Android package has no staged payloads.")
    parent = payloads[0].parent
    return parent.parent if parent.name == "payloads" else parent


def _has_signature_metadata(names: set[str]) -> bool:
    return any(
        name.upper().startswith("META-INF/")
        and name.upper().endswith((".RSA", ".DSA", ".EC", ".SF"))
        for name in names
    )


def _bundle_apk_entries(members: list[zipfile.ZipInfo]) -> tuple[str, ...]:
    entries: list[str] = []
    for member in members:
        name = member.filename
        pure = Path(name)
        if member.is_dir() or pure.suffix.lower() != ".apk":
            continue
        if pure.is_absolute() or ".." in pure.parts or "\\" in name:
            raise ApkValidationError("The Android bundle contains an unsafe APK path.")
        if member.file_size <= 0:
            raise ApkValidationError("The Android bundle contains an empty APK payload.")
        entries.append(name)
    if len(entries) > 256:
        raise ApkValidationError("The Android bundle contains too many APK payloads.")
    canonical = [name for name in entries if Path(name).name.lower() == "base.apk"]
    if len(canonical) > 1:
        raise ApkValidationError("The Android bundle contains multiple base APKs.")
    if not canonical and len(entries) > 1:
        # XAPK producers commonly name the base after the application package
        # while retaining Android's config.* or split_config.* names for its
        # dependent splits. Accept only that unambiguous shape here; Android's
        # PackageInstaller still verifies package and signing identity.
        candidates = [
            name
            for name in entries
            if not Path(name).name.lower().startswith(("config.", "split_config."))
        ]
        if len(candidates) != 1:
            raise ApkValidationError("The Android bundle has no identifiable base APK.")
        canonical = candidates

    base = canonical[0] if canonical else entries[0]
    return tuple([base, *sorted(name for name in entries if name != base)])


def _native_abis_from_members(members: list[zipfile.ZipInfo]) -> tuple[str, ...]:
    abis = {
        parts[1]
        for member in members
        if not member.is_dir()
        and len(parts := Path(member.filename).parts) >= 3
        and parts[0] == "lib"
        and parts[1] in ANDROID_NATIVE_ABIS
    }
    return tuple(sorted(abis))


def _inspect_nested_apks(
    path: Path, entries: tuple[str, ...], max_bytes: int
) -> tuple[str, ...]:
    native_abis: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for member_name in entries:
            member = archive.getinfo(member_name)
            if member.file_size > max_bytes:
                raise ApkValidationError("A bundled APK exceeds the configured size limit.")
            try:
                with archive.open(member) as stream, zipfile.ZipFile(stream) as nested:
                    nested_members = nested.infolist()
                    if len(nested_members) > MAX_ZIP_MEMBERS:
                        raise ApkValidationError("A bundled APK contains too many files.")
                    if sum(item.file_size for item in nested_members) > MAX_EXPANDED_BYTES:
                        raise ApkValidationError("A bundled APK expands beyond the safe limit.")
                    if "AndroidManifest.xml" not in {item.filename for item in nested_members}:
                        raise ApkValidationError("A bundled APK has no Android manifest.")
                    native_abis.update(_native_abis_from_members(nested_members))
            except zipfile.BadZipFile as error:
                raise ApkValidationError("The bundle contains an invalid APK payload.") from error
    return tuple(sorted(native_abis))


# Product-language aliases. The source keeps these wrappers so callers can
# migrate from the original APK-only API without weakening compatibility.
inspect_android_package = inspect_apk
stage_android_package = stage_package_payloads
local_android_package_from_uri = local_apk_from_uri
