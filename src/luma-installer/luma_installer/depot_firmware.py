# SPDX-License-Identifier: Apache-2.0
"""Hardware (firmware) updates in plain words, and the Luma firmware block list.

Firmware comes from the Linux Vendor Firmware Service (LVFS), stable releases
only; the image turns LVFS's testing remote off. Device makers publish it
there and fwupd verifies it. Luma never installs firmware on its own.

**Plain words.** fwupd names devices the way their makers do ("UEFI dbx",
"Fibocom L850-GL"), and versions like "0.1.2.3" mean nothing to most people.
:func:`describe` turns a device's protocol, icon and name into a title and one
sentence a person can decide on; the maker's name, part and versions stay
behind Details.

**The block list.** Luma may block a firmware release for everyone (a release
that bricks a model, or one that breaks Luma). The list is a small JSON
document signed with the Depot catalogue key in minisign's format, like the
catalogue itself:

    {"schema": "org.projectluma.firmware-blocklist/v1",
     "generated_at": "2026-09-17T00:00:00Z",
     "entries": [{"guid": "...", "version": "1.2.3", "reason": "..."}]}

An entry without ``version`` blocks every release for that device GUID. An
entry may also name a ``plugin`` (fwupd's, such as ``modem_manager``) and
``fwupd`` builds (``VERSION-RELEASE``, exact) it applies to; an entry with a
``plugin`` and no ``guid`` covers every device that plugin updates. The
package ships a copy at ``/usr/share/luma/firmware/blocklist.json`` (+
``.minisig``) when one has been signed; Depot fetches
``https://dl.simplyluma.com/catalog/firmware-blocklist.json`` (+ ``.minisig``)
with its background run and keeps the newest verified copy. An unsigned,
altered or older list is ignored: a list that could hide updates must never
come from anyone but Luma. No list means nothing is blocked. Blocked releases
stay listed, paused, with the entry's reason, and are written to the journal
(MESSAGE_ID below) for Luma Vitals.
To block firmware: add an entry, bump ``generated_at``, sign with the
catalogue key where the catalogue is signed, and publish both files beside the
catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re

from . import depot_signature

SCHEMA = "org.projectluma.firmware-blocklist/v1"
MAX_BYTES = 256 * 1024
SHIPPED_PATH = Path(os.environ.get("LUMA_FIRMWARE_BLOCKLIST", "/usr/share/luma/firmware/blocklist.json"))
URL = os.environ.get("LUMA_FIRMWARE_BLOCKLIST_URL", "https://dl.simplyluma.com/catalog/firmware-blocklist.json")
#: journalctl MESSAGE_ID=... finds every firmware release Luma hid; Luma Vitals reads it.
BLOCKED_MESSAGE_ID = "8e2f5a31c7d94b0e9a6d3f18b25c4e70"
SOURCE_TEXT = ("Firmware comes from the Linux Vendor Firmware Service (LVFS), where device makers publish "
               "it; Luma uses its stable releases only. fwupd checks every file before it is installed, "
               "Luma hides releases it has found to cause problems, and nothing is ever installed "
               "without you.")


@dataclass(frozen=True)
class Description:
    title: str
    summary: str
    security: bool
    kind: str


def _has(values, *needles) -> bool:
    return any(needle in (value or "").lower() for value in values for needle in needles)


def describe(name: str, *, protocols=(), icons=(), plugin: str = "", urgency: str = "",
             security_release: bool = False) -> Description:
    """A title and one sentence for a firmware update, from what fwupd knows."""
    name = name or ""
    lower = name.lower()
    protocols = tuple(p.lower() for p in protocols if p)
    icons = tuple(i.lower() for i in icons if i)
    plugin = (plugin or "").lower()
    # LVFS marks most releases "high"; only a security release or a critical
    # one is shown as a security update.
    urgent = (urgency or "").lower() == "critical" or security_release

    if "org.uefi.dbx" in protocols or plugin == "uefi_dbx" or "dbx" in lower.split():
        return Description("Security update for your computer’s startup protection",
                           "Updates the list of startup software your computer refuses to run, so known "
                           "unsafe code cannot start before Luma does.", True, "dbx")
    if plugin in ("tpm", "tpm_eventlog") or _has(protocols, "tpm") or re.search(r"\btpm\b", lower):
        return Description("Security update for your computer’s security chip",
                           "Updates the chip that protects your encryption keys and checks how your "
                           "computer starts.", True, "tpm")
    if (_has(icons, "modem") or plugin in ("modem_manager", "mm") or _has(protocols, "qualcomm.firehose",
                                                                           "modemmanager", "fibocom", "sierra")
            or "modem" in lower or "wwan" in lower):
        return Description("Update for your mobile broadband modem",
                           "Improves how your computer connects to mobile networks.", urgent, "modem")
    if "org.uefi.capsule" in protocols or plugin == "uefi_capsule":
        if "system firmware" in lower or "bios" in lower or _has(icons, "computer"):
            return Description("Update for your computer’s firmware",
                               "Improves how your computer starts and works with its hardware.", urgent, "system")
        return Description("Update for part of your computer’s firmware",
                           "Improves a built-in part of your computer that starts before Luma.", urgent, "component")
    if _has(protocols, "thunderbolt", "usb4") or "thunderbolt" in lower or "usb4" in lower:
        return Description("Update for your computer’s Thunderbolt ports",
                           "Improves how docks, displays and fast storage connect.", urgent, "thunderbolt")
    if _has(icons, "dock") or "dock" in lower:
        return Description("Update for your dock", "Improves your dock and the devices connected to it.",
                           urgent, "dock")
    if _has(protocols, "nvmexpress", "ata") or _has(icons, "drive-harddisk") or "ssd" in lower.split():
        return Description("Update for your storage drive",
                           "Improves how reliably and quickly your drive stores your files.", urgent, "storage")
    if _has(icons, "touchpad") or "touchpad" in lower:
        return Description("Update for your touchpad", "Improves how your touchpad responds.", urgent, "touchpad")
    if _has(icons, "keyboard") or "keyboard" in lower:
        return Description("Update for your keyboard", "Improves how your keyboard works.", urgent, "keyboard")
    if _has(icons, "mouse") or "mouse" in lower or "receiver" in lower:
        return Description("Update for your mouse", "Improves how your mouse or its receiver works.",
                           urgent, "mouse")
    if _has(icons, "fingerprint") or "fingerprint" in lower:
        return Description("Update for your fingerprint reader",
                           "Improves how your fingerprint reader recognizes you.", urgent, "fingerprint")
    if _has(icons, "camera") or "camera" in lower or "webcam" in lower:
        return Description("Update for your camera", "Improves your camera.", urgent, "camera")
    if _has(icons, "audio", "headphones", "speaker") or "audio" in lower or "headset" in lower:
        return Description("Update for your audio device", "Improves your audio device.", urgent, "audio")
    if _has(icons, "network-wireless", "bluetooth") or "wi-fi" in lower or "bluetooth" in lower:
        return Description("Update for your wireless adapter", "Improves your wireless connections.",
                           urgent, "wireless")
    label = name.strip() or "a device"
    return Description(f"Update for {label}", "Improves this device.", urgent, "other")


def needs_text(requires_ac: bool, needs_reboot: bool) -> str:
    parts = []
    if requires_ac:
        parts.append("Needs power connected")
    if needs_reboot:
        parts.append("Finishes when you restart")
    return " · ".join(parts)


# ── Block list ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BlockEntry:
    guid: str = ""
    version: str = ""
    reason: str = ""
    plugin: str = ""
    fwupd: tuple[str, ...] = ()

    def matches(self, guids, version: str, plugin: str = "", fwupd_build: str = "") -> bool:
        if self.guid and self.guid not in guids:
            return False
        if self.version and self.version != version:
            return False
        if self.plugin and self.plugin != (plugin or "").lower():
            return False
        if self.fwupd and fwupd_build not in self.fwupd:
            return False
        return bool(self.guid or self.plugin)


@dataclass(frozen=True)
class BlockList:
    generated_at: str = ""
    entries: tuple[BlockEntry, ...] = ()

    def blocks(self, guids, version: str, plugin: str = "", fwupd_build: str = "") -> BlockEntry | None:
        wanted = {g.lower() for g in guids if g}
        return next((e for e in self.entries if e.matches(wanted, version, plugin, fwupd_build)), None)


EMPTY = BlockList()


class BlockListError(ValueError):
    pass


def parse(content: bytes, signature: bytes, key_data) -> BlockList:
    """Verify, then read. Nothing is parsed before the signature holds."""
    if len(content) > MAX_BYTES:
        raise BlockListError("the firmware block list is too large")
    try:
        depot_signature.verify_file(key_data, content, signature)
    except depot_signature.SignatureError as error:
        raise BlockListError(str(error)) from error
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise BlockListError("the firmware block list is not JSON") from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise BlockListError("the firmware block list has an unknown schema")
    generated = value.get("generated_at")
    try:
        datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
    except ValueError as error:
        raise BlockListError("the firmware block list has no valid generated_at") from error
    entries = []
    for item in value.get("entries", []) if isinstance(value.get("entries"), list) else []:
        if not isinstance(item, dict):
            continue
        guid = item.get("guid") or ""
        plugin = item.get("plugin") or ""
        if guid and (not isinstance(guid, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", guid)):
            continue
        if plugin and (not isinstance(plugin, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", plugin)):
            continue
        if not guid and not plugin:
            continue
        builds = item.get("fwupd") or ()
        builds = (builds,) if isinstance(builds, str) else builds
        if not isinstance(builds, (list, tuple)):
            continue
        entries.append(BlockEntry(guid.lower(), str(item.get("version") or "")[:64],
                                  str(item.get("reason") or "")[:300], plugin,
                                  tuple(str(b)[:64] for b in builds if isinstance(b, str))[:16]))
    return BlockList(str(generated), tuple(entries))


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def read_pair(path: Path, key_data) -> BlockList | None:
    try:
        content = path.read_bytes()[:MAX_BYTES + 1]
        signature = Path(str(path) + ".minisig").read_bytes()[:depot_signature.SIGNATURE_MAX_BYTES]
    except OSError:
        return None
    try:
        return parse(content, signature, key_data)
    except BlockListError:
        return None


def cache_path(environment=None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get("XDG_CACHE_HOME") or os.path.join(env.get("HOME", str(Path.home())), ".cache")
    return Path(base) / "luma/depot/firmware-blocklist.json"


def current(key_data, *, shipped: Path | None = None, cached: Path | None = None) -> BlockList:
    """The newest verified list among the shipped and the fetched copies, or EMPTY."""
    found = [bl for bl in (read_pair(shipped or SHIPPED_PATH, key_data), read_pair(cached or cache_path(), key_data))
             if bl is not None]
    return max(found, key=lambda bl: _moment(bl.generated_at)) if found else EMPTY


def remember(content: bytes, signature: bytes, key_data, *, cached: Path | None = None) -> BlockList:
    """Keep a fetched list if it verifies and is not older than the one kept."""
    fresh = parse(content, signature, key_data)
    path = cached or cache_path()
    kept = read_pair(path, key_data)
    if kept is not None and _moment(kept.generated_at) > _moment(fresh.generated_at):
        raise BlockListError("the published firmware block list is older than the one on this computer")
    path.parent.mkdir(parents=True, exist_ok=True)
    for target, data in ((Path(str(path) + ".minisig"), signature), (path, content)):
        temporary = target.with_name("." + target.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    return fresh
