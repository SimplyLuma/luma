# SPDX-License-Identifier: Apache-2.0
"""Per-application userlands: a package and its dependency closure as one root.

Prototype for ADR-052 (Applications 2 and 7). Nothing in the shipped install
path uses this module; it is reached through ``luma-app-closure`` only.

A closure is built by the package's own distribution, not by Luma:

* **Debian and Ubuntu.** ``debootstrap --variant=minbase`` (Priority: required
  plus apt) from the release's signed archive, then the release's own ``apt``
  installs the package inside the new root. The minimal root of each release
  is kept in the store and brought up to date before each build, so a second
  application on the same release does not bootstrap again.
* **Fedora.** ``dnf5 --installroot`` with the release's repositories and keys.
  RPM dependencies are complete, so the root is the package, its requirements
  and the release's identity package.

Every step that runs package code (maintainer scripts, triggers, scriptlets)
runs in a bubblewrap sandbox: its own PID, IPC and UTS namespaces, the new root
(or a read-only view of the host for the tools that create it) and nothing
else from the host. Without root the sandbox is entered through a user
namespace that maps the person's subordinate IDs, so packages can create and
own their system accounts. No container engine and no base image is involved.

What a third-party package needs but does not declare is added from the same
distribution and recorded: the libraries its programs link against but its
metadata omits, the loaders for graphics and sound, and the person's locale.
The record keeps the resolved package list (the lockfile) and every software
source the package added, which becomes its update source.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import capsule_runtime as runtime_policy
from .errors import InstallerError

SCHEMA = 1
RECORD_PATH = "usr/share/luma/closure.json"

_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*")


@dataclass(frozen=True)
class Userland:
    name: str
    family: str  # "deb" or "rpm"
    release: str
    glibc: tuple[int, int]
    suite: str = ""
    mirror: str = ""
    security: str = ""
    components: tuple[str, ...] = ()
    keyring: str = ""
    snapshot: str = ""  # URL template with {stamp}, for building as of a date
    snapshot_security: str = ""

    @property
    def sources(self) -> list[str]:
        return self.sources_at("")

    def sources_at(self, stamp: str) -> list[str]:
        parts = " ".join(self.components)
        if stamp and self.snapshot:
            option, mirror = "[check-valid-until=no] ", self.snapshot.format(stamp=stamp)
            security = self.snapshot_security.format(stamp=stamp) if self.snapshot_security else ""
        else:
            option, mirror, security = "", self.mirror, self.security
        lines = [f"deb {option}{mirror} {self.suite} {parts}", f"deb {option}{mirror} {self.suite}-updates {parts}"]
        if security:
            lines.append(f"deb {option}{security} {self.suite}-security {parts}")
        return lines


_DEBIAN = ("main", "contrib", "non-free", "non-free-firmware")
_UBUNTU = ("main", "restricted", "universe", "multiverse")

# Adding a userland is a row here. Newest first within a family: selection
# walks the list and takes the first release the package resolves on.
USERLANDS: dict[str, Userland] = {u.name: u for u in (
    Userland("debian-13", "deb", "13", (2, 41), "trixie", "http://deb.debian.org/debian",
             "http://security.debian.org/debian-security", _DEBIAN, "debian-archive-keyring.gpg",
             "http://snapshot.debian.org/archive/debian/{stamp}",
             "http://snapshot.debian.org/archive/debian-security/{stamp}"),
    Userland("ubuntu-24.04", "deb", "24.04", (2, 39), "noble", "http://archive.ubuntu.com/ubuntu",
             "http://security.ubuntu.com/ubuntu", _UBUNTU, "ubuntu-archive-keyring.gpg",
             "https://snapshot.ubuntu.com/ubuntu/{stamp}", "https://snapshot.ubuntu.com/ubuntu/{stamp}"),
    Userland("debian-12", "deb", "12", (2, 36), "bookworm", "http://deb.debian.org/debian",
             "http://security.debian.org/debian-security", _DEBIAN, "debian-archive-keyring.gpg",
             "http://snapshot.debian.org/archive/debian/{stamp}",
             "http://snapshot.debian.org/archive/debian-security/{stamp}"),
    Userland("ubuntu-22.04", "deb", "22.04", (2, 35), "jammy", "http://archive.ubuntu.com/ubuntu",
             "http://security.ubuntu.com/ubuntu", _UBUNTU, "ubuntu-archive-keyring.gpg",
             "https://snapshot.ubuntu.com/ubuntu/{stamp}", "https://snapshot.ubuntu.com/ubuntu/{stamp}"),
    Userland("fedora-45", "rpm", "45", (2, 44)),
    Userland("fedora-44", "rpm", "44", (2, 43)),
)}

KEYRING_DIRECTORIES = ("/usr/share/keyrings",)
RPM_KEY_DIRECTORIES = ("/etc/pki/rpm-gpg", "/usr/share/distribution-gpg-keys/fedora")

# What a windowed application expects from a desktop system and third-party
# packages rarely declare: the GL, EGL, GBM and Vulkan loaders (drivers come
# from the device, see device_graphics), the sound client libraries, the
# Secret Service client and certificates. Recorded in the closure as "baseline".
BASELINE = {
    "deb": ("libgl1", "libegl1", "libgles2", "libgbm1", "libvulkan1", "libpulse0", "libasound2-plugins",
            "libsecret-1-0", "ca-certificates", "libglib2.0-bin"),
    "rpm": ("mesa-libGL", "mesa-libEGL", "mesa-libgbm", "libglvnd-gles", "vulkan-loader", "pulseaudio-libs",
            "alsa-plugins-pulseaudio", "libsecret", "ca-certificates", "glib2"),
}

# A toolkit's Wayland support is a separate package its applications only
# recommend; without it a Qt application falls back to X11. Added when the
# toolkit is in the tree: (package that shows the toolkit, package to add).
TOOLKIT_BASELINE = {
    "deb": (("libqt5gui5", "qtwayland5"), ("libqt5gui5t64", "qtwayland5"), ("libqt6gui6", "qt6-wayland"),
            ("libqt6gui6t64", "qt6-wayland")),
    "rpm": (("qt5-qtbase-gui", "qt5-qtwayland"), ("qt6-qtbase-gui", "qt6-qtwayland")),
}

DEB_ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64"}

BASELINE_VERSION = 1
BASELINE_FILES = ("/usr/share/luma/installer/userland-baseline.json",
                  str(Path(__file__).resolve().parent.parent / "data/userland-baseline.json"))


def baseline_for(userland: str) -> dict[str, object]:
    """{"version", "packages", "toolkit"} for a release: the recorded baseline (data/userland-baseline.json).

    The lists above are what is used when the data file is not installed."""
    family = USERLANDS[userland].family
    fallback = {"version": BASELINE_VERSION, "packages": list(BASELINE[family]),
                "toolkit": [list(pair) for pair in TOOLKIT_BASELINE[family]]}
    for candidate in (os.environ.get("LUMA_USERLAND_BASELINE", ""), *BASELINE_FILES):
        if not candidate:
            continue
        try:
            data = json.loads(Path(candidate).read_text())
            row = data["userlands"][userland]
            packages = [name for name in row["packages"] if isinstance(name, str) and _SAFE.fullmatch(name)]
            toolkit = [[str(shown), str(extra)] for shown, extra in row.get("toolkit") or []
                       if _SAFE.fullmatch(str(shown)) and _SAFE.fullmatch(str(extra))]
            if packages:
                return {"version": int(data["version"]), "packages": packages, "toolkit": toolkit}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return fallback


def host_architecture() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


# -- Package metadata ----------------------------------------------------------------

@dataclass(frozen=True)
class PackageFacts:
    kind: str
    name: str
    version: str
    architecture: str
    depends: tuple[str, ...]
    sha256: str = ""


def _run(arguments: list[str], *, timeout: int = 120, check: bool = True, **options) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False, **options)
    except FileNotFoundError as error:
        raise InstallerError(f"{arguments[0]} is not installed.") from error
    except subprocess.TimeoutExpired as error:
        raise InstallerError(f"{arguments[0]} did not finish in {timeout} s.") from error
    if check and result.returncode != 0:
        raise InstallerError(f"{arguments[0]} failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-1500:]}")
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def split_dependencies(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.replace("\n", " ").split(",") if part.strip())


def read_package(path: Path) -> PackageFacts:
    """Name, version, architecture and declared dependencies of a package file."""
    suffix = path.suffix.lower()
    if suffix == ".deb":
        fields = {}
        for name in ("Package", "Version", "Architecture", "Pre-Depends", "Depends"):
            fields[name] = _run(["dpkg-deb", "-f", str(path), name]).stdout.strip()
        return PackageFacts("deb", fields["Package"], fields["Version"], fields["Architecture"],
                            split_dependencies(fields["Pre-Depends"]) + split_dependencies(fields["Depends"]),
                            file_sha256(path))
    if suffix == ".rpm":
        head = _run(["rpm", "-qp", "--nosignature", "--qf", "%{NAME}\n%{EVR}\n%{ARCH}\n", str(path)]).stdout.split("\n")
        requires = _run(["rpm", "-qp", "--nosignature", "--requires", str(path)]).stdout.splitlines()
        return PackageFacts("rpm", head[0], head[1], head[2], tuple(line.strip() for line in requires if line.strip()),
                            file_sha256(path))
    if suffix == ".appimage":
        return PackageFacts("appimage", path.stem, "", host_architecture(), (), file_sha256(path))
    raise InstallerError("Closures are built from .deb, .rpm and .AppImage files.")


_GLIBC_FLOOR = re.compile(r"^libc6(?::\w+)?\s*\(\s*>=\s*(\d+)\.(\d+)")
_RPM_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")
# Library package names that exist only from the 64-bit time_t transition on
# (Debian 13, Ubuntu 24.04), and ones that were dropped by then.
_T64 = re.compile(r"^lib[a-z0-9.+-]+t64(\s|$|:)")


def glibc_floor(facts: PackageFacts) -> tuple[int, int]:
    """The newest glibc the package says it needs, or (0, 0)."""
    floor = (0, 0)
    for dependency in facts.depends:
        for alternative in dependency.split("|"):
            match = _GLIBC_FLOOR.match(alternative.strip()) if facts.kind == "deb" else None
            if match:
                floor = max(floor, (int(match.group(1)), int(match.group(2))))
        if facts.kind == "rpm":
            for match in _RPM_GLIBC.finditer(dependency):
                floor = max(floor, (int(match.group(1)), int(match.group(2))))
    return floor


def candidate_userlands(facts: PackageFacts, prefer: tuple[str, ...] = ()) -> list[str]:
    """Userlands that can hold the package, best first, from its metadata alone.

    This only rules releases out (a glibc that is too old, library names from
    the wrong side of the 64-bit time_t rename). The release actually used is
    the first of these on which the distribution's package manager resolves
    every dependency; ``build`` walks the list.
    """
    family = "rpm" if facts.kind in {"rpm", "appimage", "portable"} else "deb"
    floor = glibc_floor(facts)
    names = []
    wants_t64 = family == "deb" and any(_T64.match(alternative.strip())
                                        for dependency in facts.depends for alternative in dependency.split("|")
                                        if "|" not in dependency)
    for userland in USERLANDS.values():
        if userland.family != family or userland.glibc < floor:
            continue
        if wants_t64 and userland.glibc < (2, 39):
            continue
        names.append(userland.name)
    ordered = [name for name in prefer if name in names]
    return ordered + [name for name in names if name not in ordered]


# -- The build sandbox ---------------------------------------------------------------

BUILD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def userns_prefix() -> list[str]:
    """Without root: a user namespace with the person as root and their
    subordinate IDs as every other account, as rootless containers use."""
    if os.geteuid() == 0:
        return []
    return ["unshare", "--user", "--map-root-user", "--map-auto", "--"]


def _resolver() -> list[str]:
    try:
        resolved = Path("/etc/resolv.conf").resolve(strict=True)
    except OSError:
        return []
    return [str(resolved)]


def sandbox_in_tree(root: Path, command: list[str], *, binds: list[tuple[str, str, bool]] | None = None,
                    network: bool = True) -> list[str]:
    """Run inside the new root: the root is / and nothing of the host is visible."""
    arguments = [*userns_prefix(), "bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                 "--unshare-cgroup-try", "--hostname", "luma-build", "--bind", str(root), "/",
                 "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/run", "--perms", "1777", "--tmpfs", "/tmp"]
    if not network:
        arguments.append("--unshare-net")
    else:
        for resolver in _resolver():
            arguments.extend(("--ro-bind", resolver, "/etc/resolv.conf"))
    for source, destination, writable in binds or []:
        arguments.extend(("--bind" if writable else "--ro-bind", source, destination))
    arguments.extend(("--clearenv", "--setenv", "PATH", BUILD_PATH, "--setenv", "HOME", "/root",
                      "--setenv", "LC_ALL", "C.UTF-8", "--setenv", "DEBIAN_FRONTEND", "noninteractive",
                      "--setenv", "TERM", "dumb", "--chdir", "/", "--", *command))
    return arguments


HOST_FOLDERS = ("usr", "etc", "var", "opt", "bin", "sbin", "lib", "lib64")


def sandbox_on_host(command: list[str], *, binds: list[tuple[str, str, bool]], target: str = "",
                    host: Path = Path("/")) -> list[str]:
    """Run a host tool that creates a root: the host's system folders read-only, no home.

    ``target`` is where the new root is bound; it gets /dev and /proc of its
    own, which package scripts run by chroot expect."""
    arguments = [*userns_prefix(), "bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                 "--unshare-cgroup-try"]
    for name in HOST_FOLDERS:
        path = host / name
        if path.is_symlink():
            arguments.extend(("--symlink", os.readlink(path), f"/{name}"))
        elif path.is_dir():
            arguments.extend(("--ro-bind", str(path), f"/{name}"))
    arguments.extend(("--dev", "/dev", "--proc", "/proc", "--perms", "1777", "--tmpfs", "/tmp", "--tmpfs", "/run"))
    # Homes stay out; the tools' own scratch folders are empty and writable.
    for hidden in ("/var/home", "/var/roothome", "/var/tmp", "/var/cache", "/var/log"):
        if (host / hidden.lstrip("/")).is_dir() and not (host / hidden.lstrip("/")).is_symlink():
            arguments.extend(("--tmpfs", hidden))
    for resolver in _resolver():
        if not resolver.startswith("/etc/"):
            arguments.extend(("--ro-bind", resolver, resolver))
    for source, destination, writable in binds:
        arguments.extend(("--bind" if writable else "--ro-bind", source, destination))
    if target:
        arguments.extend(("--dev", f"{target}/dev", "--proc", f"{target}/proc"))
    arguments.extend(("--clearenv", "--setenv", "PATH", BUILD_PATH, "--setenv", "HOME", "/tmp",
                      "--setenv", "LC_ALL", "C.UTF-8", "--setenv", "TERM", "dumb", "--chdir", "/tmp", "--", *command))
    return arguments


class BuildLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.started = time.monotonic()
        self.steps: list[dict[str, object]] = []

    def run(self, step: str, arguments: list[str], *, timeout: int = 3600, check: bool = True) -> subprocess.CompletedProcess[str]:
        began = time.monotonic()
        result = _run(arguments, timeout=timeout, check=False)
        self.steps.append({"step": step, "seconds": round(time.monotonic() - began, 1), "status": result.returncode})
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"== {step} ({result.returncode}, {time.monotonic() - began:.1f} s)\n$ {shlex.join(arguments)}\n")
                handle.write(result.stdout[-200_000:] + result.stderr[-50_000:] + "\n")
        if check and result.returncode != 0:
            raise InstallerError(f"{step} failed ({result.returncode}): {(result.stderr + result.stdout).strip()[-1800:]}")
        return result


# -- Debian and Ubuntu ---------------------------------------------------------------

def find_keyring(name: str) -> Path:
    for directory in (os.environ.get("LUMA_USERLAND_KEYS", ""), *KEYRING_DIRECTORIES):
        if directory and (Path(directory) / name).is_file():
            return Path(directory) / name
    raise InstallerError(f"The archive keyring {name} is not on this system, so the distribution cannot be verified.")


def debootstrap_command(userland: Userland, root: Path, cache: Path, architecture: str, stamp: str = "") -> list[str]:
    mirror = userland.snapshot.format(stamp=stamp) if stamp and userland.snapshot else userland.mirror
    return ["debootstrap", "--foreign", "--variant=minbase", "--merged-usr", "--force-check-gpg",
            f"--arch={DEB_ARCHITECTURES[architecture]}", f"--keyring={find_keyring(userland.keyring)}",
            "--components=main", f"--cache-dir={cache}", userland.suite, str(root), mirror]


POLICY_RC = "#!/bin/sh\n# No service starts while an application's userland is built.\nexit 101\n"
# The root is scratch until it is committed; syncing every file to disk as it is unpacked buys nothing.
DPKG_BUILD = "force-unsafe-io\n"
DPKG_BUILD_PATH = "etc/dpkg/dpkg.cfg.d/luma-build"

DEB_BASE_SCRIPT = r"""
set -e
printf '%s\n' "$LUMA_SOURCES" > /etc/apt/sources.list
rm -f /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list.d/ubuntu.sources
for arch in $LUMA_FOREIGN; do dpkg --add-architecture "$arch"; done
apt-get -q update
apt-get -q -y -o Dpkg::Options::=--force-confold dist-upgrade
"""

DEB_INSTALL_SCRIPT = r"""
set -e
if [ -d /tmp/luma-added ]; then
  # A source the package added the last time it was built: usually HTTPS.
  apt-get -q -y --no-install-recommends install ca-certificates
  cp -r /tmp/luma-added/. /
  apt-get -q update
