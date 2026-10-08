from __future__ import annotations

import configparser
import platform
import re
import struct
import subprocess
from pathlib import Path

from .errors import InstallerError
from .model import PackageReport, kind_for_path
from .safety import fingerprint

TOOL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _run(arguments: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PATH": TOOL_PATH, "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallerError(f"Static inspection could not complete: {error}") from error


def _first_line(value: str, fallback: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), fallback)


def _rpm(path: Path, size: int, digest: str) -> PackageReport:
    query = _run([
        "rpm", "-qp", "--queryformat",
        "%{NAME}\\n%{SUMMARY}\\n%{ARCH}\\n%{VENDOR}\\n%{LICENSE}\\n", str(path),
    ])
    if query.returncode != 0:
        raise InstallerError("This is not a valid RPM package.")
    values = query.stdout.splitlines() + [""] * 5
    signature = _run(["rpm", "-Kv", str(path)])
    signature_text = signature.stdout + signature.stderr
    verified = (signature.returncode == 0 and "NOT OK" not in signature_text
                and "NOKEY" not in signature_text and "SIGNATURE" in signature_text.upper())
    signature_lines = [
        line.strip() for line in signature_text.splitlines()
        if line.strip() and not line.strip().endswith(":")
    ]
    identity = next(
        (line for line in signature_lines if "signature" in line.lower()),
        "No publisher signature; RPM payload integrity digests were checked",
    )
    scriptlets = _run(["rpm", "-qp", "--scripts", str(path)])
    files = _run(["rpm", "-qpl", str(path)])
    is_application = any(
        line.startswith(("/usr/share/applications/", "/usr/local/share/applications/"))
        and line.endswith(".desktop") for line in files.stdout.splitlines()
    )
    warnings = []
    if is_application:
        warnings.extend((
            "This application RPM is isolated in a rootless Fedora capsule instead of modifying Luma.",
            "Its Fedora dependencies and maintainer scripts remain inside that capsule.",
        ))
    else:
        warnings.extend((
            "This RPM does not publish a desktop application and is treated as a system component.",
            "It is added to Luma's system image and usually works right away; a change that can't be applied "
            "to the running system waits for a restart. It stays through updates and can be rolled back.",
        ))
    if scriptlets.stdout.strip():
        if is_application:
            warnings.append(
                "This package contains maintainer scripts; they run as capsule root inside the rootless container, not on the Luma host."
            )
        else:
            warnings.append("This package contains maintainer scripts that run with system privilege.")
    return PackageReport(
        path, "rpm", values[0] or path.stem,
        values[1] or "Fedora software package",
        size, digest, values[2] or "unknown",
        identity,
        "Private Fedora application capsule" if is_application else "Luma's system image",
        (("Use network while resolving declared Fedora dependencies", "Keep private application data")
         if is_application else ("Install system files", "Add to Luma's system image")),
        tuple(warnings),
        {"Package name": values[0], "Vendor": values[3] or "Not declared", "License": values[4] or "Not declared",
         "RPM role": "Application" if is_application else "System component"},
        not is_application, not is_application, verified,
    )


def _deb(path: Path, size: int, digest: str) -> PackageReport:
    def field(name: str) -> str:
        result = _run(["dpkg-deb", "--field", str(path), name])
        if result.returncode != 0:
            raise InstallerError("This is not a valid Debian package.")
        return _first_line(result.stdout, "")
    values = [field(name) for name in ("Package", "Description", "Architecture", "Maintainer", "Depends")]
    script_note = "Maintainer scripts are isolated inside the Debian capsule"
    return PackageReport(
        path, "deb", values[0] or path.stem, values[1] or "Debian software package",
        size, digest, values[2] or "unknown", "Local Debian package; no trusted signature envelope",
        "Private Debian application capsule",
        ("Use network while resolving declared Debian dependencies", "Keep private application data"),
        (
            "This is not converted into an RPM and does not modify Fedora's package database.",
            script_note,
        ),
        {"Maintainer": values[3] or "Not declared", "Dependencies": values[4] or "None declared"},
    )


def _elf_arch(header: bytes) -> str:
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return "unknown"
    endian = "<" if header[5] == 1 else ">"
    machine = struct.unpack(endian + "H", header[18:20])[0]
    return {3: "i386", 40: "arm", 62: "x86_64", 183: "aarch64"}.get(machine, f"ELF machine {machine}")


