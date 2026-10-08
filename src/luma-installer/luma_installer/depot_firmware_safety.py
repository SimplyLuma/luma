# SPDX-License-Identifier: Apache-2.0
"""Everything that keeps a hardware (firmware) update safe and understandable.

fwupd does the flashing; this module decides what Depot says around it. It has
no GI and no I/O beyond the attempt record, so every rule here is unit tested
with plain dictionaries standing in for fwupd.

**Before Install** (:func:`requirements`). What fwupd and the computer report
about a device becomes a short list of lines a person can act on: connect the
charger, charge to 30 %, open the lid, free room on the startup partition,
refresh old update information. A *blocking* line keeps Install unavailable;
the others are said once, before anything starts (a modem drops its
connection while it updates).

**When it fails** (:func:`explain`). fwupd's error code, its message and how far
the install got become a :class:`Failure`: one title, one sentence, whether
anything was written to the device, and whether this is urgent. A failure
before writing always says "Nothing was changed on your device"; one during
writing says so plainly and tells the person how to recover. Depot never shows
fwupd's raw text outside Details, and never "unspecified error".

**Backing off** (:class:`Attempts`, :func:`hold`). Every attempt is recorded
per device, firmware version, plugin and fwupd build. A second failure before
writing holds the offer for a day; a third pauses it until fwupd or the
firmware changes. Failures while writing never hold anything: the way out of a
half-written device is to try again. :data:`KNOWN_ISSUES` pauses combinations
Luma knows cannot work (a fwupd bug with no fix installed yet) before anyone
tries; the signed block list (depot_firmware) does the same from the network.

**Journal** (:func:`journal`). One structured entry per attempt, failure, hold
and blocked preflight under :data:`MESSAGE_ID`, for Luma Vitals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

#: journalctl MESSAGE_ID=... finds every firmware attempt, failure, hold and
#: unmet requirement Depot saw; Luma Vitals reads it.
MESSAGE_ID = "5f0c3e8a92d44b6c8e17a4d2b9f06c31"

#: fwupd's own error codes (FwupdError in libfwupd/fwupd-error.h), so nothing
#: here needs libfwupd. The D-Bus names end in the same words.
FWUPD_ERRORS = {
    0: "Internal", 1: "VersionNewer", 2: "VersionSame", 3: "AlreadyPending", 4: "AuthFailed",
    5: "Read", 6: "Write", 7: "InvalidFile", 8: "NotFound", 9: "NothingToDo", 10: "NotSupported",
    11: "SignatureInvalid", 12: "AcPowerRequired", 13: "PermissionDenied", 14: "BrokenSystem",
    15: "BatteryLevelTooLow", 16: "NeedsUserAction", 17: "AuthExpired", 18: "InvalidData",
    19: "TimedOut", 20: "Busy", 21: "NotReachable",
}

#: How far an install got. ``prepare``: downloading, checking and reading the
#: file. ``detach``: asking the device to switch into its update mode.
#: ``write``: writing to the device. ``attach``: the device restarting with
#: what was written. ``unknown``: Depot lost track (the helper ended).
PHASES = ("prepare", "detach", "write", "attach", "unknown")
BEFORE_WRITING = ("prepare", "detach")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(text) -> str:
    """fwupd passes on what devices report, control characters included (a
    Quectel modem's model ends in a carriage return). Strip them for display
    and for keys."""
    return _CONTROL.sub("", str(text or "")).replace("\r", "").strip()


def loads_lenient(text):
    """JSON from fwupdmgr or journalctl, which can carry raw control characters
    inside strings (``fwupdmgr get-updates --json`` writes the modem's
    ``\\r`` unescaped in InstanceIds). Parse it instead of failing."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(text, strict=False)


def error_name(code) -> str:
    """``AcPowerRequired`` from 12, ``org.freedesktop.fwupd.AcPowerRequired`` or
    ``AcPowerRequired``; ``""`` when unknown."""
    if isinstance(code, int) and not isinstance(code, bool):
        return FWUPD_ERRORS.get(code, "")
    text = str(code or "")
    tail = text.rsplit(".", 1)[-1]
    return tail if tail in FWUPD_ERRORS.values() else ""


def phase_from_message(message: str, default: str = "prepare") -> str:
    """fu-engine prefixes where an install stopped; the outermost prefix wins."""
    lower = (message or "").lower()
    for prefix, phase in (("failed to write-firmware", "write"), ("failed to write firmware", "write"),
                          ("failed to attach", "attach"), ("failed to reload", "attach"),
                          ("failed to detach", "detach")):
        if lower.startswith(prefix):
            return phase
    for prefix, phase in (("failed to write-firmware", "write"), ("failed to attach", "attach"),
                          ("failed to detach", "detach")):
        if prefix in lower:
            return phase
    return default


# ── Before Install ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Requirement:
    key: str
    text: str
    blocking: bool = True
    #: An action Depot can take for the person: "refresh" (update information).
    action: str = ""
    action_label: str = ""


#: fwupd device problems (FwupdDeviceProblem, as fwupd_device_problem_to_string
#: names them) in plain words. ``{device}`` is the plain device name.
_PROBLEMS = {
    "system-power-too-low": ("battery", "Charge this computer before updating.", True),
    "unreachable": ("unreachable", "Turn on {device} and keep it close to this computer.", True),
    "power-too-low": ("device-battery", "Charge {device} before updating.", True),
    "update-pending": ("pending", "An update for {device} is waiting for a restart. Restart this computer "
                                  "to finish it first.", True),
    "require-ac-power": ("ac", "Connect your charger. This update needs power the whole time.", True),
    "lid-is-closed": ("lid", "Open this computer’s lid. Firmware can’t update with it closed.", True),
    "is-emulated": ("emulated", "This device is a test device and can’t be updated.", True),
    "missing-license": ("license", "The maker hasn’t published a license for this update, so it can’t "
                                   "be installed.", True),
    "system-inhibit": ("inhibit", "Something on this computer is holding off firmware updates. Try again "
                                  "when it finishes.", True),
    "update-in-progress": ("in-progress", "An update for {device} is already running.", True),
    "in-use": ("in-use", "Stop using {device}, then install.", True),
    "display-required": ("display", "Connect a display to update {device}.", True),
    "lower-priority": ("lower-priority", "Another update for this device should go first.", True),
    "insecure-platform": ("insecure", "This update needs this computer’s security settings turned back "
                                      "on first.", True),
    "firmware-locked": ("locked", "The firmware of {device} is locked. Unlock it in your computer’s "
                                  "firmware settings to update it.", True),
}

#: Update information older than this is refreshed before offering an install.
STALE_METADATA_DAYS = 30
#: fwupd's default when it reports no threshold for the system battery.
DEFAULT_BATTERY_THRESHOLD = 25
#: Room a UEFI capsule needs on the EFI system partition on top of its own size:
#: the fwupd EFI program, its log and headroom for the firmware's own copy.
ESP_HEADROOM = 32 * 1024 * 1024


def _mb(value: int) -> str:
    return f"{max(1, round(value / (1024 * 1024)))} MB"


def requirements(update, facts: dict | None, *, device_name: str = "this device",
                 kind: str = "other") -> list[Requirement]:
    """What must be true before ``update`` installs, from the probe's ``facts``.

    ``update`` needs ``requires_ac``, ``needs_reboot``, ``plugin``; ``facts`` is
    the probe's per-update dictionary merged over its computer-wide one."""
    facts = facts or {}
    found: dict[str, Requirement] = {}

    def add(requirement: Requirement) -> None:
        found.setdefault(requirement.key, requirement)

    if facts.get("daemon_ok") is False:
        add(Requirement("daemon", "The firmware service isn’t responding. Try again after restarting "
                                  "this computer."))
    requires_ac = bool(getattr(update, "requires_ac", False))
    on_battery = bool(facts.get("on_battery"))
    if requires_ac and on_battery:
        add(Requirement("ac", "Connect your charger. This update needs power the whole time."))
    level = facts.get("battery_level")
    threshold = facts.get("battery_threshold") or DEFAULT_BATTERY_THRESHOLD
    if ("ac" not in found and on_battery and isinstance(level, (int, float)) and 0 <= level <= 100
            and isinstance(threshold, (int, float)) and level < threshold):
        add(Requirement("battery", f"Charge this computer to at least {int(threshold)}% first "
                                   f"(it’s at {int(level)}%), or connect your charger."))
    for problem in facts.get("problems") or ():
        known = _PROBLEMS.get(str(problem))
        if known is None:
            continue
        key, text, blocking = known
        add(Requirement(key, text.replace("{device}", device_name), blocking))
    device_level = facts.get("device_battery_level")
    device_threshold = facts.get("device_battery_threshold")
    if (isinstance(device_level, (int, float)) and isinstance(device_threshold, (int, float))
            and 0 <= device_level <= 100 and device_level < device_threshold):
        add(Requirement("device-battery", f"Charge {device_name} to at least {int(device_threshold)}% "
                                          f"first (it’s at {int(device_level)}%)."))
    capsule = (getattr(update, "plugin", "") == "uefi_capsule"
               or "org.uefi.capsule" in tuple(getattr(update, "protocols", ()) or ()))
    if capsule:
        if facts.get("uefi") is False:
            add(Requirement("uefi", "This computer didn’t start in UEFI mode, so its firmware can’t be "
                                    "updated from Luma."))
        free, needed = facts.get("esp_free"), facts.get("esp_needed")
        if isinstance(free, int) and isinstance(needed, int) and free < needed:
            add(Requirement("esp", f"Free {_mb(needed - free)} on the startup (EFI) partition. It has "
                                   f"{_mb(free)} free and this update needs {_mb(needed)}."))
        if facts.get("secure_boot") and facts.get("signed_loader") is False:
            add(Requirement("secure-boot", "Secure Boot is on, and the signed firmware updater isn’t "
                                           "installed. Update Luma, then try again."))
    if kind == "modem" and facts.get("modem_connected"):
        add(Requirement("modem-connected", "Mobile data disconnects while the modem updates, for a few "
                                           "minutes. It reconnects on its own.", blocking=False))
    age = facts.get("metadata_age_days")
    if isinstance(age, (int, float)) and age > STALE_METADATA_DAYS:
        add(Requirement("metadata", f"Hardware update information is {int(age)} days old. Refresh it "
                                    "before installing.", blocking=True,
                        action="refresh", action_label="Refresh"))
    return list(found.values())


#: Requirements about the computer or fwupd rather than one device: said once
#: for the whole hardware card.
GLOBAL = ("daemon", "metadata")


def blocking(requirements_list) -> bool:
    return any(requirement.blocking for requirement in requirements_list)


# ── When it fails ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Failure:
    title: str
    body: str
    #: before (nothing written), during (stopped while writing), after
    #: (written, the device did not come back), unknown (Depot lost track).
    stage: str
    kind: str
    retry: bool = True
    urgent: bool = False
    #: "restart" when restarting the computer is the way forward.
    action: str = ""
    detail: str = ""
    code: str = ""
    phase: str = ""

    @property
    def changed_nothing(self) -> bool:
        return self.stage == "before"


NOTHING_CHANGED = "Nothing was changed on your device."
#: The whole of what Depot says when fwupd gives no reason: the title, then the body.
COULD_NOT_START = "The update couldn’t start. " + NOTHING_CHANGED


def _stage(phase: str, device_after: str) -> str:
    if device_after in ("missing", "bootloader") and phase not in ("prepare",):
        return "during"
    if phase in BEFORE_WRITING:
        return "before"
    if phase == "write":
        return "during"
    if phase == "attach":
        return "after"
    return "unknown"


def explain(code=None, message: str = "", *, phase: str = "", device_after: str = "present",
            device_name: str = "this device", removable: bool = False, log: str = "") -> Failure:
    """A :class:`Failure` for what fwupd said. ``code`` is fwupd's error code (int
    or name), ``message`` its text, ``phase`` how far the install got and
    ``device_after`` whether the device is still there afterwards (present,
    missing or bootloader)."""
    name = error_name(code)
    message = clean(message)
    phase = phase if phase in PHASES else phase_from_message(message)
    stage = _stage(phase, device_after)
    lower = message.lower()
    detail = "\n".join(part for part in (message, log.strip()) if part)
    common = dict(detail=detail, code=name, phase=phase)

    if stage == "during":
        if device_after in ("missing", "bootloader"):
            body = (f"{device_name[:1].upper() + device_name[1:]} is waiting in its update mode. Keep this "
                    "computer on power and try again now; the update can finish from here. Don’t restart "
                    "or unplug anything until it does.")
        else:
            body = ("Keep this computer on power and don’t restart it or unplug the device. Try again now; "
                    "a second try usually finishes. If it stops again, leave the computer on and get help "
                    "with the Details below.")
        return Failure("The update stopped while it was being written", body, "during", "interrupted",
                       urgent=True, **common)
    if stage == "after":
        return Failure("Restart to finish the update",
                       "The update was written, but the device didn’t restart on its own. Restart this "
                       "computer to finish.", "after", "attach", action="restart", **common)
    if stage == "unknown":
        return Failure("Depot lost track of the update",
                       "Keep this computer on power. Depot is checking the device again; if the update is "
                       "still offered, try again.", "unknown", "lost", **common)

    # Nothing was written.
    def before(title: str, sentence: str, kind: str, *, retry: bool = True, action: str = "") -> Failure:
        return Failure(title, f"{sentence} {NOTHING_CHANGED}".strip(), "before", kind, retry=retry,
                       action=action, **common)

    if name == "AcPowerRequired" or "ac power" in lower or "requires ac" in lower:
        return before("Connect your charger", "This update needs power the whole time. Connect your "
                                              "charger, then try again.", "ac")
    if name == "BatteryLevelTooLow" or "battery" in lower:
        return before("Charge first", "The battery is too low for this update. Charge, then try again.",
                      "battery")
    if "lid" in lower and "closed" in lower:
        return before("Open the lid", "Firmware can’t update with the lid closed. Open it, then try again.",
                      "lid")
    if name in ("AuthFailed", "AuthExpired", "PermissionDenied") or "not authorized" in lower \
            or "polkit" in lower:
        return before("Permission needed", "Luma didn’t get permission to update this device. Try again "
                                           "and approve the request.", "authorization")
    if name in ("SignatureInvalid", "InvalidFile", "InvalidData") or "checksum" in lower \
            or "signature" in lower:
        return before("The update couldn’t be verified", "The downloaded update didn’t pass its checks, so "
                                                         "it wasn’t installed. Try again later.",
                      "verification")
    if name == "NotReachable" or any(words in lower for words in ("download", "could not resolve",
                                                                 "network", "connection refused",
                                                                 "failed to connect")):
        return before("The update couldn’t be downloaded", "Check your connection and try again.",
                      "network")
    if "esp" in lower and ("space" in lower or "size" in lower) or "no space left" in lower:
        return before("Not enough room on the startup partition",
                      "Free some space on the EFI system partition, then try again.", "esp")
    if "secure boot" in lower or "secureboot" in lower or "shim" in lower:
        return before("Secure Boot stopped the update", "The firmware updater couldn’t be started with "
                                                        "Secure Boot on. Update Luma, then try again.",
                      "secure-boot", retry=False)
    if name in ("AlreadyPending",):
        return before("Restart to finish the waiting update", "An update for this device is already "
                                                               "waiting for a restart.", "pending",
                      retry=False, action="restart")
    if name == "Busy" or "busy" in lower or "in use" in lower:
        return before("The device is busy", "Something is using it right now. Try again in a minute.",
                      "busy")
    if name in ("VersionNewer", "VersionSame", "NothingToDo"):
        return before("Already up to date", "This device already has this version or a newer one.",
                      "current", retry=False)
    if name == "NotFound" and ("device" in lower or not lower):
        return before("The device isn’t connected", "Reconnect it and try again." if removable else
                      "Restart this computer, then try again.", "gone")
    if name == "NotSupported":
        return before("This update can’t be installed here", "fwupd says this device can’t take this "
                                                             "update on this computer.", "unsupported",
                      retry=False)
    if name == "NeedsUserAction" and message:
        return before("The device needs you first", message.rstrip(".") + ".", "user-action")
    if name == "BrokenSystem":
        return before("This computer can’t update firmware right now", "Something on this computer stops "
                                                                        "firmware updates. See Details.",
                      "broken-system", retry=False)
    if name in ("Read", "Write", "TimedOut") or "timed out" in lower or "timeout" in lower \
            or "not responding" in lower or "port not found" in lower:
        if removable:
            return before("The device isn’t responding", "Unplug it, plug it back in and try again.",
                          "unresponsive")
        return before("The device isn’t responding", "Try again after restarting this computer.",
                      "unresponsive", action="restart")
    return Failure("The update couldn’t start", NOTHING_CHANGED, "before", "unspecified", **common)


# ── Backing off ───────────────────────────────────────────────────────────

#: Consecutive failures before writing that hold an offer for a day, and that
#: pause it until fwupd or the firmware changes.
HOLD_AFTER = 2
PAUSE_AFTER = 3
HOLD_FOR = timedelta(hours=24)
MAX_ATTEMPTS_KEPT = 12


def attempt_key(guid: str, plugin: str, current_version: str, version: str, fwupd_build: str) -> str:
    """One combination of device, installed and offered firmware, plugin and fwupd
    build. A new fwupd or a new firmware release is a new key, so its offer
    comes back on its own."""
    text = "\x1f".join(clean(part).lower() for part in (guid, plugin, current_version, version, fwupd_build))
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def state_path(environment=None) -> Path:
    env = os.environ if environment is None else environment
    if env.get("LUMA_DEPOT_FIRMWARE_ATTEMPTS"):
        return Path(env["LUMA_DEPOT_FIRMWARE_ATTEMPTS"])
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME", str(Path.home())), ".local/state")
    return Path(base) / "luma/depot/firmware-attempts.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _moment(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Attempts:
    """The local record of firmware attempts, kept in the person's state folder."""

    path: Path
    entries: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Attempts":
        path = path or state_path()
        try:
            value = loads_lenient(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        entries = value.get("attempts") if isinstance(value, dict) else None
        return cls(path, entries if isinstance(entries, dict) else {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name("." + self.path.name + ".tmp")
            temporary.write_text(json.dumps({"schema": "org.projectluma.firmware-attempts/v1",
                                             "attempts": self.entries}, indent=1, sort_keys=True),
                                 encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            pass

    def history(self, key: str) -> list[dict]:
        entry = self.entries.get(key)
        return list(entry.get("results") or ()) if isinstance(entry, dict) else []

    def record(self, key: str, outcome: str, *, stage: str = "", code: str = "", kind: str = "",
               when: datetime | None = None, about: dict | None = None) -> None:
        """``outcome``: succeeded or failed."""
        entry = self.entries.setdefault(key, {})
        if about:
            entry["about"] = {name: clean(value) for name, value in about.items()}
        results = [r for r in entry.get("results") or () if isinstance(r, dict)]
        results.append({"at": _stamp(when or _now()), "outcome": outcome, "stage": stage, "code": code,
                        "kind": kind})
        entry["results"] = results[-MAX_ATTEMPTS_KEPT:]
        self.save()


@dataclass(frozen=True)
class Hold:
    #: held (until a time) or paused (until fwupd or the firmware changes).
    state: str
    reason: str
    until: datetime | None = None
    failures: int = 0


def _consecutive_before_failures(results) -> int:
    count = 0
    for result in reversed(results):
        if result.get("outcome") != "failed" or result.get("stage") != "before":
            break
        count += 1
    return count


def hold(attempts: Attempts, key: str, *, now: datetime | None = None) -> Hold | None:
    """Whether this combination's offer waits, after failures that changed nothing."""
    results = attempts.history(key)
    failures = _consecutive_before_failures(results)
    if failures == 0:
        return None
    last = results[-1]
    if last.get("kind") in ("unsupported", "broken-system", "secure-boot") or failures >= PAUSE_AFTER:
        return Hold("paused", f"This update failed {failures} time{'s' if failures != 1 else ''} on this "
                              "computer before anything was changed, so Luma paused it. It comes back when a "
                              "fixed firmware service or a new release of this update arrives.",
                    None, failures)
    if failures >= HOLD_AFTER:
        started = _moment(last.get("at", ""))
        until = (started or (now or _now())) + HOLD_FOR
        if (now or _now()) < until:
            return Hold("held", "This update failed twice before anything was changed. Luma will offer it "
                                "again tomorrow.", until, failures)
    return None


# ── Combinations Luma knows cannot work ───────────────────────────────────

@dataclass(frozen=True)
class KnownIssue:
    reason: str
    plugin: str = ""
    protocol: str = ""
    vendor_id_prefix: str = ""
    #: fwupd builds (``VERSION-RELEASE``) the issue affects, as a regular expression.
    fwupd_build: str = ""
    link: str = ""

    def matches(self, update, fwupd_build: str) -> bool:
        if self.plugin and getattr(update, "plugin", "") != self.plugin:
            return False
        if self.protocol and self.protocol not in tuple(getattr(update, "protocols", ()) or ()):
            return False
        if self.vendor_id_prefix and not any(str(v).upper().startswith(self.vendor_id_prefix)
                                             for v in getattr(update, "vendor_ids", ()) or ()):
            return False
        if self.fwupd_build and not re.fullmatch(self.fwupd_build, fwupd_build or ""):
            return False
        return True


KNOWN_ISSUES = (
    # fwupd 2.1.7 detaches PCIe (MHI) firehose modems before loading the
    # firehose programmer they need, so the update stops at once with
    # "unspecified error". Luma's fwupd 2.1.7-1.luma.1 carries the fix.
    KnownIssue("A known problem in this version of the firmware service stops this update before it "
               "starts. It comes back once Luma’s fix for the firmware service is installed.",
               plugin="modem_manager", protocol="com.qualcomm.firehose", vendor_id_prefix="PCI:",
               fwupd_build=r"2\.1\.7-(?!.*luma).*",
               link="https://github.com/fwupd/fwupd/issues/10939"),
)


def known_issue(update, fwupd_build: str, issues=KNOWN_ISSUES) -> KnownIssue | None:
    return next((issue for issue in issues if issue.matches(update, fwupd_build)), None)


# ── Journal ───────────────────────────────────────────────────────────────

def journal(event: str, update=None, *, fwupd_build: str = "", failure: Failure | None = None,
            detail: str = "", priority: str | None = None, **extra) -> dict:
    """One structured entry for Luma Vitals. ``event``: started, succeeded,
    failed, held, paused, blocked (by Luma), unmet (a requirement)."""
    device = clean(getattr(update, "device", "")) if update is not None else ""
    guids = tuple(getattr(update, "guids", ()) or ()) if update is not None else ()
    line = f"Depot: firmware {event}"
    if update is not None:
        line += f" for {device or 'a device'} {clean(getattr(update, 'current_version', ''))} → " \
                f"{clean(getattr(update, 'version', ''))}"
    if failure is not None:
        line += f" ({failure.stage}, {failure.kind}): {failure.detail.splitlines()[0] if failure.detail else ''}"
    elif detail:
        line += f": {detail}"
    fields = {
        "MESSAGE_ID": MESSAGE_ID,
        "PRIORITY": priority or {"failed": "3", "paused": "4", "held": "4", "blocked": "5", "unmet": "5"}
        .get(event, "6"),
        "SYSLOG_IDENTIFIER": "luma-depot",
        "LUMA_FIRMWARE_EVENT": event,
        "LUMA_FIRMWARE_DEVICE": device,
        "LUMA_FIRMWARE_DEVICE_ID": clean(getattr(update, "device_id", "")) if update is not None else "",
        "LUMA_FIRMWARE_GUID": guids[0] if guids else "",
        "LUMA_FIRMWARE_GUIDS": ",".join(guids),
        "LUMA_FIRMWARE_PLUGIN": clean(getattr(update, "plugin", "")) if update is not None else "",
        "LUMA_FIRMWARE_PROTOCOLS": ",".join(getattr(update, "protocols", ()) or ()) if update is not None else "",
        "LUMA_FIRMWARE_CURRENT_VERSION": clean(getattr(update, "current_version", "")) if update is not None else "",
        "LUMA_FIRMWARE_VERSION": clean(getattr(update, "version", "")) if update is not None else "",
        "LUMA_FIRMWARE_FWUPD": fwupd_build,
    }
    if failure is not None:
        fields.update(LUMA_FIRMWARE_STAGE=failure.stage, LUMA_FIRMWARE_PHASE=failure.phase,
                      LUMA_FIRMWARE_FAILURE=failure.kind, LUMA_FIRMWARE_ERROR_CODE=failure.code,
                      LUMA_FIRMWARE_ERROR=failure.detail[:4000])
    if detail:
        fields["LUMA_FIRMWARE_DETAIL"] = detail[:4000]
    fields.update({f"LUMA_FIRMWARE_{key.upper()}": str(value) for key, value in extra.items()})
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, **fields)
    except Exception:  # noqa: BLE001 - python3-systemd missing or no journal socket
        print(line, file=sys.stderr)
    return fields