fi
apt-get -q -y --no-install-recommends -o Dpkg::Options::=--force-confold install "$@"
"""

# One package per line, tab separated; the lockfile.
DEB_LOCK = ["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\n"]

DEB_SOURCE_PATHS = ("etc/apt/sources.list.d", "etc/apt/trusted.gpg.d", "etc/apt/keyrings", "usr/share/keyrings")
RPM_SOURCE_PATHS = ("etc/yum.repos.d", "etc/pki/rpm-gpg")


def snapshot_files(root: Path, folders: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for folder in folders:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.is_file() and not path.is_symlink():
                try:
                    found[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    continue
    return found


def added_repositories(root: Path, before: dict[str, str], folders: tuple[str, ...]) -> list[dict[str, str]]:
    """Software sources and keys the package's own scripts added to the root."""
    import base64
    added = []
    for relative, digest in snapshot_files(root, folders).items():
        if before.get(relative) == digest:
            continue
        data = (root / relative).read_bytes()
        if len(data) > 256 * 1024:
            continue
        added.append({"path": relative, "sha256": digest, "base64": base64.b64encode(data).decode("ascii")})
    return added


def restore_repositories(added: list[dict[str, str]], destination: Path) -> None:
    import base64
    for entry in added:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(entry["base64"]))


def parse_lock(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 3 and parts[0] and (len(parts) < 4 or parts[3].startswith("ii")):
            rows.append(parts[:3])
    return sorted(rows)


def parse_simulation(text: str) -> list[list[str]]:
    """apt-get -s: "Inst name [old] (new origin [arch])" for each change."""
    changes = []
    for line in text.splitlines():
        match = re.match(r"Inst (\S+) (?:\[(\S+)\] )?\((\S+) ", line)
        if match:
            changes.append([match.group(1), match.group(2) or "", match.group(3)])
    return changes


# -- Fedora ---------------------------------------------------------------------------

FEDORA_REPOSITORIES = """[fedora]
name=Fedora $releasever
metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=0
gpgkey=file:///.luma-build/keys/RPM-GPG-KEY-fedora-$releasever-$basearch
skip_if_unavailable=False

[updates]
name=Fedora $releasever updates
metalink=https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever&arch=$basearch
enabled={updates}
gpgcheck=1
repo_gpgcheck=0
gpgkey=file:///.luma-build/keys/RPM-GPG-KEY-fedora-$releasever-$basearch
skip_if_unavailable=False
"""

# RPM dependencies are complete, so there is no "required" set to start from: the
# release's identity, a shell and the text tools package scripts and the
# library check below take for granted.
RPM_BASE = ("filesystem", "setup", "fedora-release", "fedora-repos", "glibc-minimal-langpack", "bash", "coreutils",
            "grep", "gawk", "sed", "findutils")

RPM_LOCK = ["rpm", "--root", "/target", "-qa", "--qf", "%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n"]


def find_rpm_keys(release: str) -> Path:
    for directory in (os.environ.get("LUMA_USERLAND_KEYS", ""), *RPM_KEY_DIRECTORIES):
        if directory and (Path(directory) / f"RPM-GPG-KEY-fedora-{release}-primary").is_file():
            return Path(directory)
    raise InstallerError(f"The Fedora {release} signing key is not on this system, so its packages cannot be verified.")


def dnf_command(userland: Userland, architecture: str, verb: list[str]) -> list[str]:
    forced = [] if architecture == host_architecture() else [f"--forcearch={architecture}"]
    return ["dnf5", "-y", "--installroot=/target", f"--releasever={userland.release}", *forced,
            "--setopt=reposdir=/.luma-build/repos", "--setopt=cachedir=/.luma-build/cache", "--setopt=keepcache=1",
            "--setopt=install_weak_deps=False", "--setopt=gpgcheck=1", "--setopt=localpkg_gpgcheck=0", *verb]


# -- The closure ----------------------------------------------------------------------

@dataclass
class Spec:
    app_id: str
    kind: str                      # deb | rpm | appimage | portable
    userland: str
    package: str = ""              # package name
    package_file: str = ""         # local file, when it came as one
    sha256: str = ""
    architecture: str = field(default_factory=host_architecture)
    foreign_architectures: tuple[str, ...] = ()
    extra_packages: tuple[str, ...] = ()
    language: str = ""
    stamp: str = ""                # deb: build as of this archive snapshot
    updates: bool = True           # rpm: include the updates repository
    added: list[dict[str, str]] = field(default_factory=list)
    from_repository: bool = False  # install by name from a source the package added
    windowed: bool = True
    command: str = ""              # an unpacked folder (archive): the program to start, under /opt/ID
    display_name: str = ""


@dataclass
class Built:
    root: Path
    record: dict[str, object]


def _clean_tree(root: Path) -> None:
    """Remove what belongs to the build, not to the application."""
    for relative in ("usr/sbin/policy-rc.d", DPKG_BUILD_PATH, "etc/resolv.conf", "etc/hostname", "etc/machine-id", "debootstrap",
                     ".luma-build", "var/lib/dbus/machine-id", "var/cache/ldconfig/aux-cache",
                     "var/log/dpkg.log", "var/log/alternatives.log", "var/log/bootstrap.log", "var/log/apt",
                     "var/log/dnf5.log", "var/lib/dnf", "var/cache/debconf/config.dat-old",
                     "var/cache/debconf/templates.dat-old", "var/lib/dpkg/status-old", "var/lib/dpkg/diversions-old",
                     "var/cache/apt/pkgcache.bin", "var/cache/apt/srcpkgcache.bin"):
        path = root / relative
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    for relative in ("dev", "run", "tmp", "proc", "sys", "var/tmp", "var/lib/apt/lists", "var/cache/apt/archives",
                     "var/cache/dnf", "var/cache/libdnf5"):
        folder = root / relative
        if not folder.is_dir() or folder.is_symlink():
            continue
        for child in folder.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def _make_readable(root: Path) -> list[str]:
    """Give the owner read access to everything in the tree; returns what changed.

    A store whose files are plain files owned by the person cannot check out or
    verify a file its owner may not read (Fedora ships /etc/shadow with mode
    0000). Inside the application's namespace the owner is the person anyway."""
    changed = []
    top = os.lstat(root).st_mode
    if top & 0o700 != 0o700:
        os.chmod(root, (top & 0o7777) | 0o700)
        changed.append(".")
    for folder, directories, files in os.walk(root):
        for name in directories:
            path = os.path.join(folder, name)
            mode = os.lstat(path).st_mode
            if not os.path.islink(path) and mode & 0o700 != 0o700:
                os.chmod(path, (mode & 0o7777) | 0o700)
                changed.append(os.path.relpath(path, root))
        for name in files:
            path = os.path.join(folder, name)
            status = os.lstat(path)
            if (status.st_mode & 0o170000) == 0o100000 and not status.st_mode & 0o400:
                os.chmod(path, (status.st_mode & 0o7777) | 0o400)
                changed.append(os.path.relpath(path, root))
    return sorted(changed)


CLEAN_HELPER = ("import sys; from luma_installer import closure; from pathlib import Path; "
                "closure._clean_tree(Path(sys.argv[1])); print('\\n'.join(closure._make_readable(Path(sys.argv[1]))))")


def _python_in_userns(code: str, *arguments: str) -> list[str]:
    """Trees built without root hold files owned by subordinate IDs; whatever
    touches them from outside the sandbox runs in the same ID mapping."""
    package_root = str(Path(__file__).resolve().parent.parent)
    return [*userns_prefix(), "env", f"PYTHONPATH={package_root}", "python3", "-c", code, *arguments]


def remove_tree(path: Path) -> None:
    if path.exists():
        _run([*userns_prefix(), "rm", "-rf", "--one-file-system", str(path)], timeout=1800)


def _language(spec: Spec) -> str:
    value = (spec.language or os.environ.get("LANG", "")).split(".", 1)[0]
    return value if re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}(@[a-z]+)?", value) else ""


