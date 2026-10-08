"""Read-only, best-effort inventory of observable Luma host state."""

from __future__ import annotations

import json
import os
import platform
import shlex
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .errors import InventoryError

INVENTORY_SCHEMA = "org.luma.mod-host-inventory/v0.1"
MAX_PROBE_OUTPUT = 2 * 1024 * 1024


@dataclass(frozen=True)
class ProbeResult:
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None = None


Runner = Callable[[Sequence[str], float], ProbeResult]


def run_probe(command: Sequence[str], timeout: float = 8.0) -> ProbeResult:
    """Run a fixed argument vector with limits and no shell interpretation."""

    try:
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C.UTF-8",
            },
        )
    except FileNotFoundError:
        return ProbeResult(tuple(command), None, "", "", "not-installed")
    except subprocess.TimeoutExpired:
        return ProbeResult(tuple(command), None, "", "", "timeout")
    stdout = completed.stdout[:MAX_PROBE_OUTPUT].decode("utf-8", "replace")
    stderr = completed.stderr[:MAX_PROBE_OUTPUT].decode("utf-8", "replace")
    if len(completed.stdout) > MAX_PROBE_OUTPUT or len(completed.stderr) > MAX_PROBE_OUTPUT:
        return ProbeResult(tuple(command), completed.returncode, stdout, stderr, "output-limit")
    return ProbeResult(tuple(command), completed.returncode, stdout, stderr)


def _read_regular(path: Path, limit: int = 1024 * 1024) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, PermissionError):
        return None
    except OSError as error:
        raise InventoryError(f"cannot read inventory source {path} safely: {error.strerror}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise InventoryError(f"inventory source {path} is not a bounded regular file")
        payload = os.read(descriptor, limit + 1)
        if len(payload) > limit:
            raise InventoryError(f"inventory source {path} exceeds its size limit")
        return payload.decode("utf-8", "replace")
    finally:
        os.close(descriptor)


def _os_release(path: Path) -> dict[str, str]:
    text = _read_regular(path, 64 * 1024)
    if text is None:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key in {"ID", "VERSION_ID", "VARIANT_ID", "IMAGE_ID", "IMAGE_VERSION"}:
            try:
                parsed = shlex.split(raw, posix=True)
            except ValueError:
                continue
            if len(parsed) == 1:
                values[key] = parsed[0]
    return values


def _observation(kind: str, identity: str, source: str, **details: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "identity": identity,
        "state": "observed",
        "provenance": "unmanaged-or-unknown",
        "source": source,
        "details": details,
    }


def collect_inventory(
    *,
    proc_root: Path = Path("/proc"),
    os_release_path: Path = Path("/etc/os-release"),
    runner: Runner = run_probe,
) -> dict[str, Any]:
    """Collect bounded observations without making any host changes.

    The scanner never turns an observation into a claim of ownership. Even an
    rpm-ostree requested package is labeled unmanaged-or-unknown until a Luma
    transaction record proves who introduced it.
    """

    observations: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    release = _os_release(os_release_path)
    if release:
        observations.append(_observation("base", release.get("IMAGE_ID", release.get("ID", "unknown")), str(os_release_path), **release))
    observations.append(_observation(
        "kernel", platform.release(), "platform.uname",
        architecture=platform.machine(), system=platform.system(),
    ))

    command_line = _read_regular(proc_root / "cmdline", 128 * 1024)
    if command_line is not None:
        for argument in shlex.split(command_line.strip()):
            observations.append(_observation("boot-argument", argument, str(proc_root / "cmdline")))

    modules = _read_regular(proc_root / "modules", MAX_PROBE_OUTPUT)
    if modules is not None:
        for line in modules.splitlines():
            fields = line.split()
            if fields:
                observations.append(_observation("kernel-module", fields[0], str(proc_root / "modules")))

    ostree = runner(("rpm-ostree", "status", "--json"), 12.0)
    if ostree.error or ostree.returncode not in (0, None):
        failures.append({"probe": "rpm-ostree-status", "error": ostree.error or ostree.stderr.strip() or f"exit-{ostree.returncode}"})
    elif ostree.returncode == 0:
        try:
            value = json.loads(ostree.stdout)
            deployments = value.get("deployments", []) if isinstance(value, dict) else []
            if not isinstance(deployments, list):
                raise ValueError("deployments is not a list")
            for deployment in deployments:
                if not isinstance(deployment, dict):
                    continue
                checksum = deployment.get("checksum")
                if isinstance(checksum, str):
                    observations.append(_observation(
                        "ostree-deployment", checksum, "rpm-ostree status --json",
                        booted=bool(deployment.get("booted")),
                        staged=bool(deployment.get("staged")),
                        version=deployment.get("version"),
                    ))
                for key in ("requested-packages", "requested-local-packages"):
                    packages = deployment.get(key, [])
                    if isinstance(packages, list):
                        for package in packages:
                            if isinstance(package, str):
                                observations.append(_observation(
                                    "layered-package", package, "rpm-ostree status --json",
                                    request_class=key,
                                ))
        except (json.JSONDecodeError, ValueError) as error:
            failures.append({"probe": "rpm-ostree-status", "error": f"invalid-json: {error}"})

    extensions = runner(("gnome-extensions", "list", "--enabled"), 8.0)
    if extensions.error or extensions.returncode not in (0, None):
        failures.append({"probe": "gnome-extensions", "error": extensions.error or extensions.stderr.strip() or f"exit-{extensions.returncode}"})
    elif extensions.returncode == 0:
        for identifier in extensions.stdout.splitlines():
            identifier = identifier.strip()
            if identifier:
                observations.append(_observation("gnome-extension", identifier, "gnome-extensions list --enabled"))

    config_diff = runner(("ostree", "admin", "config-diff"), 12.0)
    if config_diff.error or config_diff.returncode not in (0, None):
        failures.append({"probe": "ostree-config-diff", "error": config_diff.error or config_diff.stderr.strip() or f"exit-{config_diff.returncode}"})
    elif config_diff.returncode == 0:
        for line in config_diff.stdout.splitlines():
            line = line.strip()
            if line:
                observations.append(_observation("etc-change", line, "ostree admin config-diff"))

    observations.sort(key=lambda item: (item["kind"], item["identity"]))
    return {
        "schema": INVENTORY_SCHEMA,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "observations": observations,
        "probe_failures": failures,
        "mutated_host": False,
    }
