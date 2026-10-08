# SPDX-License-Identifier: Apache-2.0
"""System tools a person added with ``sudo dnf install`` (or apt), for My apps.

On Luma, ``dnf install htop`` adds htop to this computer's system image with
rpm-ostree (docs/decisions/038-no-install-hurdles.md). rpm-ostree records every
such request in the deployment's origin (``requested-packages``, and
``requested-local-packages`` for ``.rpm`` files). My apps lists them under
"System tools you installed" with a Remove button; nothing here reads a
catalogue, and nothing here changes the system: removal goes through
``luma-remove-added-package`` under polkit, which removes only packages that
were added, never part of Luma's image.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess

RPM_OSTREE = "/usr/bin/rpm-ostree"
RPM = "/usr/bin/rpm"
HELPER = "/usr/libexec/luma-install-commands/luma-remove-added-package"
PKEXEC = "/usr/bin/pkexec"


@dataclass(frozen=True)
class SystemTool:
    name: str
    summary: str
    version: str
    state: str      # "installed", "after-restart" (added, not yet running), "removal-pending"

    @property
    def detail(self) -> str:
        if self.state == "after-restart":
            return "Ready after the next restart"
        if self.state == "removal-pending":
            return "Removed after the next restart"
        return self.summary or (f"Version {self.version}" if self.version else "")


def _nevra_name(nevra: str) -> str:
    stem = nevra.rsplit(".", 1)[0]
    parts = stem.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else nevra


def _names(deployment: dict) -> list[str]:
    names = [str(item) for item in deployment.get("requested-packages") or []]
    for nevra in deployment.get("requested-local-packages") or []:
        name = _nevra_name(str(nevra))
        if name not in names:
            names.append(name)
    return names


def parse(status: dict, rpm_query) -> list[SystemTool]:
    """``rpm-ostree status --json`` into the rows My apps shows.

    ``rpm_query(name)`` returns ``(version, summary)`` from the running
    system's rpm database, or None when the package is not running yet.
    """
    deployments = status.get("deployments") or []
    booted = next((item for item in deployments if item.get("booted")), None)
    pending = next((item for item in deployments if item.get("staged")), None) or \
        (deployments[0] if deployments else None)
    if booted is None or pending is None:
        return []
    now = _names(pending)
    before = _names(booted)
    rows: list[SystemTool] = []
    for name in sorted(set(now) | set(before)):
        found = rpm_query(name)
        if name in now:
            state = "installed" if found else "after-restart"
        else:
            if not found:
                continue
            state = "removal-pending"
        version, summary = found or ("", "")
        rows.append(SystemTool(name, summary, version, state))
    return rows


def _rpm_query(name: str):
    result = subprocess.run([RPM, "-q", "--qf", "%{VERSION}-%{RELEASE}\t%{SUMMARY}\n", name],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    version, _, summary = result.stdout.splitlines()[0].partition("\t")
    return version, summary


def load() -> list[SystemTool]:
    result = subprocess.run([RPM_OSTREE, "status", "--json"], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=60)
    if result.returncode != 0:
        return []
    try:
        return parse(json.loads(result.stdout or "{}"), _rpm_query)
    except ValueError:
        return []


def remove(name: str) -> str:
    """Remove one added package; returns "" on success or the reason it failed."""
    result = subprocess.run([PKEXEC, HELPER, name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    if result.returncode == 0:
        return ""
    if result.returncode in (126, 127):
        return "Removing needs an administrator’s password."
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    errors = [line[len("Error:"):].strip() for line in lines if line.startswith("Error:")]
    return (errors or lines or ["The package was not removed."])[-1]