def _package_files(root: Path, spec: Spec, log: BuildLog) -> str:
    if spec.kind == "deb":
        return log.run("list files", sandbox_in_tree(root, ["dpkg-query", "-L", spec.package], network=False),
                       check=False).stdout
    if spec.kind == "rpm":
        return log.run("list files", sandbox_on_host(["rpm", "--root", "/target", "-ql", spec.package],
                                                     binds=[(str(root), "/target", True)]), check=False).stdout
    return "\n".join("/" + str(path.relative_to(root)) for path in (root / "opt" / spec.app_id).rglob("*"))


def _settle_windowed(root: Path, spec: Spec, log: BuildLog) -> None:
    """A package whose launcher asks for a terminal draws no window: it gets no desktop baseline."""
    if spec.windowed and _exports(root, spec, _package_files(root, spec, log)).get("terminal"):
        spec.windowed = False


def _library_check(root: Path, spec: Spec, files: str, exports: dict[str, object], log: BuildLog,
                   step: str = "check libraries") -> dict[str, object]:
    """What the launcher's program needs and the tree does not have (tree_check.py)."""
    command = str(exports.get("command") or "")
    listing, cache_text = root.parent / "files.list", root.parent / "ld.so.cache.txt"
    listing.write_text(files[:8_000_000])
    ldconfig = "/sbin/ldconfig.real" if (root / "usr/sbin/ldconfig.real").exists() else "/sbin/ldconfig"
    cache_text.write_text(log.run("library index", sandbox_in_tree(root, [ldconfig, "-p"], network=False), check=False).stdout)
    extra = [str(program) for program in exports.get("programs_hint") or []]
    code = "import sys; from luma_installer import tree_check; raise SystemExit(tree_check.main(sys.argv[1:]))"
    result = log.run(step, _python_in_userns(code, str(root), command, str(listing), str(cache_text), *extra), check=False)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"programs": [], "missing": [], "plugin_missing": [], "loaded": 0, "error": result.stderr.strip()[-300:]}


