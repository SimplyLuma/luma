# SPDX-License-Identifier: Apache-2.0
"""Look inside one uploaded package. Runs in the networkless review container.

  python3 -m depot_review.inspect --upload FILE --format FORMAT --work DIR \
      [--clamav-db DIR] > facts.json

Every package is opened with the tools its own format uses (flatpak and
ostree, rpm, dpkg-deb, unsquashfs), never executed. The facts printed here are
all review needs: identity, architecture, runtime, what it can reach, and the
malware scan of every file it would install.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from . import words

APP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z")
ARCH_NAMES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64",
              "noarch": "any", "all": "any"}
ELF_MACHINES = {62: "x86_64", 183: "aarch64"}


class Refused(Exception):
    """A package that cannot be accepted; the message is shown to the developer."""


def run(args: list[str], *, timeout: int = 600, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sniff(path: Path) -> str:
    with path.open("rb") as stream:
        head = stream.read(64)
    if head[:4] == b"\xed\xab\xee\xdb":
        return "rpm"
    if head.startswith(b"!<arch>\ndebian-binary"):
        return "deb"
    if head[:4] == b"\x7fELF" and head[8:11] == b"AI\x02":
        return "appimage"
    if head[:4] == b"\x7fELF" and head[8:11] == b"AI\x01":
        return "appimage-type1"
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] == b"hsqs":
        return "snap"
    return "flatpak?"


# -- Flatpak -----------------------------------------------------------------

def inspect_flatpak(upload: Path, work: Path) -> dict:
    repo = work / "repo"
    run(["ostree", "init", "--mode=archive-z2", f"--repo={repo}"])
    imported = run(["flatpak", "build-import-bundle", "--no-update-summary", str(repo), str(upload)],
                   check=False, timeout=900)
    if imported.returncode != 0:
        raise Refused("This file isn't a valid Flatpak bundle, or it's damaged. "
                      "Make it again with `flatpak build-bundle` and upload it again.")
    refs = run(["ostree", "refs", f"--repo={repo}"]).stdout.split()
    apps = [r for r in refs if r.startswith("app/")]
    if len(apps) != 1:
        raise Refused("A Flatpak bundle must hold exactly one app. Bundles of runtimes or "
                      "extensions aren't accepted yet.")
    ref = apps[0]
    _, app_id, arch, branch = ref.split("/")
    commit = run(["ostree", "rev-parse", f"--repo={repo}", ref]).stdout.strip()
    metadata_text = run(["ostree", "cat", f"--repo={repo}", commit, "/metadata"]).stdout
    metadata = words.read_metadata(metadata_text)
    if metadata.has_section("Extra Data"):
        raise Refused("This app downloads part of itself during install (Flatpak extra-data). "
                      "Depot can't check what isn't in the upload, so put everything in the bundle.")
    application = metadata["Application"] if metadata.has_section("Application") else {}
    if application.get("name") != app_id:
        raise Refused("The bundle's metadata names a different app than its ref.")
    listing = run(["ostree", "ls", "-R", "-C", f"--repo={repo}", commit, "/export", "/files/share"],
                  check=False).stdout
    exported = [line.split()[-1] for line in listing.splitlines() if line.startswith("-")]
    desktop = [p for p in exported if p.startswith("/export/share/applications/") and p.endswith(".desktop")]
    icons = [p for p in exported if p.startswith("/export/share/icons/") and f"/apps/{app_id}" in p]
    metainfo = [p for p in exported if p.startswith("/files/share/metainfo/") and p.endswith(".xml")]
    show = run(["ostree", "show", f"--repo={repo}", "--print-metadata-key=xa.installed-size", commit],
               check=False).stdout.strip()
    installed = None
    match = re.search(r"(\d+)", show)
    if match:
        installed = int(match.group(1))
    checkout = work / "files"
    run(["ostree", "checkout", f"--repo={repo}", "--user-mode", commit, str(checkout)], timeout=900)
    reach = words.flatpak_reach(metadata_text)
    return {
        "format": "flatpak", "app_id": app_id, "arch": arch, "branch": branch, "ref": ref,
        "commit": commit, "runtime": application.get("runtime", ""), "sdk": application.get("sdk", ""),
        "command": application.get("command", ""), "metadata": metadata_text,
        "desktop_files": desktop, "icons": icons, "metainfo": metainfo,
        "installed_size": installed, "scan_root": str(checkout),
        "reach": reach,
    }


# -- Universal packages --------------------------------------------------------

def inspect_rpm(upload: Path, work: Path) -> dict:
    query = run(["rpm", "-qp", "--nosignature", "--nodigest", "--queryformat",
                 "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}\\n%{SUMMARY}\\n", str(upload)], check=False)
    if query.returncode != 0:
        raise Refused("This file isn't a valid RPM package, or it's damaged.")
    verify = run(["rpm", "-K", "--nosignature", str(upload)], check=False)
    if verify.returncode != 0:
        raise Refused("This RPM package is damaged: its checksums don't match its contents.")
    name, version, arch, summary = (query.stdout.split("\n") + ["", "", "", ""])[:4]
    files = [f for f in run(["rpm", "-qlp", "--nosignature", "--nodigest", str(upload)]).stdout.splitlines()
             if f.startswith("/")]
    scripts_text = run(["rpm", "-qp", "--nosignature", "--nodigest", "--scripts", str(upload)]).stdout
    scripts = {}
    current = None
    for line in scripts_text.splitlines():
        found = re.match(r"(\w+) scriptlet", line)
        if found:
            current = found.group(1)
            scripts[current] = ""
        elif current:
            scripts[current] += line + "\n"
    root = work / "files"
    root.mkdir()
    extract = subprocess.run(f"rpm2cpio {shlex_quote(str(upload))} | cpio -idm --quiet --no-absolute-filenames",
                             shell=True, cwd=root, capture_output=True, text=True, timeout=900)
    if extract.returncode != 0:
        raise Refused("This RPM package couldn't be unpacked.")
    return {"format": "rpm", "app_id": name, "version": version, "arch": ARCH_NAMES.get(arch, arch),
            "summary": summary, "files": files, "scripts": sorted(scripts),
            "scan_root": str(root), "reach": words.package_reach(files, scripts)}


def shlex_quote(value: str) -> str:
    import shlex
    return shlex.quote(value)


def inspect_deb(upload: Path, work: Path) -> dict:
    fields = run(["dpkg-deb", "-f", str(upload), "Package", "Version", "Architecture", "Description"],
                 check=False)
    if fields.returncode != 0:
        raise Refused("This file isn't a valid Debian package, or it's damaged.")
    info = {}
    for line in fields.stdout.splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    control = work / "control"
    run(["dpkg-deb", "-e", str(upload), str(control)])
    scripts = {}
    for name in ("preinst", "postinst", "prerm", "postrm", "config", "triggers"):
        path = control / name
        if path.is_file():
            scripts[name] = path.read_text(errors="replace")
    root = work / "files"
    extracted = run(["dpkg-deb", "-x", str(upload), str(root)], check=False, timeout=900)
    if extracted.returncode != 0:
        raise Refused("This Debian package couldn't be unpacked; it may be damaged.")
    files = sorted("/" + str(p.relative_to(root)) for p in root.rglob("*") if not p.is_dir())
    return {"format": "deb", "app_id": info.get("Package", ""), "version": info.get("Version", ""),
            "arch": ARCH_NAMES.get(info.get("Architecture", ""), info.get("Architecture", "")),
            "summary": info.get("Description", ""), "files": files, "scripts": sorted(scripts),
            "scan_root": str(root), "reach": words.package_reach(files, scripts)}


# -- AppImage --------------------------------------------------------------------

def inspect_appimage(upload: Path, work: Path) -> dict:
    with upload.open("rb") as stream:
        header = stream.read(64)
    if header[4] != 2:
        raise Refused("Only 64-bit AppImages are accepted.")
    machine = struct.unpack("<H", header[18:20])[0]
    shoff = struct.unpack("<Q", header[40:48])[0]
    shentsize, shnum = struct.unpack("<HH", header[58:62])
    offset = shoff + shentsize * shnum
    root = work / "files"
    extracted = run(["unsquashfs", "-q", "-n", "-o", str(offset), "-d", str(root), str(upload)],
                    check=False, timeout=900)
    if extracted.returncode != 0:
        raise Refused("This AppImage couldn't be opened. Depot reads type 2 AppImages with a "
                      "SquashFS payload.")
    desktop = sorted(str(p.relative_to(root)) for p in root.glob("*.desktop"))
    return {"format": "appimage", "arch": ELF_MACHINES.get(machine, f"machine {machine}"),
            "desktop_files": desktop, "has_apprun": (root / "AppRun").exists(),
            "scan_root": str(root), "reach": {"words": {}, "notes": []}}


# -- Malware ---------------------------------------------------------------------

def scan(paths: list[str], database: str | None) -> dict:
    if not database:
        return {"ran": False, "message": "no signature database"}
    result = run(["clamscan", f"--database={database}", "--recursive", "--infected", "--no-summary",
                  "--max-filesize=2000M", "--max-scansize=4000M", "--alert-exceeds-max=no",
                  "--cross-fs=yes", *paths], check=False, timeout=3600)
    found = [line for line in result.stdout.splitlines() if line.endswith(" FOUND")]
    version = run(["clamscan", f"--database={database}", "--version"], check=False).stdout.strip()
    if result.returncode not in (0, 1):
        return {"ran": False, "message": (result.stderr or result.stdout)[-400:], "version": version}
    return {"ran": True, "clean": not found, "found": found[:20], "version": version}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", type=Path, required=True)
    parser.add_argument("--format", default="")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--clamav-db")
    args = parser.parse_args(argv)
    work = args.work
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    facts: dict = {"file": args.upload.name, "bytes": args.upload.stat().st_size,
                   "sha256": sha256(args.upload), "declared_format": args.format}
    kind = sniff(args.upload)
    facts["sniffed"] = kind
    try:
        if kind == "rpm":
            facts.update(inspect_rpm(args.upload, work))
        elif kind == "deb":
            facts.update(inspect_deb(args.upload, work))
        elif kind == "appimage":
            facts.update(inspect_appimage(args.upload, work))
        elif kind == "appimage-type1":
            raise Refused("This is an old type 1 AppImage. Depot only reads type 2 AppImages.")
        elif kind == "snap":
            raise Refused("Snaps aren't accepted. Please send a Flatpak bundle.")
        elif kind == "elf":
            raise Refused("This is a bare program, not a package. Please send a Flatpak bundle.")
        else:
            facts.update(inspect_flatpak(args.upload, work))
        if args.format and args.format != facts["format"]:
            raise Refused(f"The upload was sent as {args.format}, but the file is {facts['format']}.")
        facts["scan"] = scan([str(args.upload), facts["scan_root"]], args.clamav_db)
    except Refused as refusal:
        facts["refused"] = str(refusal)
        facts["scan"] = scan([str(args.upload)], args.clamav_db)
    except subprocess.TimeoutExpired:
        facts["refused"] = "Opening this package took too long. Please make it smaller or simpler."
    json.dump(facts, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
