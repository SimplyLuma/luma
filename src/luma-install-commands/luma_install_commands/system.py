# SPDX-License-Identifier: MPL-2.0
"""What this computer is and what has been added to it, read-only.

Everything here reads: whether this is an image-based (OSTree) system, what
rpm-ostree records as added to or removed from the image, and what the rpm
database holds. Nothing changes the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import subprocess
from pathlib import Path

RPM_OSTREE = "/usr/bin/rpm-ostree"
DNF5 = "/usr/bin/dnf5"
RPM = "/usr/bin/rpm"
OSTREE_BOOTED = Path("/run/ostree-booted")


def image_based(root: Path = Path("/")) -> bool:
    """True on a booted OSTree deployment (Luma, Silverblue, bootc hosts).

    A container, a toolbox or distrobox, or a chroot has no ``/run/ostree-booted``
    and keeps dnf5's ordinary behaviour.
    """
    return root.joinpath(str(OSTREE_BOOTED).lstrip("/")).exists()


def _names(values) -> list[str]:
    out = []
    for value in values or ():
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        out.append(str(value))
    return out


@dataclass
class Deployment:
    booted: bool = False
    staged: bool = False
    checksum: str = ""
    base_checksum: str = ""
    version: str = ""
    requested_packages: list[str] = field(default_factory=list)     # added by name
    packages: list[str] = field(default_factory=list)               # added and resolved
    requested_local: list[str] = field(default_factory=list)        # added from .rpm files (NEVRAs)
    requested_base_removals: list[str] = field(default_factory=list)
    base_removals: list[str] = field(default_factory=list)
    requested_base_replacements: list[str] = field(default_factory=list)
    base_replacements: list[str] = field(default_factory=list)
    live_inprogress: str = ""
    live_replaced: str = ""

    @classmethod
    def from_json(cls, item: dict) -> "Deployment":
        return cls(
            booted=bool(item.get("booted")),
            staged=bool(item.get("staged")),
            checksum=str(item.get("checksum", "")),
            base_checksum=str(item.get("base-checksum") or item.get("checksum", "")),
            version=str(item.get("version", "") or ""),
            requested_packages=_names(item.get("requested-packages")),
            packages=_names(item.get("packages")),
            requested_local=_names(item.get("requested-local-packages")),
            requested_base_removals=_names(item.get("requested-base-removals")),
            base_removals=_names(item.get("base-removals")),
            requested_base_replacements=_names(item.get("requested-base-local-replacements"))
            + _names(item.get("requested-base-replacements")),
            base_replacements=_names(item.get("base-local-replacements")),
            live_inprogress=str(item.get("live-inprogress", "") or ""),
            live_replaced=str(item.get("live-replaced", "") or ""),
        )

    @property
    def added(self) -> list[str]:
        """Every package a person added to this deployment, by name."""
        names = list(self.requested_packages)
        for nevra in self.requested_local:
            name = nevra_name(nevra)
            if name not in names:
                names.append(name)
        return names


def nevra_name(nevra: str) -> str:
    """``htop-3.4.1-1.fc44.x86_64`` -> ``htop``."""
    parts = nevra.rsplit(".", 1)[0] if nevra.count(".") else nevra
    pieces = parts.rsplit("-", 2)
    return pieces[0] if len(pieces) == 3 else nevra


@dataclass
class Status:
    deployments: list[Deployment] = field(default_factory=list)
    transaction: str = ""

    @property
    def booted(self) -> Deployment | None:
        return next((d for d in self.deployments if d.booted), None)

    @property
    def staged(self) -> Deployment | None:
        return next((d for d in self.deployments if d.staged), None)

    @property
    def pending(self) -> Deployment | None:
        """What the next boot runs: the staged deployment, else the default one."""
        return self.staged or (self.deployments[0] if self.deployments else None)


def run(argv, *, check=False, capture=True, env=None, runner=subprocess.run) -> subprocess.CompletedProcess:
    return runner(argv, check=check, text=True,
                  stdout=subprocess.PIPE if capture else None,
                  stderr=subprocess.PIPE if capture else None,
                  env=env)


def status(runner=subprocess.run) -> Status:
    result = run([RPM_OSTREE, "status", "--json"], runner=runner)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip() or "rpm-ostree status failed")
    data = json.loads(result.stdout or "{}")
    transaction = data.get("transaction") or ""
    if isinstance(transaction, (list, tuple)):
        transaction = " ".join(str(x) for x in transaction if x)
    return Status([Deployment.from_json(item) for item in data.get("deployments", [])], str(transaction))


def installed(names, runner=subprocess.run) -> dict[str, str]:
    """``{name: nevra}`` for each of ``names`` the running system's rpm database has."""
    out: dict[str, str] = {}
    for name in names:
        result = run([RPM, "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n", name], runner=runner)
        if result.returncode == 0 and result.stdout.strip():
            out[name] = result.stdout.strip().splitlines()[0]
    return out


def base_packages(runner=subprocess.run) -> set[str]:
    """Package names in the booted image itself (rpm-ostree's base database)."""
    dbpath = "/usr/lib/sysimage/rpm-ostree-base-db"
    result = run([RPM, "-qa", "--dbpath", dbpath, "--qf", "%{NAME}\n"], runner=runner)
    return set((result.stdout or "").split()) if result.returncode == 0 else set()


def invoking_user() -> str:
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or str(os.getuid())