def _bus_names(root: Path, spec: Spec, files: str, exports: dict[str, object], programs: list[str], log: BuildLog) -> list[str]:
    """The session-bus names this application may own (bus_names.py)."""
    from . import bus_names

    def read(path: str) -> str | None:
        try:
            return (root / path.lstrip("/")).read_text(errors="replace")[:65536]
        except OSError:
            return None
    prefix = f"/opt/{spec.app_id}" if spec.kind in {"appimage", "portable"} else ""
    declared = bus_names.declared(files.splitlines(), read, prefix)
    words = bus_names.identity_words(spec.package or "", str(exports.get("name") or ""),
                                     Path(str(exports.get("desktop_path") or "")).stem)
    command = bus_names.scan_command(words, programs[:4])
    tokens: list[str] = []
    if command:
        tokens = log.run("bus names", sandbox_in_tree(root, command, network=False), check=False, timeout=300).stdout.splitlines()
    return bus_names.select([Path(str(exports.get("desktop_path") or "")).stem], declared,
                            [str(exports.get("startup_wm_class") or "")], bus_names.embedded(tokens[:20000], words))


def _exports(root: Path, spec: Spec, files: str) -> dict[str, object]:
    """What the application shows the desktop: its launcher, command and icon."""
    from .desktop import desktop_is_console, desktop_mime_types, desktop_window_class, parse_desktop
    lines = [line.strip() for line in files.splitlines() if line.strip().endswith(".desktop")]
    unpacked = spec.kind in {"appimage", "portable"}
    if unpacked:
        paths = [line for line in lines if Path(line).parent == Path("/opt") / spec.app_id]
    else:
        paths = [line for line in lines if "/applications/" in line]
    best, best_score = "", -10**9
    try:
        from .backends import _launcher_score
    except ImportError:  # the scoring lives with today's installer
        _launcher_score = None
    readable = []
    for path in paths:
        try:
            readable.append((path, (root / path.lstrip("/")).read_text(errors="replace")))
        except OSError:
            continue
    # A launcher the package hides from the applications list (a link handler: VS Code's
    # "code --open-url") is not how the application is opened, whatever its name scores.
    shown = [entry for entry in readable if "NoDisplay=true" not in entry[1] and "Hidden=true" not in entry[1]]
    for path, contents in shown or readable:
        score = _launcher_score(spec.package or spec.app_id, path, contents) if _launcher_score else 0
        if score > best_score:
            best, best_score = path, score
    if not best:
        found: dict[str, object] = {"chromium": runtime_policy.is_chromium(files)}
        if unpacked and spec.command:
            # A folder with no launcher of its own: the program its archive was recognised by.
            found.update({"name": spec.display_name or spec.app_id, "comment": "", "icon": "", "command": spec.command,
                          "startup_wm_class": Path(spec.command).name, "terminal": False, "mime_types": [], "desktop_path": ""})
        return found
    contents = (root / best.lstrip("/")).read_text(errors="replace")
    name, comment, icon, command = parse_desktop(contents)
    hint = []
    if unpacked:
        # The launcher inside the folder names a program by its bare name; the folder is not on PATH.
        first = command.split()[0] if command.split() else ""
        hint = [line for line in files.splitlines() if first and Path(line.strip()).name == Path(first).name][:4]
        command = spec.command or f"/opt/{spec.app_id}/AppRun"
    return {"desktop_path": best, "name": name, "comment": comment, "icon": icon, "command": command,
            "startup_wm_class": desktop_window_class(contents, best), "terminal": desktop_is_console(contents),
            "mime_types": desktop_mime_types(contents), "chromium": runtime_policy.is_chromium(files),
            **({"programs_hint": hint} if hint else {})}