def _appimage(path: Path, size: int, digest: str) -> PackageReport:
    with path.open("rb") as stream:
        header = stream.read(16 * 1024)
    if header[:4] != b"\x7fELF" or not (b"AI\x01" in header or b"AI\x02" in header):
        raise InstallerError("This file does not contain a recognized AppImage marker.")
    return PackageReport(
        path, "appimage", path.name.removesuffix(path.suffix), "Portable Linux application",
        size, digest, _elf_arch(header), "Portable local executable; publisher identity is unverified",
        "Private Luma application directory",
        ("Run inside a Luma application sandbox", "Keep private application data"),
        ("The file is never executed during inspection.", "Luma will make its private staged copy executable."),
    )


def _portable(path: Path, size: int, digest: str) -> PackageReport:
    from .archive_app import analyse
    layout = analyse(path)
    toolkit = {"electron": "Electron", "flutter": "Flutter", "java": "Java", "qt": "Qt"}.get(layout.toolkit, "")
    files = sum(1 for member in layout.members if member.kind == "file")
    return PackageReport(
        path, "portable", layout.name,
        layout.comment or (f"{toolkit} application for Linux" if toolkit else "Application for Linux"),
        size, digest, "unknown" if layout.architecture in {"script", "unknown"} else layout.architecture,
        "Downloaded application folder; publisher identity is unverified",
        "Private Luma application directory",
        ("Run inside a Luma application sandbox", "Keep private application data"),
        ("Nothing in the folder runs during inspection.",
         "Every file is checked before extraction: no paths or links outside the application folder."),
        {"Program": layout.executable, "Kind": toolkit or "Native", "Files": str(files),
         "Metadata source": "The folder's own launcher and program, read without running them"},
        version=next(iter(re.findall(r"(?<![\w.])v?(\d+(?:\.\d+)+)", layout.top or path.name)), ""),
    )


def pack_folder(folder: Path) -> Path:
    """An unpacked application folder, as one reviewable, fingerprinted file.

    The tar is deterministic for unchanged contents, so reopening the same
    folder reviews the same fingerprint.
    """
    import os
    import tarfile
    folder = folder.expanduser().absolute()
    if folder.is_symlink() or not folder.is_dir():
        raise InstallerError("The application folder must be a local folder.")
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "luma/installer/folders"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / f"{folder.name[:120]}.tar"
    partial = root / f".{folder.name[:120]}.tar.partial"

    def normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    total = 0
    with tarfile.open(partial, "w", format=tarfile.PAX_FORMAT) as archive:
        archive.add(folder, arcname=folder.name, recursive=False, filter=normalise)
        for current, directories, names in os.walk(folder):
            directories.sort()
            for name in sorted(directories) + sorted(names):
                item = Path(current) / name
                if item.is_dir() and not item.is_symlink():
                    archive.add(item, arcname=str(Path(folder.name) / item.relative_to(folder)), recursive=False, filter=normalise)
                    continue
                if not (item.is_file() or item.is_symlink()):
                    continue
                total += item.lstat().st_size
                if total > 8 * 1024 ** 3:
                    partial.unlink(missing_ok=True)
                    raise InstallerError("The folder exceeds Luma's 8 GB safety limit.")
                archive.add(item, arcname=str(Path(folder.name) / item.relative_to(folder)), recursive=False, filter=normalise)
            directories[:] = sorted(d for d in directories if not (Path(current) / d).is_symlink())
    partial.chmod(0o600)
    partial.replace(destination)
    return destination


def _flatpak_keyfile(path: Path, kind: str, size: int, digest: str) -> PackageReport:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error) as error:
        raise InstallerError(f"The Flatpak reference is malformed: {error}") from error
    section_name = "Flatpak Ref" if kind == "flatpakref" else "Flatpak Repo"
    if section_name not in parser:
        raise InstallerError(f"The file does not contain a [{section_name}] section.")
    section = parser[section_name]
    name = section.get("Name", path.stem)
    has_key = bool(section.get("GPGKey", "").strip())
    remote = section.get("Url", "Not declared")
    return PackageReport(
        path, kind, section.get("Title", name).strip() or name, "Flatpak application reference" if kind == "flatpakref" else "Flatpak repository",
        size, digest, "runtime-selected", "Includes a GPG trust key" if has_key else "No embedded GPG key",
        "Per-user Flatpak installation",
        ("Use Flatpak's declared sandbox permissions", "Use network to retrieve signed repository content"),
        (() if has_key else ("The reference does not embed a GPG key; review the remote carefully.",)),
        {"Repository": remote, "Application ID": name}, verified=False,
    )


