"""Applications published as an archive: .tar.gz, .tar.xz, .zip and friends.

Many Linux applications are released as a compressed folder with a program in
it — Flutter and Electron apps, JetBrains IDEs, Firefox and Thunderbird
builds, games, anything built with a portable toolchain. People download one,
get a folder, and find nothing that installs it. Valet recognises the shape:

* the archive is listed, never executed, and every member is checked before
  anything is written — no absolute paths, no `..`, no links that leave the
  application folder, no device files;
* the program to run is found from, in order, a launcher (.desktop) inside the
  archive, an AppRun, a native executable named like the archive, or the only
  native executable at the top of the folder, and its architecture is read
  from its ELF header;
* the icon is the launcher's, or a picture named like an icon in the folders
  applications customarily keep one in;
* the kind of application (Electron, Flutter, Java, Qt) is noted so it can be
  started the way it needs.

The folder is extracted into Luma's private application storage and run in the
same sandbox as an AppImage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import configparser
import io
import posixpath
import re
import stat
import struct
import tarfile
import zipfile
from pathlib import Path

from .desktop import desktop_mime_types
from .errors import InstallerError

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tbz", ".tar.zst", ".tzst", ".tar", ".zip")
MAX_MEMBERS = 200_000
MAX_EXTRACTED_BYTES = 16 * 1024 ** 3
ICON_NAMES = re.compile(r"(?:^|/)(?:icon|logo|app|appicon|product|[a-z0-9_-]*icon)[^/]*\.(?:png|svg)$", re.IGNORECASE)
VERSION_TAIL = re.compile(r"[-_. ]v?\d[\w.]*$|[-_. ](?:linux|x86[-_]?64|amd64|arm64|aarch64|x64|portable|bin)$", re.IGNORECASE)


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


@dataclass
class Member:
    name: str            # relative to the archive root, after stripping a single top folder
    size: int
    executable: bool
    kind: str            # file | dir | symlink
    target: str = ""


@dataclass
class Layout:
    name: str
    executable: str
    architecture: str
    toolkit: str
    icon: str = ""
    comment: str = ""
    top: str = ""
    # The files and links the folder's own launcher says the application opens.
    mime_types: list[str] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)


def _clean(name: str) -> str | None:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if not name or name in (".", "./"):
        return None
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise InstallerError("The archive contains an absolute path. Luma will not extract it.")
    normal = posixpath.normpath(name)
    if normal == ".." or normal.startswith("../"):
        raise InstallerError("The archive contains a path that leaves its own folder. Luma will not extract it.")
    return normal


def _open(path: Path):
    if path.name.lower().endswith(".zip"):
        return zipfile.ZipFile(path)
    if path.name.lower().endswith((".tar.zst", ".tzst")):
        raise InstallerError("Zstandard archives are not supported yet. Extract it and open the folder with Valet.")
    return tarfile.open(path, "r:*")


def list_members(path: Path) -> list[Member]:
    raw: list[Member] = []
    try:
        with _open(path) as archive:
            if isinstance(archive, zipfile.ZipFile):
                for info in archive.infolist():
                    name = _clean(info.filename)
                    if name is None:
                        continue
                    mode = info.external_attr >> 16
                    kind = "dir" if info.is_dir() else "symlink" if stat.S_ISLNK(mode) else "file"
                    target = archive.read(info).decode("utf-8", "replace") if kind == "symlink" else ""
                    raw.append(Member(name, info.file_size, bool(mode & 0o111), kind, target))
                    if len(raw) > MAX_MEMBERS:
                        raise InstallerError("The archive has too many files.")
            else:
                for info in archive:
                    name = _clean(info.name)
                    if name is None:
                        continue
                    if not (info.isfile() or info.isdir() or info.issym()):
                        raise InstallerError("The archive contains a device or special file. Luma will not extract it.")
                    kind = "dir" if info.isdir() else "symlink" if info.issym() else "file"
                    raw.append(Member(name, info.size, bool(info.mode & 0o111), kind, info.linkname if info.issym() else ""))
                    if len(raw) > MAX_MEMBERS:
                        raise InstallerError("The archive has too many files.")
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as error:
        raise InstallerError(f"The archive could not be read: {error}") from None
    if sum(member.size for member in raw) > MAX_EXTRACTED_BYTES:
        raise InstallerError("The archive expands to more than Luma's 16 GB limit.")
    for member in raw:
        if member.kind == "symlink":
            if member.target.startswith("/"):
                raise InstallerError("The archive contains a link to an absolute path. Luma will not extract it.")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.target))
            if resolved == ".." or resolved.startswith("../"):
                raise InstallerError("The archive contains a link that leaves its own folder. Luma will not extract it.")
    return raw


def _single_top(members: list[Member]) -> str:
    tops = {member.name.split("/", 1)[0] for member in members}
    if len(tops) == 1:
        top = next(iter(tops))
        if any(member.name.startswith(top + "/") for member in members):
            return top
    return ""


def _read(path: Path, member_name: str, limit: int) -> bytes:
    with _open(path) as archive:
        if isinstance(archive, zipfile.ZipFile):
            for info in archive.infolist():
                if _clean(info.filename) == member_name:
                    with archive.open(info) as stream:
                        return stream.read(limit)
        else:
            info = archive.getmember(member_name) if member_name in archive.getnames() else None
            if info is None:
                for candidate in archive.getmembers():
                    if _clean(candidate.name) == member_name:
                        info = candidate
                        break
            if info is not None and info.isfile():
                stream = archive.extractfile(info)
                return stream.read(limit) if stream else b""
    return b""


def _elf_architecture(header: bytes) -> str:
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return ""
    endian = "<" if header[5] == 1 else ">"
    machine = struct.unpack(endian + "H", header[18:20])[0]
    return {3: "i386", 40: "arm", 62: "x86_64", 183: "aarch64"}.get(machine, f"ELF machine {machine}")


def display_name(stem: str) -> str:
    name = stem
    for _ in range(4):
        shorter = VERSION_TAIL.sub("", name)
        if shorter == name:
            break
        name = shorter
    name = name.replace("_", " ").replace("-", " ").strip() or stem
    return name[:1].upper() + name[1:]


def analyse(path: Path) -> Layout:
    members = list_members(path)
    top = _single_top(members)
    full = {member.name: member for member in members}
    relative = {(member.name[len(top) + 1:] if top else member.name): member for member in members
                if not top or member.name != top}
    files = {name: member for name, member in relative.items() if member.kind == "file"}
    stem = path.name
    for suffix in ARCHIVE_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    folder = top or stem
    name, comment, icon, executable = display_name(folder), "", "", ""
    mime_types: list[str] = []

    def member_path(rel: str) -> str:
        return f"{top}/{rel}" if top else rel

    desktop = next((rel for rel in sorted(files) if rel.endswith(".desktop") and rel.count("/") <= 2), "")
    if desktop:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            contents = _read(path, member_path(desktop), 64_000).decode("utf-8", "replace")
            parser.read_string(contents)
            mime_types = desktop_mime_types(contents)
            entry = parser["Desktop Entry"]
            name = entry.get("Name", name).strip() or name
            comment = entry.get("Comment", "").strip()
            icon = entry.get("Icon", "").strip()
            command = entry.get("Exec", "").split()
            if command:
                candidate = posixpath.basename(command[0])
                executable = next((rel for rel in files if posixpath.basename(rel) == candidate and files[rel].executable), "")
        except (configparser.Error, KeyError, UnicodeError):
            pass

    architecture = ""
    if not executable and "AppRun" in files:
        executable = "AppRun"
    if not executable:
        wanted = re.sub(r"[^a-z0-9]", "", display_name(folder).lower())
        native: list[tuple[int, str]] = []
        for rel, member in files.items():
            if not member.executable or rel.count("/") > 1 or member.size < 64:
                continue
            if rel.count("/") == 1 and not rel.startswith(("bin/", "usr/bin/")):
                continue
            arch = _elf_architecture(_read(path, member_path(rel), 64))
            if not arch:
                continue
            base = re.sub(r"[^a-z0-9]", "", posixpath.basename(rel).lower())
            score = (0 if base == wanted else 1 if wanted.startswith(base) or base.startswith(wanted) else 2,
                     rel.count("/"))
            native.append((score[0] * 10 + score[1], rel))
            if base == wanted and rel.count("/") == 0:
                break
        if native:
            native.sort()
            if native[0][0] < 20 or len([n for n in native if n[1].count("/") == 0]) == 1:
                executable = native[0][1]
    if not executable:
        scripts = [rel for rel, member in files.items() if member.executable and rel.startswith("bin/") and rel.endswith(".sh")]
        if len(scripts) == 1:
            executable = scripts[0]
    if not executable:
        raise InstallerError("This archive does not contain a Linux application Valet recognises: "
                             "no launcher, no AppRun and no program at the top of the folder.")
    header = _read(path, member_path(executable), 64)
    architecture = _elf_architecture(header) or ("script" if header.startswith(b"#!") else "unknown")

    if "resources/app.asar" in files or any(rel.endswith("/resources/app.asar") for rel in files):
        toolkit = "electron"
    elif any(rel.startswith("data/flutter_assets/") for rel in files):
        toolkit = "flutter"
    elif any(rel.startswith(("jbr/", "jre/")) for rel in relative):
        toolkit = "java"
    elif any(posixpath.basename(rel).startswith("libQt") for rel in files):
        toolkit = "qt"
    else:
        toolkit = "native"

    icon_path = ""
    if icon:
        icon_path = next((rel for rel in sorted(files) if posixpath.splitext(posixpath.basename(rel))[0] == icon
                          and rel.endswith((".png", ".svg"))), "")
    if not icon_path:
        candidates = sorted((rel for rel in files if ICON_NAMES.search(rel) and files[rel].size < 4 * 1024 ** 2),
                            key=lambda rel: (rel.endswith(".ico"), -files[rel].size))
        icon_path = candidates[0] if candidates else ""
    del full
    return Layout(name=name, executable=executable, architecture=architecture, toolkit=toolkit,
                  icon=icon_path, comment=comment, top=top, mime_types=mime_types, members=members)


def extract(path: Path, destination: Path, layout: Layout) -> None:
    """Extract into `destination`, stripping the single top folder, members re-checked."""
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = destination.resolve()
    prefix = layout.top + "/" if layout.top else ""
    with _open(path) as archive:
        if isinstance(archive, zipfile.ZipFile):
            items = [(info, _clean(info.filename)) for info in archive.infolist()]
        else:
            items = [(info, _clean(info.name)) for info in archive]
        for info, name in items:
            if name is None or (layout.top and name == layout.top):
                continue
            relative = name[len(prefix):] if prefix and name.startswith(prefix) else name
            target = (destination / relative)
            if not target.resolve().is_relative_to(root) and not target.is_symlink():
                raise InstallerError("An archive member would land outside the application folder.")
            if isinstance(archive, zipfile.ZipFile):
                mode = info.external_attr >> 16
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if stat.S_ISLNK(mode):
                    target.symlink_to(archive.read(info).decode("utf-8", "replace"))
                    continue
                with archive.open(info) as source, open(target, "wb") as sink:
                    while chunk := source.read(1 << 20):
                        sink.write(chunk)
                target.chmod(0o755 if mode & 0o111 else 0o644)
            else:
                if info.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif info.issym():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    target.symlink_to(info.linkname)
                elif info.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(info)
                    with open(target, "wb") as sink:
                        while source and (chunk := source.read(1 << 20)):
                            sink.write(chunk)
                    target.chmod(0o755 if info.mode & 0o111 else 0o644)
    # Links are only valid if they still resolve inside the folder.
    for link in destination.rglob("*"):
        if link.is_symlink():
            try:
                resolved = link.resolve(strict=False)
            except OSError:
                link.unlink()
                continue
            if not resolved.is_relative_to(root):
                link.unlink()