def _bootstrap_deb(userland: Userland, root: Path, work: Path, cache: Path, spec: Spec, log: BuildLog,
                   lists: tuple[str, str, bool], archives: tuple[str, str, bool]) -> None:
    bootstrap_cache = cache / "debootstrap" / (userland.name + (f"-{spec.stamp}" if spec.stamp else ""))
    bootstrap_cache.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True)
    log.run("bootstrap", sandbox_on_host(
        debootstrap_command(userland, root, bootstrap_cache, spec.architecture, spec.stamp),
        binds=[(str(work), str(work), True), (str(bootstrap_cache), str(bootstrap_cache), True)]))
    _write_in_tree(root, DPKG_BUILD_PATH, DPKG_BUILD)
    log.run("bootstrap, second stage", sandbox_in_tree(root, ["/debootstrap/debootstrap", "--second-stage"]))
    _write_in_tree(root, "usr/sbin/policy-rc.d", POLICY_RC, 0o755)
    log.run("update userland", sandbox_in_tree(root, [
        "env", "LUMA_SOURCES=" + "\n".join(userland.sources_at(spec.stamp)), "LUMA_FOREIGN=", "sh", "-c", DEB_BASE_SCRIPT],
        binds=[lists, archives]))


def ensure_userland(name: str, architecture: str, work: Path, cache: Path, store, log: BuildLog) -> str:
    """The release's minimal root as a ref in the store, bootstrapped once."""
    userland = USERLANDS[name]
    ref = store.userland_ref(name, architecture)
    if store.has_ref(ref):
        return ref
    target = Path(tempfile.mkdtemp(prefix=f"userland-{name}-", dir=work))
    try:
        root = target / "root"
        spec = Spec(app_id=name, kind="deb", userland=name, architecture=architecture)
        _bootstrap_deb(userland, root, target, cache, spec, log, _lists_bind(cache, userland, ""),
                       _archives_bind(cache, userland))
        lock = parse_lock(log.run("lock", sandbox_in_tree(root, DEB_LOCK, network=False)).stdout)
        _run(_python_in_userns(CLEAN_HELPER, str(root)), timeout=600)
        store.commit_tree(root, ref, {"schema": SCHEMA, "kind": "userland", "id": name, "userland": name,
                                      "architecture": architecture, "packages": lock},
                          subject=f"{name} minimal root")
    finally:
        remove_tree(target)
    return ref


def _lists_bind(cache: Path, userland: Userland, stamp: str) -> tuple[str, str, bool]:
    lists = cache / "apt-lists" / (userland.name + (f"-{stamp}" if stamp else ""))
    lists.mkdir(parents=True, exist_ok=True)
    return (str(lists), "/var/lib/apt/lists", True)


def _archives_bind(cache: Path, userland: Userland) -> tuple[str, str, bool]:
    archives = cache / "apt" / userland.name
    archives.mkdir(parents=True, exist_ok=True)
    return (str(archives), "/var/cache/apt/archives", True)


