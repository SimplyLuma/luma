"""Root-owned host identity used for privileged Mod resolution.

The graphical client may describe what it observed, but it never supplies the
hardware, kernel, base, or active-provider tuple that authorizes a system Mod.
Those values are reconstructed by the privileged service from an immutable
Luma profile plus kernel-owned runtime sources.
"""

from __future__ import annotations

import json
import os
import platform
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import TransactionError
from .resolver import HostContext

SYSTEM_HOST_SCHEMA = "org.luma.mod-system-host/v0.1"
SYSTEM_HOST_CONFIG = Path("/usr/share/luma/mods/trust/system-host.json")
MAX_HOST_CONFIG_BYTES = 512 * 1024
MAX_IDENTITY_BYTES = 512
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?$")


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TransactionError(f"system host profile contains duplicate field: {key}")
        result[key] = value
    return result


def _read_regular(path: Path, limit: int, *, require_root: bool) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise TransactionError(
            f"cannot open system host profile safely: {error.strerror}"
        ) from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > limit
            or require_root and info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise TransactionError(
                "system host profile must be a bounded, root-owned, non-writable regular file"
            )
        payload = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(payload) > limit:
        raise TransactionError("system host profile exceeds its size limit")
    return payload


def _runtime_identity(path: Path) -> str | None:
    try:
        payload = _read_regular(path, MAX_IDENTITY_BYTES, require_root=False)
    except TransactionError:
        return None
    try:
        value = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not value or len(value) > 128 or any(character in value for character in ":\x00\r\n"):
        return None
    return value


def dmi_hardware_identities(
    root: Path = Path("/sys/class/dmi/id"),
) -> tuple[str, ...]:
    """Return exact positive DMI identities, never wildcard guesses."""

    vendor = _runtime_identity(root / "sys_vendor")
    product = _runtime_identity(root / "product_name")
    if vendor is None or product is None:
        return ()
    return (f"dmi:{vendor}:{product}",)


@dataclass(frozen=True)
class SystemHostProfile:
    luma_base: str
    presentation: str
    capabilities: Mapping[str, str]
    capability_providers: Mapping[str, Mapping[str, str]]
    effect_owners: Mapping[str, Mapping[str, str]]

    @classmethod
    def from_file(
        cls, path: Path = SYSTEM_HOST_CONFIG, *, require_root: bool = True
    ) -> "SystemHostProfile":
        payload = _read_regular(path, MAX_HOST_CONFIG_BYTES, require_root=require_root)
        try:
            value = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_duplicate_keys,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    TransactionError(
                        f"system host profile contains unsupported constant: {item}"
                    )
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransactionError(f"system host profile is invalid UTF-8 JSON: {error}") from error
        expected = {
            "schema", "luma_base", "presentation", "capabilities",
            "capability_providers", "effect_owners",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise TransactionError("system host profile has an invalid shape")
        if value.get("schema") != SYSTEM_HOST_SCHEMA:
            raise TransactionError("system host profile has an unsupported schema")
        luma_base = value.get("luma_base")
        presentation = value.get("presentation")
        if not isinstance(luma_base, str) or not VERSION.fullmatch(luma_base):
            raise TransactionError("system host profile Luma base is invalid")
        if presentation not in {"desktop", "tablet", "handheld"}:
            raise TransactionError("system host profile presentation is invalid")
        # HostContext performs the authoritative nested capability/owner checks.
        try:
            probe = HostContext.from_dict({
                "luma_base": luma_base,
                "architecture": "x86_64",
                "presentation": presentation,
                "kernel_release": "",
                "install_mode": "running-system",
                "hardware": [],
                "capabilities": value["capabilities"],
                "capability_providers": value["capability_providers"],
                "installed_mods": {},
                "effect_owners": value["effect_owners"],
            })
        except (TypeError, AttributeError) as error:
            raise TransactionError("system host profile collections are invalid") from error
        return cls(
            luma_base=luma_base,
            presentation=presentation,
            capabilities=probe.capabilities,
            capability_providers={
                identifier: {
                    "provider": provider.provider,
                    "version": provider.version,
                    "ownership": provider.ownership,
                }
                for identifier, provider in probe.capability_providers.items()
            },
            effect_owners=probe.effect_owners,
        )

    def context(
        self,
        *,
        installed_mods: Mapping[str, str] | None = None,
        dmi_root: Path = Path("/sys/class/dmi/id"),
        architecture: str | None = None,
        kernel_release: str | None = None,
    ) -> HostContext:
        return HostContext.from_dict({
            "luma_base": self.luma_base,
            "architecture": architecture or platform.machine(),
            "presentation": self.presentation,
            "kernel_release": kernel_release or platform.release(),
            "install_mode": "running-system",
            "hardware": list(dmi_hardware_identities(dmi_root)),
            "capabilities": dict(self.capabilities),
            "capability_providers": {
                key: dict(value) for key, value in self.capability_providers.items()
            },
            "installed_mods": dict(installed_mods or {}),
            "effect_owners": {
                key: dict(value) for key, value in self.effect_owners.items()
            },
        })
