# SPDX-License-Identifier: Apache-2.0
"""Device-package updates from a phone's device repository.

luma-device-layer layers a device's packages from its signed rpm-md repository
and leaves /etc/yum.repos.d/ID.repo pointing at it (device_layer.py). A device
release published only to that repository changes no OS commit, so the signed
graph never offers it. This module reads the repository's metadata and the
booted rpm database and lists the installed device packages the repository has
newer builds of. Only a system with a layered device kit has such a repository;
everywhere else ``repo()`` is None and nothing is read.

The listing only decides whether to offer an update. Staging goes through
rpm-ostree, which checks repomd.xml (repo_gpgcheck) and every package
(gpgcheck) against the Luma OS Release key.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import hashlib
import io
import subprocess
from typing import Callable, Sequence
from xml.etree import ElementTree

from .config import Paths
from .rpmver import rpmvercmp

__all__ = ("DeviceRepo", "DeviceUpdates", "DeviceRepoError", "Newer", "TAG")

#: The comment device_layer.repo_file_text writes into every device repository file.
TAG = "(luma-device-layer)"
LAYERED_MARKER = "/var/lib/luma-device-layer/layered"
REPOS_DIR = "/etc/yum.repos.d"
MAX_REPOMD_BYTES = 1024 * 1024
MAX_PRIMARY_BYTES = 64 * 1024 * 1024
REPO_NS = "{http://linux.duke.edu/metadata/repo}"
COMMON_NS = "{http://linux.duke.edu/metadata/common}"


class DeviceRepoError(Exception):
    """The device repository's metadata could not be read."""


@dataclass(frozen=True)
class DeviceRepo:
    id: str
    baseurl: str
    packages: tuple[str, ...]


@dataclass(frozen=True)
class Newer:
    name: str
    installed: str  # [epoch:]version-release
    available: str
    size: int = 0

    def describe(self) -> str:
        return f"{self.name} {self.available.split(':', 1)[-1]}"


def evr_compare(a: str, b: str) -> int:
    def split(evr: str) -> tuple[int, str, str]:
        epoch, _, rest = evr.rpartition(":")
        version, _, release = rest.partition("-")
        return int(epoch or 0), version, release
    ea, va, ra = split(a)
    eb, vb, rb = split(b)
    if ea != eb:
        return 1 if ea > eb else -1
    return rpmvercmp(va, vb) or rpmvercmp(ra, rb)


def _evr(epoch: str, version: str, release: str) -> str:
    return f"{int(epoch or 0)}:{version}-{release}"


def default_run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(argv), text=True, capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(list(argv), 127, "", str(error))


def decompress(data: bytes, href: str) -> bytes:
    if href.endswith(".zst"):
        try:
            from compression import zstd  # Python 3.14
        except ImportError:
            return subprocess.run(["zstd", "-dc"], input=data, capture_output=True, check=True).stdout
        return zstd.decompress(data)
    if href.endswith(".xz"):
        import lzma
        return lzma.decompress(data)
    if href.endswith(".gz"):
        import gzip
        return gzip.decompress(data)
    return data


class DeviceUpdates:
    def __init__(self, paths: Paths, http, arch: str, run: Callable = default_run) -> None:
        self.paths = paths
        self.http = http
        self.arch = arch
        self.run = run

    def repo(self) -> DeviceRepo | None:
        """The device repository luma-device-layer configured, or None."""
        if not (self.paths.root / LAYERED_MARKER.lstrip("/")).exists():
            return None
        for path in sorted((self.paths.root / REPOS_DIR.lstrip("/")).glob("*.repo")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if TAG not in text:
                continue
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(text)
            except configparser.Error:
                continue
            for section in parser.sections():
                baseurl = parser.get(section, "baseurl", fallback="").strip()
                packages = tuple(p for p in parser.get(section, "includepkgs", fallback="").replace(",", " ").split()
                                 if p)
                if baseurl.startswith("https://") and baseurl.endswith("/") and packages \
                        and parser.get(section, "enabled", fallback="1").strip() == "1":
                    return DeviceRepo(section, baseurl, packages)
        return None

    def installed(self, names: Sequence[str]) -> dict[str, str]:
        """[epoch:]version-release of each of ``names`` in the booted rpm database."""
        result = self.run(["rpm", "-q", "--qf", "%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}\\n", *names])
        found = {}
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in names:
                # Several installed builds (kernel): the newest counts.
                if parts[0] not in found or evr_compare(parts[1], found[parts[0]]) > 0:
                    found[parts[0]] = parts[1]
        return found

    def available(self, repo: DeviceRepo) -> dict[str, tuple[str, int]]:
        """Newest [epoch:]version-release and package size of each package of ``repo``."""
        repomd = self.http.get(repo.baseurl + "repodata/repomd.xml", MAX_REPOMD_BYTES)
        try:
            root = ElementTree.fromstring(repomd)
        except ElementTree.ParseError as error:
            raise DeviceRepoError(f"repomd.xml of {repo.id} is not XML: {error}") from None
        primary = next((item for item in root.iter(REPO_NS + "data") if item.get("type") == "primary"), None)
        location = primary.find(REPO_NS + "location") if primary is not None else None
        checksum = primary.find(REPO_NS + "checksum") if primary is not None else None
        href = location.get("href", "") if location is not None else ""
        if not href or ".." in href.split("/") or href.startswith("/") or ":" in href:
            raise DeviceRepoError(f"repomd.xml of {repo.id} names no usable primary metadata")
        data = self.http.get(repo.baseurl + href, MAX_PRIMARY_BYTES)
        if checksum is not None and checksum.get("type") in hashlib.algorithms_guaranteed:
            if hashlib.new(checksum.get("type"), data).hexdigest() != (checksum.text or "").strip():
                raise DeviceRepoError(f"primary metadata of {repo.id} does not match repomd.xml")
        try:
            primary_xml = decompress(data, href)
        except Exception as error:
            raise DeviceRepoError(f"primary metadata of {repo.id} could not be unpacked: {error}") from None
        wanted = set(repo.packages)
        newest: dict[str, tuple[str, int]] = {}
        try:
            for _event, element in ElementTree.iterparse(io.BytesIO(primary_xml)):
                if element.tag != COMMON_NS + "package":
                    continue
                name = element.findtext(COMMON_NS + "name") or ""
                arch = element.findtext(COMMON_NS + "arch") or ""
                version = element.find(COMMON_NS + "version")
                size = element.find(COMMON_NS + "size")
                if name in wanted and arch in (self.arch, "noarch") and version is not None:
                    evr = _evr(version.get("epoch", "0"), version.get("ver", ""), version.get("rel", ""))
                    if name not in newest or evr_compare(evr, newest[name][0]) > 0:
                        newest[name] = (evr, int(size.get("package", 0) or 0) if size is not None else 0)
                element.clear()
        except (ElementTree.ParseError, ValueError) as error:
            raise DeviceRepoError(f"primary metadata of {repo.id} is not readable: {error}") from None
        return newest

    def newer(self, repo: DeviceRepo | None = None) -> list[Newer]:
        """Installed device packages that the device repository has newer builds of."""
        repo = repo or self.repo()
        if repo is None:
            return []
        available = self.available(repo)
        installed = self.installed(sorted(available))
        return [Newer(name, installed[name], evr, size)
                for name, (evr, size) in sorted(available.items())
                if name in installed and evr_compare(evr, installed[name]) > 0]