def _build_deb(spec: Spec, userland: Userland, work: Path, cache: Path, store, log: BuildLog) -> Built:
    root = work / "root"
    lists, archives = _lists_bind(cache, userland, spec.stamp), _archives_bind(cache, userland)
    common = [lists, archives]
    if store is not None and not spec.stamp:
        store.export(ensure_userland(userland.name, spec.architecture, work, cache, store, log), root)
        _write_in_tree(root, DPKG_BUILD_PATH, DPKG_BUILD)
        _write_in_tree(root, "usr/sbin/policy-rc.d", POLICY_RC, 0o755)
        log.run("update userland", sandbox_in_tree(root, [
            "env", "LUMA_SOURCES=" + "\n".join(userland.sources), "LUMA_FOREIGN=" + " ".join(spec.foreign_architectures),
            "sh", "-c", DEB_BASE_SCRIPT], binds=common))
    else:
        _bootstrap_deb(userland, root, work, cache, spec, log, lists, archives)
        if spec.foreign_architectures:
            log.run("update userland", sandbox_in_tree(root, [
                "env", "LUMA_SOURCES=" + "\n".join(userland.sources_at(spec.stamp)),
                "LUMA_FOREIGN=" + " ".join(spec.foreign_architectures), "sh", "-c", DEB_BASE_SCRIPT], binds=common))
    if spec.package_file or spec.added:
        # A vendor's package registers its repository while it is installed, and converts its signing
        # key with gpg when gpg is there (VS Code). Without it the source is written and its key is
        # not: the repository is "not signed" for ever after and no update from the vendor arrives.
        log.run("signing tool", sandbox_in_tree(root, ["sh", "-c", DEB_INSTALL_SCRIPT, "sh", "gpg"], binds=common), check=False)
    before = snapshot_files(root, DEB_SOURCE_PATHS)
    binds = list(common)
    if spec.added and spec.from_repository:
        staging = work / "added"
        restore_repositories(spec.added, staging)
        binds.append((str(staging), "/tmp/luma-added", False))
        target = spec.package
    elif spec.package_file:
        binds.append((spec.package_file, "/tmp/luma-package.deb", False))
        target = "/tmp/luma-package.deb"
    else:
        target = spec.package
    log.run("install", sandbox_in_tree(root, ["sh", "-c", DEB_INSTALL_SCRIPT, "sh", target, *spec.extra_packages], binds=binds))
    _settle_windowed(root, spec, log)
    baseline: list[str] = []
    recorded = baseline_for(userland.name)
    if spec.windowed:
        baseline = list(recorded["packages"])  # type: ignore[arg-type]
        result = log.run("baseline", sandbox_in_tree(root, ["sh", "-c", DEB_INSTALL_SCRIPT, "sh", *baseline], binds=common),
                         check=False)
        if result.returncode != 0:
            for name in baseline:
                log.run(f"baseline {name}", sandbox_in_tree(root, ["sh", "-c", DEB_INSTALL_SCRIPT, "sh", name], binds=common),
                        check=False)
    if spec.windowed:
        present = {row[0] for row in parse_lock(log.run("lock", sandbox_in_tree(root, DEB_LOCK, network=False)).stdout)}
        toolkit = sorted({extra for shown, extra in recorded["toolkit"] if shown in present and extra not in present})  # type: ignore[union-attr]
        if toolkit:
            log.run("toolkit baseline", sandbox_in_tree(root, ["sh", "-c", DEB_INSTALL_SCRIPT, "sh", *toolkit], binds=common),
                    check=False)
            baseline += toolkit
    language = _language(spec)
    if language:
        log.run("locale", sandbox_in_tree(root, ["sh", "-c", runtime_policy.DEBIAN_LOCALE_SCRIPT, "sh", language],
                                          binds=common), check=False)
    files = _package_files(root, spec, log)
    exports = _exports(root, spec, files)
    checked = _library_check(root, spec, files, exports, log)
    undeclared: list[str] = []
    for soname in [*checked["missing"], *checked["plugin_missing"]]:  # type: ignore[misc]
        for candidate in runtime_policy.deb_package_candidates(soname):
            known = log.run(f"find {soname}", sandbox_in_tree(root, ["apt-cache", "show", "--no-all-versions", candidate],
                                                               binds=[lists], network=False), check=False)
            if known.returncode == 0 and known.stdout.strip():
                undeclared.append(candidate)
                break
    if undeclared:
        log.run("undeclared libraries", sandbox_in_tree(
            root, ["sh", "-c", DEB_INSTALL_SCRIPT, "sh", *sorted(set(undeclared))], binds=common), check=False)
        checked = _library_check(root, spec, files, exports, log, "check libraries again")
    exports["bus_names"] = _bus_names(root, spec, files, exports, list(checked["programs"]), log)  # type: ignore[arg-type]
    lock = parse_lock(log.run("lock", sandbox_in_tree(root, DEB_LOCK, network=False)).stdout)
    added = added_repositories(root, before, DEB_SOURCE_PATHS)
    record = _record(spec, userland, lock, added or spec.added, baseline, sorted(set(undeclared)), language, exports, log,
                     checked, int(recorded["version"]))  # type: ignore[call-overload]
    return Built(root, record)


def _write_in_tree(root: Path, relative: str, text: str, mode: int = 0o644) -> None:
    code = ("import sys, os; p = sys.argv[1]; os.makedirs(os.path.dirname(p), exist_ok=True); "
            "open(p, 'w').write(sys.argv[2]); os.chmod(p, int(sys.argv[3]))")
    _run([*userns_prefix(), "python3", "-c", code, str(root / relative), text, str(mode)])


def _rpm_binds(root: Path, conf: Path, keys: Path, dnf_cache: Path) -> list[tuple[str, str, bool]]:
    # dnf5 looks for its configuration under the install root or under /,
    # depending on the option; the same folder is at both.
    binds = [(str(root), "/target", True)]
    for prefix in ("", "/target"):
        binds += [(str(conf), f"{prefix}/.luma-build", True), (str(keys), f"{prefix}/.luma-build/keys", False),
                  (str(dnf_cache), f"{prefix}/.luma-build/cache", True)]
    return binds


