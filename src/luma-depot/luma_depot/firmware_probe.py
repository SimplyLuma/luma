# SPDX-License-Identifier: Apache-2.0
"""Talks to fwupd for Depot, in a process of its own.

Depot runs this as ``python3 -m luma_depot.firmware_probe list`` or
``... install DEVICE_ID`` and reads JSON lines from its standard output. Nothing
from libfwupd is ever loaded into the Depot window's process: a fault in
libfwupd, or in fwupd while it starts, stops or restarts, ends this process and
Depot shows the firmware section's own error state.

libfwupd's FwupdClient is not thread-safe and delivers its notifications on the
main context it was created for. Here it lives on this process's main thread,
one client for the whole run, and the process ends with ``os._exit`` so the
client is never finalized while one of its notifications is still queued
(libfwupd 2.1 aborts in ``g_mutex_clear`` when that happens).

Output, one JSON object per line (``json.dumps``, so whatever control
characters a device reports arrive escaped):
  ``{"progress": 0.42}``, ``{"phase": "write"}``    while installing
  ``{"ok": true, "updates": [...], "on_battery": false, "host": {...}}``  after ``list``;
      each update carries ``facts`` for the checks before Install
  ``{"ok": true, "needs_reboot": false}``           after ``install``
  ``{"ok": true, "refreshed": 1}``                  after ``refresh``
  ``{"ok": false, "code": "...", "hint": "...", "fwupd_code": 0, "phase": "detach",
     "device_after": "present", "message": "...", "log": "..."}``  when something went wrong

Depot talks to fwupd only through libfwupd (D-Bus), never by parsing
``fwupdmgr --json``: that output is not escaped (a modem's model can carry a
raw carriage return) and its shape changes between releases.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import os
import sys

#: ``code`` values. ``missing``: fwupd or its GObject bindings are not installed.
#: ``unavailable``: fwupd could not be reached or answered with an error.
MISSING = "missing"
UNAVAILABLE = "unavailable"
BATTERY = "battery"
GONE = "gone"
FAILED = "failed"


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _client():
    import gi
    gi.require_version("Fwupd", "2.0")
    from gi.repository import Fwupd, GLib
    client = Fwupd.Client.new()
    client.set_main_context(GLib.MainContext.default())
    return Fwupd, GLib, client


def _identify(GLib, client) -> None:
    """fwupd wants a user agent naming its own version before it downloads; that
    version is known only once the client has talked to the daemon."""
    try:
        client.set_user_agent_for_package("luma-depot", "4")
    except (GLib.Error, TypeError):
        pass


def list_updates() -> dict:
    try:
        Fwupd, GLib, client = _client()
    except (ImportError, ValueError) as error:
        return {"ok": False, "code": MISSING, "hint": str(error)}
    from luma_installer.depot_catalog import public_key
    from luma_installer.depot_firmware import EMPTY, current
    from luma_installer.depot_firmware_safety import clean
    from .system_updates import (FirmwareUpdate, _journal_blocked, _security_release, _strings,
                                 on_battery, plain)
    _KEEP.append(client)
    try:
        blocklist = current(public_key())
    except Exception:  # noqa: BLE001 - an unreadable key or list blocks nothing
        blocklist = EMPTY
    try:
        devices = _devices(Fwupd, GLib, client)
    except GLib.Error as error:
        return {"ok": False, "code": UNAVAILABLE, "hint": error.message}
    host = host_facts(Fwupd, GLib, client)
    found = []
    for device in devices:
        if not device.has_flag(Fwupd.DeviceFlags.UPDATABLE):
            continue
        try:
            releases = client.get_upgrades(device.get_id(), None)
        except GLib.Error:
            continue
        if not releases:
            continue
        release = releases[0]
        guids = tuple(clean(g).lower() for g in (device.get_guids() or ()))
        plugin = clean(device.get_plugin() or "") if hasattr(device, "get_plugin") else ""
        blocked = blocklist.blocks(guids, release.get_version() or "", plugin, host.get("fwupd_build", ""))
        if blocked is not None:
            _journal_blocked(device, release, blocked)
        protocols = tuple(_strings(device, "get_protocols")) or tuple(_strings(release, "get_protocols"))
        values = asdict(FirmwareUpdate(
            device.get_id(), clean(device.get_name()) or "Device", clean(device.get_vendor()),
            clean(device.get_version()), clean(release.get_version()),
            clean(release.get_summary()), plain(release.get_description() or ""),
            _urgency(Fwupd, release),
            device.has_flag(Fwupd.DeviceFlags.NEEDS_REBOOT),
            device.has_flag(Fwupd.DeviceFlags.REQUIRE_AC),
            release.get_details_url() or "",
            protocols=protocols, icons=tuple(_strings(device, "get_icons")),
            plugin=plugin, guids=guids, security_release=_security_release(Fwupd, release),
            vendor_ids=tuple(clean(v) for v in _strings(device, "get_vendor_ids")),
            removable=_removable(Fwupd, device, _strings(device, "get_vendor_ids")),
            size=_int(release, "get_size"),
            blocked_reason=(blocked.reason or "Luma paused this release after reports of problems with it.")
            if blocked is not None else ""))
        values["facts"] = device_facts(Fwupd, GLib, device, release, host)
        found.append(values)
    return {"ok": True, "updates": found, "on_battery": host.get("on_battery", on_battery()), "host": host}


def _starting(error) -> bool:
    """fwupd exits when idle and can take 25 s or more to start again (it probes
    every device first), longer than a D-Bus activation waits."""
    text = (getattr(error, "message", "") or "").lower()
    return "timeout was reached" in text or "startservicebyname" in text or "timed out" in text


def _devices(Fwupd, GLib, client):
    """Every device, waiting once more for a daemon that is still starting."""
    import time
    for attempt in range(3):
        try:
            return client.get_devices(None)
        except GLib.Error as error:
            if _nothing_to_do(Fwupd, error):
                return []
            if attempt == 2 or not _starting(error):
                raise
            time.sleep(2)
    return []


def _urgency(Fwupd, release) -> str:
    try:
        value = release.get_urgency()
    except (AttributeError, TypeError):
        return ""
    if hasattr(Fwupd, "release_urgency_to_string"):
        try:
            return Fwupd.release_urgency_to_string(value) or ""
        except TypeError:
            pass
    nick = getattr(value, "value_nick", "")
    if nick:
        return nick
    return {1: "low", 2: "medium", 3: "high", 4: "critical"}.get(int(value), "") if isinstance(value, int) else ""


def _removable(Fwupd, device, vendor_ids) -> bool:
    """Whether a person can unplug it: not built in, by fwupd's flag or its bus."""
    if device.has_flag(Fwupd.DeviceFlags.INTERNAL):
        return False
    return not any(str(v).upper().startswith(("PCI:", "DMI:", "ACPI:", "NVME:", "CPUID:")) for v in vendor_ids)