def _flatpak_bundle(path: Path, size: int, digest: str) -> PackageReport:
    info = _run(["flatpak", "info", "--file", str(path)])
    if info.returncode != 0:
        raise InstallerError("This is not a valid Flatpak single-file bundle.")
    title = next((line.split(":", 1)[1].strip() for line in info.stdout.splitlines()
                  if line.lower().startswith("name:")), path.stem)
    reference = _run(["flatpak", "info", "--file", "--show-ref", str(path)])
    ref_value = reference.stdout.strip() if reference.returncode == 0 else ""
    ref_parts = ref_value.split("/")
    application_id = ref_parts[1] if len(ref_parts) >= 2 and ref_parts[0] == "app" else title
    return PackageReport(
        path, "flatpak", title, "Flatpak single-file bundle", size, digest,
        "bundle-declared", "Flatpak validates the bundle's repository metadata during install",
        "Per-user Flatpak installation",
        ("Use Flatpak's declared sandbox permissions", "Keep application data in the Flatpak sandbox"),
        ("Single-file bundles do not provide ongoing updates unless an origin is configured.",),
        {"Application ID": application_id, "Reference": ref_value or "Not declared"},
    )


def _snap(path: Path, size: int, digest: str) -> PackageReport:
    result = _run(["unsquashfs", "-cat", str(path), "meta/snap.yaml"])
    if result.returncode != 0:
        raise InstallerError("This is not a valid Snap package with meta/snap.yaml.")
    def field(name: str, fallback: str = "Not declared") -> str:
        match = re.search(rf"(?m)^{re.escape(name)}:\s*['\"]?([^'\"\n]+)", result.stdout)
        return match.group(1).strip() if match else fallback
    name = field("name", path.stem)
    confinement = field("confinement", "strict")
    grade = field("grade")
    return PackageReport(
        path, "snap", name, field("summary", "Snap application"), size, digest,
        field("architectures", "package-declared"),
        "Local Snap without a separately supplied store assertion",
        "System Snap installation",
        (f"Use Snap's {confinement} confinement policy", "Register system services declared by the snap"),
        (
            "Local Snap installation requires snapd's --dangerous mode, which skips assertion verification.",
            "Only continue if you independently trust this exact fingerprint.",
        ),
        {"Package name": name, "Confinement": confinement, "Grade": grade}, True, False, False,
    )


def inspect_package(path: Path) -> PackageReport:
    path = path.expanduser().absolute()
    if path.is_dir():
        path = pack_folder(path)
    kind = kind_for_path(path)
    if kind is None:
        raise InstallerError("Luma does not recognize this application package format.")
    size, digest = fingerprint(path)
    from .facts import compatibility_report, enrich, flatpak_bundle
    if kind in {"android", "windows"}:
        report = compatibility_report(path, kind, size, digest)
    elif kind == "flatpak":
        report = enrich(flatpak_bundle(path, size, digest))
    elif kind in {"flatpakref", "flatpakrepo"}:
        report = enrich(_flatpak_keyfile(path, kind, size, digest))
    else:
        inspector = {"rpm": _rpm, "deb": _deb, "appimage": _appimage, "snap": _snap, "portable": _portable}.get(kind)
        if inspector is None: raise InstallerError("The package format has no inspector.")
        report = enrich(inspector(path, size, digest))
    if fingerprint(path)[1] != digest:
        raise InstallerError("The package changed during inspection. Open it again.")
    return report


def host_architecture() -> str:
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())


def architecture_is_compatible(report: PackageReport) -> bool:
    value = report.architecture.lower()
    host = host_architecture()
    if value in {"unknown", "runtime-selected", "bundle-declared", "package-declared", "all", "noarch", "any"}:
        return True
    aliases = {"x86_64": {"x86_64", "amd64"}, "aarch64": {"aarch64", "arm64"}}
    return any(token in value for token in aliases.get(host, {host}))


def architecture_name(value: str) -> str:
    """Describe the package and host independently in user-facing diagnostics."""
    return {"amd64": "Intel/AMD 64-bit", "x86_64": "Intel/AMD 64-bit",
            "arm64": "ARM64", "aarch64": "ARM64"}.get(value.lower(), value)


def architecture_mismatch_message(report: PackageReport) -> str:
    package = architecture_name(report.architecture)
    host = architecture_name(host_architecture())
    return f"This application is built for {package}. This device uses {host}. Choose a {host} build."