def _build_rpm(spec: Spec, userland: Userland, work: Path, cache: Path, store, log: BuildLog,
               payload: Path | None = None) -> Built:
    root = work / "root"
    root.mkdir(parents=True)
    conf = work / "conf"
    for name in ("repos", "keys", "cache"):
        (conf / name).mkdir(parents=True)
    (conf / "repos/fedora.repo").write_text(FEDORA_REPOSITORIES.replace("{updates}", "1" if spec.updates else "0"))
    keys = find_rpm_keys(userland.release)
    dnf_cache = cache / "dnf" / userland.name
    dnf_cache.mkdir(parents=True, exist_ok=True)
    binds = _rpm_binds(root, conf, keys, dnf_cache)
    packages = list(RPM_BASE)
    language = _language(spec)
    pack = runtime_policy.language_pack("rpm", language) if language else ""
    if pack:
        packages.append(pack)
    before: dict[str, str] = {}
    if spec.kind == "rpm":
        if spec.added and spec.from_repository:
            added_dir = conf / "repos"
            for entry in spec.added:
                if entry["path"].startswith("etc/yum.repos.d/"):
                    restore_repositories([{**entry, "path": Path(entry["path"]).name}], added_dir)
            target = spec.package
        elif spec.package_file:
            binds.append((spec.package_file, "/tmp/luma-package.rpm", False))
            target = "/tmp/luma-package.rpm"
        else:
            target = spec.package
        log.run("userland", sandbox_on_host(dnf_command(userland, spec.architecture, ["install", *packages]), binds=binds, target="/target"))
        before = snapshot_files(root, RPM_SOURCE_PATHS)
        log.run("install", sandbox_on_host(dnf_command(userland, spec.architecture, ["install", target, *spec.extra_packages]),
                                           binds=binds, target="/target"))
        _settle_windowed(root, spec, log)
    else:
        log.run("userland", sandbox_on_host(dnf_command(userland, spec.architecture, ["install", *packages, *spec.extra_packages]),
                                            binds=binds, target="/target"))
    baseline = []
    recorded = baseline_for(userland.name)
    if spec.windowed:
        baseline = list(recorded["packages"])  # type: ignore[arg-type]
        log.run("baseline", sandbox_on_host(dnf_command(userland, spec.architecture, ["install", "--skip-unavailable", *baseline]),
                                            binds=binds, target="/target"), check=False)
    if spec.windowed:
        present = {row[0] for row in parse_lock(log.run("lock", sandbox_on_host(RPM_LOCK, binds=[(str(root), "/target", True)])).stdout)}
        toolkit = sorted({extra for shown, extra in recorded["toolkit"] if shown in present and extra not in present})  # type: ignore[union-attr]
        if toolkit:
            log.run("toolkit baseline", sandbox_on_host(dnf_command(userland, spec.architecture, ["install", *toolkit]),
                                                        binds=binds, target="/target"), check=False)
            baseline += toolkit
    if payload is not None:
        destination = root / "opt" / spec.app_id
        _run([*userns_prefix(), "sh", "-c", 'mkdir -p "$2" && cp -a "$1"/. "$2"/', "sh", str(payload), str(destination)],
             timeout=1800)
    files = _package_files(root, spec, log)
    exports = _exports(root, spec, files)
    checked = _library_check(root, spec, files, exports, log)
    undeclared = [*checked["missing"], *checked["plugin_missing"]]  # type: ignore[misc]
    if undeclared:
        suffix = "()(64bit)" if spec.architecture in {"x86_64", "aarch64"} else ""
        log.run("undeclared libraries", sandbox_on_host(dnf_command(
            userland, spec.architecture, ["install", "--skip-unavailable", *[f"{name}{suffix}" for name in undeclared]]),
            binds=binds, target="/target"), check=False)
        checked = _library_check(root, spec, files, exports, log, "check libraries again")
    exports["bus_names"] = _bus_names(root, spec, files, exports, list(checked["programs"]), log)  # type: ignore[arg-type]
    lock = parse_lock(log.run("lock", sandbox_on_host(RPM_LOCK, binds=[(str(root), "/target", True)])).stdout)
    added = added_repositories(root, before, RPM_SOURCE_PATHS) if spec.kind == "rpm" else []
    record = _record(spec, userland, lock, added or spec.added, baseline, sorted(set(undeclared)), language, exports, log,
                     checked, int(recorded["version"]))  # type: ignore[call-overload]
    return Built(root, record)


def _record(spec: Spec, userland: Userland, lock: list[list[str]], added: list[dict[str, str]], baseline: list[str],
            undeclared: list[str], language: str, exports: dict[str, object], log: BuildLog,
            checked: dict[str, object] | None = None, baseline_version: int = BASELINE_VERSION) -> dict[str, object]:
    version = next((row[1] for row in lock if row[0] == spec.package.partition(":")[0]), "")
    checked = checked or {}
    exports = {key: value for key, value in exports.items() if key != "programs_hint"}
    return {
        "schema": SCHEMA, "kind": "application", "id": spec.app_id, "format": spec.kind,
        "origin": {"package": spec.package, "version": version, "sha256": spec.sha256,
                   "file": Path(spec.package_file).name if spec.package_file else "",
                   "from_repository": spec.from_repository, "added_repositories": added},
        "userland": userland.name, "architecture": spec.architecture,
        "foreign_architectures": list(spec.foreign_architectures),
        "repositories": userland.sources_at(spec.stamp) if userland.family == "deb" else
                        ["fedora", *(["updates"] if spec.updates else [])],
        "stamp": spec.stamp, "updates": spec.updates, "extra_packages": list(spec.extra_packages),
        "baseline": baseline, "baseline_version": baseline_version, "undeclared": undeclared,
        # What the launcher's program still cannot load, and what its plugins cannot: the first fails the build's check.
        "programs": list(checked.get("programs") or []), "unresolved": list(checked.get("missing") or []),
        "plugin_unresolved": list(checked.get("plugin_missing") or []),
        "command": spec.command, "display_name": spec.display_name,
        "language": language, "windowed": spec.windowed,
        "packages": lock, "exports": exports,
        "build": {"seconds": round(time.monotonic() - log.started, 1), "steps": log.steps},
    }


def spec_for_file(path: Path, app_id: str = "", userland: str = "", **options) -> tuple[Spec, list[str]]:
    """A build spec for a package file and the userlands to try, best first."""
    facts = read_package(path)
    architecture = {"amd64": "x86_64", "arm64": "aarch64", "all": host_architecture(), "noarch": host_architecture(),
                    "i386": "x86_64"}.get(facts.architecture, facts.architecture)
    if architecture != host_architecture():
        raise InstallerError(f"This package is for {facts.architecture}; this device is {host_architecture()}.")
    candidates = [userland] if userland else candidate_userlands(facts)
    if not candidates or any(name not in USERLANDS for name in candidates):
        raise InstallerError("No supported userland can hold this package.")
    foreign = tuple(options.pop("foreign_architectures", ())) or (("i386",) if facts.architecture == "i386" else ())
    identity = app_id or re.sub(r"[^a-z0-9.+-]+", "-", facts.name.lower()).strip("-")
    if not _SAFE.fullmatch(identity):
        raise InstallerError("The application needs a plain identifier.")
    spec = Spec(app_id=identity, kind=facts.kind, userland=candidates[0], package=facts.name,
                package_file=str(path.resolve()), sha256=facts.sha256, architecture=architecture,
                foreign_architectures=foreign, **options)
    return spec, candidates