def _int(item, getter):
    try:
        value = getattr(item, getter)()
    except (AttributeError, TypeError):
        return 0
    return int(value) if isinstance(value, int) and 0 <= value < 2 ** 62 else 0


# ── What Depot checks before offering Install ────────────────────────────

def fwupd_build(daemon_version: str = "") -> str:
    """fwupd's package build (``2.1.7-1.fc44``): a fix packaged by Luma keeps the
    daemon version, so failures are remembered per build, not per version."""
    import subprocess
    try:
        result = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", "fwupd"], capture_output=True,
                                text=True, timeout=5, check=False)
        text = result.stdout.strip()
        if result.returncode == 0 and text and " " not in text:
            return text
    except (OSError, subprocess.SubprocessError):
        pass
    return daemon_version


def _upower(path: str, interface: str, name: str):
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync("org.freedesktop.UPower", path, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (interface, name)), GLib.VariantType("(v)"),
                              Gio.DBusCallFlags.NONE, 2000, None)
        return reply.unpack()[0]
    except GLib.Error:
        return None


def modem_connected() -> bool:
    """Whether ModemManager has a modem with an active (or starting) data connection."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync("org.freedesktop.ModemManager1", "/org/freedesktop/ModemManager1",
                              "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                              GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
    except GLib.Error:
        return False
    for interfaces in reply.unpack()[0].values():
        state = interfaces.get("org.freedesktop.ModemManager1.Modem", {}).get("State")
        if isinstance(state, int) and state >= 10:  # MM_MODEM_STATE_CONNECTING, CONNECTED
            return True
    return False


def _esp_free() -> tuple[str, int | None]:
    try:
        mounts = open("/proc/self/mounts", encoding="utf-8").read().splitlines()
    except OSError:
        return "", None
    points = {parts[1]: parts[2] for parts in (line.split() for line in mounts) if len(parts) > 2}
    for candidate in ("/boot/efi", "/efi", "/boot"):
        if points.get(candidate) == "vfat":
            try:
                stat = os.statvfs(candidate)
            except OSError:
                return candidate, None
            return candidate, stat.f_bavail * stat.f_frsize
    return "", None


def _secure_boot() -> bool | None:
    import glob
    for path in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
        try:
            data = open(path, "rb").read()
        except OSError:
            return None
        return len(data) >= 5 and data[4] == 1
    return None


def host_facts(Fwupd, GLib, client) -> dict:
    import glob
    facts: dict = {}
    try:
        version = client.get_daemon_version() or ""
    except (GLib.Error, AttributeError, TypeError):
        version = ""
    facts["daemon_version"] = version
    facts["daemon_ok"] = bool(version)
    facts["fwupd_build"] = fwupd_build(version)
    battery = _upower("/org/freedesktop/UPower", "org.freedesktop.UPower", "OnBattery")
    facts["on_battery"] = bool(battery)
    level = _upower("/org/freedesktop/UPower/devices/DisplayDevice", "org.freedesktop.UPower.Device",
                    "Percentage")
    kind = _upower("/org/freedesktop/UPower/devices/DisplayDevice", "org.freedesktop.UPower.Device", "Type")
    if isinstance(level, (int, float)) and kind == 2:  # UP_DEVICE_KIND_BATTERY
        facts["battery_level"] = int(level)
    for getter, name in (("get_battery_threshold", "battery_threshold"),):
        try:
            value = getattr(client, getter)()
        except (GLib.Error, AttributeError, TypeError):
            continue
        if isinstance(value, int) and 0 < value <= 100:
            facts[name] = value
    ages = []
    try:
        for remote in client.get_remotes(None):
            if not _remote_downloads(Fwupd, remote):
                continue
            age = remote.get_age()
            if isinstance(age, int) and 0 <= age < 10 ** 10:
                ages.append(age)
    except (GLib.Error, AttributeError, TypeError):
        pass
    if ages:
        facts["metadata_age_days"] = min(ages) // 86400
    facts["uefi"] = os.path.isdir("/sys/firmware/efi")
    facts["secure_boot"] = _secure_boot()
    facts["signed_loader"] = bool(glob.glob("/usr/libexec/fwupd/efi/*.efi.signed"))
    facts["esp_path"], facts["esp_free"] = _esp_free()
    return facts


def _remote_downloads(Fwupd, remote) -> bool:
    """An enabled remote that downloads metadata (fwupd 2 has flags, 1.x getters)."""
    try:
        enabled = (remote.has_flag(Fwupd.RemoteFlags.ENABLED) if hasattr(Fwupd, "RemoteFlags")
                   and hasattr(remote, "has_flag") else remote.get_enabled())
        return bool(enabled) and remote.get_kind() == Fwupd.RemoteKind.DOWNLOAD
    except (AttributeError, TypeError):
        return False


def _problems(Fwupd, device) -> list[str]:
    try:
        problems = device.get_problems()
    except (AttributeError, TypeError):
        return []
    names = []
    bit = 1
    while problems and bit <= problems and bit < 2 ** 40:
        if problems & bit:
            try:
                names.append(Fwupd.device_problem_to_string(bit))
            except (AttributeError, TypeError):
                pass
        bit <<= 1
    return [name for name in names if name]


def device_facts(Fwupd, GLib, device, release, host: dict) -> dict:
    from luma_installer.depot_firmware_safety import ESP_HEADROOM
    facts: dict = {"problems": _problems(Fwupd, device)}
    for getter, name in (("get_battery_level", "device_battery_level"),
                         ("get_battery_threshold", "device_battery_threshold")):
        value = _int(device, getter)
        if 0 < value <= 100:
            facts[name] = value
    try:
        plugin = device.get_plugin() or ""
    except (AttributeError, TypeError):
        plugin = ""
    if plugin == "uefi_capsule":
        facts["esp_needed"] = _int(release, "get_size") + ESP_HEADROOM
    if plugin == "modem_manager":
        facts["modem_connected"] = modem_connected()
    try:
        facts["update_error"] = device.get_update_error() or ""
    except (AttributeError, TypeError):
        pass
    return facts


# ── Installing ───────────────────────────────────────────────────────────

#: fwupd statuses by how far an install has got (FwupdStatus nicks).
_WRITING = ("DEVICE_WRITE", "DEVICE_ERASE", "DEVICE_VERIFY")
_PREPARING = ("LOADING", "DECOMPRESSING", "DOWNLOADING", "SCHEDULING", "WAITING_FOR_AUTH")


def _journal_excerpt(since: float, lines: int = 40) -> str:
    """fwupd's own journal lines since the install began, for Details."""
    import subprocess
    try:
        result = subprocess.run(["journalctl", "-u", "fwupd.service", f"--since=@{int(since) - 2}",
                                 "-o", "short-iso", "--no-pager", "-n", str(lines), "-q"],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    from luma_installer.depot_firmware_safety import clean
    return "\n".join(clean(line) for line in result.stdout.splitlines() if line.strip())[-6000:]


def _device_after(Fwupd, GLib, device_id: str, guids) -> str:
    """present, bootloader or missing: where the device is after a failed install."""
    try:
        client = Fwupd.Client.new()
        _KEEP.append(client)
        devices = client.get_devices(None)
    except GLib.Error:
        return "unknown"
    wanted = {g.lower() for g in guids}
    for device in devices:
        same = device.get_id() == device_id or wanted & {g.lower() for g in (device.get_guids() or ())}
        if same:
            return "bootloader" if device.has_flag(Fwupd.DeviceFlags.IS_BOOTLOADER) else "present"
    return "missing"


def install(device_id: str) -> dict:
    import time
    try:
        Fwupd, GLib, client = _client()
    except (ImportError, ValueError) as error:
        return {"ok": False, "code": MISSING, "hint": "Firmware updates are not available on this computer."}
    from luma_installer.depot_firmware_safety import phase_from_message
    _KEEP.append(client)
    started = time.time()
    state = {"phase": "prepare", "wrote": False}

    def failed(error, *, device=None, guids=()) -> dict:
        message = (error.message or "") if hasattr(error, "message") else str(error)
        phase = phase_from_message(message, state["phase"])
        if state["wrote"] and phase in ("prepare", "detach"):
            phase = "write"
        domain = getattr(error, "domain", "") or ""
        code = error.code if isinstance(getattr(error, "code", None), int) and "fwupd" in str(domain) else None
        return {"ok": False, "code": FAILED, "hint": message.split("\n")[0][:200], "message": message[:2000],
                "fwupd_code": code, "domain": str(domain), "phase": phase,
                "device_after": _device_after(Fwupd, GLib, device_id, guids) if phase != "prepare" else "present",
                "log": _journal_excerpt(started)}

    status_names = {}
    for name in _WRITING + _PREPARING + ("DEVICE_RESTART", "DEVICE_BUSY", "DEVICE_READ"):
        value = getattr(Fwupd.Status, name, None)
        if value is not None:
            status_names[int(value)] = name

    def status_changed(*_):
        name = status_names.get(int(client.get_status()), "")
        if name in _WRITING:
            state["wrote"] = True
            phase = "write"
        elif name == "DEVICE_RESTART":
            phase = "attach" if state["wrote"] else "detach"
        elif name in _PREPARING:
            phase = "prepare" if not state["wrote"] else state["phase"]
        else:
            return
        if phase != state["phase"]:
            state["phase"] = phase
            _emit({"phase": phase})
    client.connect("notify::status", status_changed)
    client.connect("notify::percentage",
                   lambda *_: _emit({"progress": max(0.0, min(1.0, client.get_percentage() / 100.0))}))
    device = None
    try:
        device = next((d for d in _devices(Fwupd, GLib, client) if d.get_id() == device_id), None)
        _identify(GLib, client)
        client.ensure_networking()
        if device is None:
            return {"ok": False, "code": GONE, "hint": "That device is no longer connected.",
                    "fwupd_code": 8, "phase": "prepare", "device_after": "missing"}
        releases = client.get_upgrades(device_id, None)
        if not releases:
            return {"ok": False, "code": GONE, "hint": "This firmware update is no longer offered.",
                    "fwupd_code": 9, "phase": "prepare", "device_after": "present"}
    except GLib.Error as error:
        return failed(error, device=device)
    guids = tuple(device.get_guids() or ())
    # Asynchronously, with this thread running the client's main context, so the
    # percentage notifications are delivered while fwupd works.
    loop = GLib.MainLoop.new(GLib.MainContext.default(), False)
    outcome: dict = {}

    def finished(source, result):
        try:
            source.install_release_finish(result)
            outcome.update(ok=True, needs_reboot=device.has_flag(Fwupd.DeviceFlags.NEEDS_REBOOT))
        except GLib.Error as error:
            outcome.update(failed(error, device=device, guids=guids))
        loop.quit()
    client.install_release_async(device, releases[0], Fwupd.InstallFlags.NONE,
                                 Fwupd.ClientDownloadFlags.NONE, None, finished)
    loop.run()
    return outcome


def refresh() -> dict:
    """Download fresh update information from every enabled download remote."""
    try:
        Fwupd, GLib, client = _client()
    except (ImportError, ValueError) as error:
        return {"ok": False, "code": MISSING, "hint": str(error)}
    _KEEP.append(client)
    _identify(GLib, client)
    refreshed, last_error = 0, ""
    try:
        client.ensure_networking()
        for remote in client.get_remotes(None):
            if not _remote_downloads(Fwupd, remote):
                continue
            try:
                client.refresh_remote(remote, Fwupd.ClientDownloadFlags.NONE, None)
                refreshed += 1
            except (GLib.Error, TypeError) as error:
                last_error = getattr(error, "message", str(error))
    except GLib.Error as error:
        last_error = error.message
    if refreshed or not last_error:
        return {"ok": True, "refreshed": refreshed}
    return {"ok": False, "code": FAILED, "hint": last_error.split("\n")[0][:200]}


def _nothing_to_do(Fwupd, error) -> bool:
    """fwupd answers "no detected devices" with an error; that is an empty list."""
    try:
        return error.matches(Fwupd.error_quark(), Fwupd.Error.NOTHING_TO_DO)
    except (AttributeError, TypeError):
        return False


#: Clients stay referenced until the process ends; see the module docstring.
_KEEP: list = []


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        result = list_updates()
    elif argv[:1] == ["install"] and len(argv) == 2:
        result = install(argv[1])
    elif argv[:1] == ["refresh"]:
        result = refresh()
    else:
        sys.stderr.write("usage: python3 -m luma_depot.firmware_probe list | install DEVICE_ID | refresh\n")
        return 2
    _emit(result)
    return 0


if __name__ == "__main__":
    try:
        status = main(sys.argv[1:])
    except Exception as error:  # noqa: BLE001 - report it as a result, never a traceback on stdout
        _emit({"ok": False, "code": FAILED, "hint": str(error)[:200]})
        status = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)