def spec_for_name(name: str, userland: str, app_id: str = "", **options) -> Spec:
    if userland not in USERLANDS:
        raise InstallerError(f"Unknown userland {userland}.")
    base, _colon, architecture = name.partition(":")
    if not _SAFE.fullmatch(base) or (architecture and not re.fullmatch(r"[a-z0-9]+", architecture)):
        raise InstallerError("Not a package name.")
    if architecture and architecture not in options.get("foreign_architectures", ()):
        # zsnes:i386, wine32:i386: the package names its architecture.
        options["foreign_architectures"] = (*options.get("foreign_architectures", ()), architecture)
    return Spec(app_id=app_id or base, kind=USERLANDS[userland].family, userland=userland, package=name, **options)


def spec_from_record(record: dict[str, object], package_file: str = "") -> Spec:
    """The spec that rebuilds a recorded application against today's repositories."""
    origin = dict(record["origin"])  # type: ignore[arg-type]
    added = list(origin.get("added_repositories") or [])
    has_source = any(entry["path"].startswith(("etc/apt/sources.list.d/", "etc/yum.repos.d/")) for entry in added)
    return Spec(app_id=str(record["id"]), kind=str(record["format"]), userland=str(record["userland"]),
                package=str(origin["package"]), package_file=package_file, sha256=str(origin.get("sha256") or ""),
                architecture=str(record["architecture"]),
                foreign_architectures=tuple(record.get("foreign_architectures") or ()),
                extra_packages=tuple(record.get("extra_packages") or ()), language=str(record.get("language") or ""),
                added=added, from_repository=bool(origin.get("from_repository")) or has_source or not origin.get("file"),
                windowed=bool(record.get("windowed", True)), command=str(record.get("command") or ""),
                display_name=str(record.get("display_name") or ""))


def build(spec: Spec, work: Path, cache: Path, store=None, log_path: Path | None = None,
          payload: Path | None = None) -> Built:
    """Build the closure for one userland. The root is left in ``work/root``."""
    userland = USERLANDS[spec.userland]
    log = BuildLog(log_path)
    for tool in ("bwrap",) + (("debootstrap",) if userland.family == "deb" else ("dnf5", "rpm")):
        if not shutil.which(tool):
            raise InstallerError(f"{tool} is needed to build a {userland.name} userland and is not installed.")
    work.mkdir(parents=True, exist_ok=True)
    if userland.family == "deb":
        built = _build_deb(spec, userland, work, cache, store, log)
    else:
        built = _build_rpm(spec, userland, work, cache, store, log, payload)
    cleaned = _run(_python_in_userns(CLEAN_HELPER, str(built.root)), timeout=600)
    built.record["made_readable"] = [line for line in cleaned.stdout.splitlines() if line]
    return built


def build_first(spec: Spec, candidates: list[str], work: Path, cache: Path, store=None,
                log_path: Path | None = None, payload: Path | None = None) -> Built:
    """Build on the first candidate userland the package resolves on."""
    failures = []
    for name in candidates:
        attempt = Spec(**{**asdict(spec), "userland": name})
        target = Path(tempfile.mkdtemp(prefix=f"{spec.app_id}-{name}-", dir=work))
        try:
            return build(attempt, target, cache, store, log_path, payload)
        except InstallerError as error:
            failures.append(f"{name}: {str(error).splitlines()[-1][:300]}")
            remove_tree(target)
    raise InstallerError("The package did not resolve on any supported userland.\n" + "\n".join(failures))


# -- Refresh --------------------------------------------------------------------------

DEB_RESOLVE_SCRIPT = r"""
set -e
printf '%s\n' "$LUMA_SOURCES" > /etc/apt/sources.list
apt-get -q update >/dev/null
apt-get -q -s -o Dpkg::Options::=--force-confold dist-upgrade
echo LUMA-URIS
apt-get -q -y --print-uris dist-upgrade | grep "^'" || true
"""


def resolve_changes(record: dict[str, object], root: Path, cache: Path, log: BuildLog) -> dict[str, object]:
    """Ask the distribution what would change in a copy of the current tree.

    Returns {"changes": [[name, old, new], ...], "download_bytes": n}."""
    userland = USERLANDS[str(record["userland"])]
    if userland.family == "deb":
        # An empty archive folder: the sizes are what a device with nothing cached would fetch.
        result = log.run("resolve", sandbox_in_tree(
            root, ["env", "LUMA_SOURCES=" + "\n".join(userland.sources), "sh", "-c", DEB_RESOLVE_SCRIPT],
            binds=[_lists_bind(cache, userland, "")]))
        simulated, _marker, uris = result.stdout.partition("LUMA-URIS")
        size = sum(int(parts[2]) for parts in (line.split() for line in uris.splitlines())
                   if len(parts) >= 3 and parts[2].isdigit())
        return {"changes": parse_simulation(simulated), "download_bytes": size}
    conf = Path(tempfile.mkdtemp(prefix="conf-", dir=root.parent))
    for name in ("repos", "keys", "cache"):
        (conf / name).mkdir()
    (conf / "repos/fedora.repo").write_text(FEDORA_REPOSITORIES.replace("{updates}", "1"))
    restore_repositories([{**entry, "path": Path(entry["path"]).name}
                          for entry in dict(record["origin"]).get("added_repositories") or []  # type: ignore[arg-type]
                          if entry["path"].startswith("etc/yum.repos.d/")], conf / "repos")
    dnf_cache = cache / "dnf" / userland.name
    dnf_cache.mkdir(parents=True, exist_ok=True)
    binds = _rpm_binds(root, conf, find_rpm_keys(userland.release), dnf_cache)
    query = ["repoquery", "--upgrades", "--qf", "%{name}\t%{evr}\t%{downloadsize}\n"]
    result = log.run("resolve", sandbox_on_host(dnf_command(userland, str(record["architecture"]), query), binds=binds, target="/target"))
    installed = {row[0]: row[1] for row in record["packages"]}  # type: ignore[union-attr]
    changes, size = [], 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] in installed and parts[2].isdigit():
            changes.append([parts[0], installed[parts[0]], parts[1]])
            size += int(parts[2])
    return {"changes": changes, "download_bytes": size}


def lock_difference(old: list[list[str]], new: list[list[str]]) -> dict[str, list[list[str]]]:
    before = {(row[0], row[2]): row[1] for row in old}
    after = {(row[0], row[2]): row[1] for row in new}
    return {
        "added": sorted([name, arch, after[(name, arch)]] for name, arch in after.keys() - before.keys()),
        "removed": sorted([name, arch, before[(name, arch)]] for name, arch in before.keys() - after.keys()),
        "changed": sorted([name, arch, before[(name, arch)], after[(name, arch)]]
                          for name, arch in before.keys() & after.keys() if before[(name, arch)] != after[(name, arch)]),
    }


def record_json(record: dict[str, object]) -> str:
    return json.dumps(record, indent=1, sort_keys=True) + "\n"
