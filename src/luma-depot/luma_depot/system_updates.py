# SPDX-License-Identifier: Apache-2.0
"""Luma's update agent and fwupd, as the Updates tab sees them.

Depot draws and asks. ``luma-updated`` decides, downloads, stages and restarts
(ADR-030, section 6); fwupd flashes firmware. Neither is reimplemented here, and
nothing here restarts the computer on its own: Apply is called only from the
"Restart to finish updating" button.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil
import subprocess
import sys

from gi.repository import Gio, GLib

from luma_installer import depot_system_update as su

from .providers import ProviderError, run_async

_TAGS = re.compile(r'<[^>]+>')


def _unpack(proxy) -> dict:
    values = {}
    for name in su.PROPERTIES:
        variant = proxy.get_cached_property(name)
        if variant is not None:
            values[name] = variant.unpack()
    return values


class SystemUpdates:
    """Follows org.projectluma.Update1 and tells the window when it changes.

    The agent is D-Bus activated and exits when idle, so a proxy with no owner
    is normal. Reading its properties with an explicit GetAll starts it without
    asking it to check anything; when the bus is unreachable its published
    status file answers, then ``luma-update status --json``.
    """

    def __init__(self, on_change) -> None:
        self.on_change = on_change
        self.state: su.SystemUpdate | None = None
        self.proxy = None
        self.busy = ""
        self.busy_channel = ""
        self.error = ""
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES, None,
            su.BUS_NAME, su.OBJECT_PATH, su.INTERFACE, None, self._ready)

    # ── Reading ──────────────────────────────────────────────────────────

    def _ready(self, _source, result) -> None:
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            self.proxy = None
        if self.proxy is not None:
            self.proxy.connect("g-properties-changed", lambda *_: self._from_cache())
        self._read()

    def _from_cache(self) -> None:
        values = _unpack(self.proxy)
        if values:
            self.state = su.from_values(values, "dbus")
            self.on_change()

    def _read(self) -> None:
        if self.proxy is None:
            self._read_file()
            return
        if self.proxy.get_name_owner() and self.proxy.get_cached_property_names():
            self._from_cache()
            return

        def answered(connection, result):
            try:
                reply = connection.call_finish(result)
            except GLib.Error:
                self._read_file()
                return
            self.state = su.from_values(reply.unpack()[0], "dbus")
            self.on_change()
        self.proxy.get_connection().call(
            su.BUS_NAME, su.OBJECT_PATH, "org.freedesktop.DBus.Properties", "GetAll",
            GLib.Variant("(s)", (su.INTERFACE,)), GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 20000, None, answered)

    def _read_file(self) -> None:
        from .host_client import sandboxed
        if sandboxed():
            self.error = 'The Luma update service could not be reached. Retry or update Luma’s application service.'
            self.on_change()
            return
        def status():
            try:
                with open(su.STATUS_FILE, encoding="utf-8") as stream:
                    state = su.from_status_json(stream.read(65536), "file")
                if state.service:
                    return state
            except OSError:
                pass
            tool = shutil.which("luma-update")
            if tool is None:
                return su.NOT_INSTALLED
            completed = subprocess.run([tool, "status", "--json"], capture_output=True, text=True,
                                       timeout=20, check=False)
            return su.from_status_json(completed.stdout if completed.returncode == 0 else "")

        def done(result):
            self.state = result.value if result.ok else su.NOT_INSTALLED
            self.on_change()
        run_async(_guard(status), done)

    def refresh(self) -> None:
        """Check now: the explicit request, the only time Depot asks for a check."""
        if self.state is not None and self.state.service and self.state.managed:
            self.call("Check")
        else:
            self._read()

    # ── Asking ───────────────────────────────────────────────────────────

    def call(self, method: str, *arguments, then=None) -> None:
        """Ask the agent to act. Every method is polkit-checked on its side."""
        if self.proxy is None:
            self.error = "The update service is not available."
            self.on_change()
            return
        self.busy = method
        # The channel a channel change is about, for "Turning on Nightly…"; never a token.
        self.busy_channel = arguments[0] if arguments and method in _CHANNEL_METHODS else ""
        self.error = ""
        signature = "".join("b" if isinstance(value, bool) else "s" for value in arguments)
        parameters = GLib.Variant(f"({signature})", arguments) if arguments else None

        def finished(proxy, result):
            self.busy = ""
            self.busy_channel = ""
            try:
                proxy.call_finish(result)
                ok = True
            except GLib.Error as error:
                ok = False
                message = _remote_text(error)
                remote = Gio.DBusError.get_remote_error(error) or ""
                if remote.endswith(".NotEntitled"):
                    self.error = "This Luma account cannot get early updates yet."
                elif remote.endswith(".SignInRequired"):
                    self.error = ("Luma Connect on this computer is signed out. Sign in to Luma Connect again, "
                                  "then choose the channel again.")
                elif remote.endswith(".Unmanaged") and method in ("EnrollPreview", "AdoptChannel"):
                    self.error = ("This computer cannot start following a Luma channel yet. "
                                  + (_sentence(message) or ""))
                elif remote == "org.freedesktop.DBus.Error.UnknownMethod":
                    self.error = ("Luma\u2019s update service on this computer is too old for that. Restart "
                                  "after the next update, then try again.")
                elif remote.endswith(".NotAuthorized"):
                    self.error = "That needs an administrator\u2019s permission."
                elif remote.endswith(".NothingToDo") and method == "Cancel":
                    self.error = ""  # the download had already finished or stopped
                elif remote.endswith(".Busy"):
                    self.error = "Luma is already working on an update. Try again in a moment."
                else:
                    self.error = _sentence(message) or {
                        "Check": "Luma could not check for updates.",
                        "Download": "The update did not download.",
                        "Cancel": "Luma could not stop the download.",
                        "Apply": "Luma could not restart to finish updating.",
                        "Rollback": "Luma could not go back to the previous version.",
                        "EnrollPreview": "Luma could not turn on early updates.",
                        "LeavePreview": "Luma could not turn off early updates.",
                        "SetAutomaticDownload": "Luma could not change how updates download.",
                        "IgnoreVersion": "Luma could not ignore that version.",
                        "ClearIgnoredVersion": "Luma could not stop ignoring that version.",
                        "SetChannel": "Luma could not change the update channel.",
                        "SetChannelNow": "Luma could not switch to that channel.",
                    }.get(method, "The update service did not do that.")
            self._read()
            if ok and then is not None:
                then()
        # "When done" methods can wait up to 20 minutes behind a running check.
        self.proxy.call(method, parameters, Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
                        25 * 60 * 1000, None, finished)
        self.on_change()

    def restart(self) -> None:
        """Restart to finish: through the session, so apps can save; the agent only without one.

        gnome-session answers Reboot only when its end-session dialog is settled,
        which takes as long as the person needs, so the call has no timeout. Only
        a missing session manager falls back to the agent's Apply(). A cancelled
        dialog, an inhibitor or a refusal is final: Depot never restarts past it.
        """
        try:
            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.call("Apply")
            return

        def finished(connection, result):
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                outcome = su.restart_outcome(Gio.DBusError.get_remote_error(error) or "")
                if outcome == "agent":
                    self.call("Apply")
                elif outcome == "refused":
                    reason = _remote_text(error)
                    self.error = _sentence(f"Luma did not restart: {reason}") if reason else ""
                    self.error = self.error or "Luma did not restart. Restart from the system menu when you are ready."
                    self.on_change()
        session.call("org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager",
                     "Reboot", None, None, Gio.DBusCallFlags.NONE, GLib.MAXINT, None, finished)

    def set_channel(self, channel: str) -> None:
        """Follow a channel, then look for its newest release straight away, so the
        person sees what it offers rather than waiting for the next scheduled check."""
        check = lambda: self.call("Check") if self.state is not None and self.state.managed else None
        self.call("SetChannel", channel, then=check)


_CHANNEL_METHODS = ("EnrollPreview", "SetChannel", "SetChannelNow", "AdoptChannel")


def _remote_text(error) -> str:
    """The message of a D-Bus error without GDBus's "GDBus.Error:<name>: " prefix.

    ``Gio.DBusError.strip_remote_error`` cannot be used from Python: PyGObject
    passes it a copy of the error and returns only a boolean."""
    text = getattr(error, "message", "") or ""
    if text.startswith("GDBus.Error:"):
        _name, _, text = text.partition(": ")
    return text


def _sentence(message: str) -> str:
    text = (message or "").strip()
    return text if 0 < len(text) < 200 and "\n" not in text else ""


def _guard(work):
    def guarded():
        try:
            return work()
        except (GLib.Error, OSError, subprocess.SubprocessError) as error:
            raise ProviderError(str(error), hint="") from error
    return guarded


# ── Firmware ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FirmwareUpdate:
    device_id: str
    device: str
    vendor: str
    current_version: str
    version: str
    summary: str
    notes: str
    urgency: str
    needs_reboot: bool
    requires_ac: bool
    details_url: str = ""
    #: What fwupd knows about the kind of device, for plain words (depot_firmware.describe).
    protocols: tuple[str, ...] = ()
    icons: tuple[str, ...] = ()
    plugin: str = ""
    guids: tuple[str, ...] = ()
    security_release: bool = False
    vendor_ids: tuple[str, ...] = ()
    #: False for parts built into the computer (fwupd's "internal" flag).
    removable: bool = False
    size: int = 0
    #: Set when Luma's signed firmware block list pauses this release.
    blocked_reason: str = ""

    @property
    def description(self):
        from luma_installer.depot_firmware import describe
        return describe(self.device, protocols=self.protocols, icons=self.icons, plugin=self.plugin,
                        urgency=self.urgency, security_release=self.security_release)

    @property
    def security(self) -> bool:
        return self.description.security


def plain(markup: str) -> str:
    text = (markup or "").replace("</p>", "\n\n").replace("<li>", "• ").replace("</li>", "\n")
    text = _TAGS.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


#: How long a firmware check may take before Depot stops waiting. fwupd can be
#: slow to answer while it starts; installs are never timed out.
FIRMWARE_CHECK_SECONDS = 90

#: What the firmware section says when a check did not finish.
FIRMWARE_CHECK_FAILED = ("Luma couldn\u2019t check for hardware updates just now. "
                         "Everything else here still works.")
FIRMWARE_INSTALL_STOPPED = ("The firmware update stopped before Depot heard back. "
                            "Check again in a moment to see whether it was installed.")


def _probe_command() -> list[str]:
    return [sys.executable, "-P", "-m", "luma_depot.firmware_probe"]


def _probe_result(text: str | None) -> dict | None:
    """The last ``ok`` line the probe wrote, or None when it wrote none."""
    result = None
    for line in (text or "").splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and "ok" in payload:
            result = payload
    return result


def _firmware_update(values: dict) -> FirmwareUpdate:
    fields = {name: values[name] for name in FirmwareUpdate.__dataclass_fields__ if name in values}
    for name in ("protocols", "icons", "guids", "vendor_ids"):
        fields[name] = tuple(str(value) for value in fields.get(name) or ())
    return FirmwareUpdate(**fields)


def _exit_words(process) -> str:
    if process.get_if_signaled():
        return f"ended by signal {process.get_term_sig()}"
    if process.get_if_exited():
        return f"exited with status {process.get_exit_status()}"
    return "ended"


def _journal_firmware(line: str, **fields) -> None:
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, PRIORITY="4", SYSLOG_IDENTIFIER="luma-depot",
                             **{f"LUMA_FIRMWARE_{key.upper()}": str(value) for key, value in fields.items()})
    except Exception:  # noqa: BLE001
        pass


@dataclass(frozen=True)
class Offer:
    """What the hardware card shows for one update, decided before Install."""
    #: ready, unmet (a requirement blocks Install), held (backing off after
    #: failures), paused (known not to work here, or blocked by Luma).
    state: str
    requirements: tuple = ()
    reason: str = ""
    until: object = None


class Firmware:
    """Devices with firmware updates from LVFS, installed only when asked.

    fwupd is reached only through ``luma_depot.firmware_probe`` in a process of
    its own, so nothing fwupd or libfwupd does can close Depot: a check that
    fails, hangs or crashes leaves ``problem`` set and the rest of the window
    as it was.

    Around each install (luma_installer.depot_firmware_safety): what must be
    true first (:meth:`offer`), a plain explanation when it fails
    (``failures``), and a record of attempts that holds back an offer after
    failures that changed nothing.
    """

    def __init__(self, on_change, *, command: list[str] | None = None,
                 check_seconds: int = FIRMWARE_CHECK_SECONDS, attempts_path=None) -> None:
        from luma_installer import depot_firmware_safety as safety
        self.on_change = on_change
        self.command = list(command) if command else _probe_command()
        self.check_seconds = check_seconds
        self.updates: tuple[FirmwareUpdate, ...] = ()
        self.available = None          # None until known; False when fwupd is not installed
        self.problem = ""              # set when the last check did not finish
        self.on_battery = False
        self.host: dict = {}
        self.facts: dict[str, dict] = {}
        self.installing = ""
        self.progress = 0.0
        self.phase = ""
        #: device_id -> safety.Failure from the last install of that device.
        self.failures: dict = {}
        self.refreshing = False
        self.refresh_error = ""
        self.error = ""                # kept for callers that show one line
        self.loaded = False
        self.attempts = safety.Attempts.load(attempts_path)
        self._check = None
        self._check_again = False
        self._reported: set = set()

    @property
    def checking(self) -> bool:
        return self._check is not None

    @property
    def fwupd_build(self) -> str:
        return str(self.host.get("fwupd_build") or self.host.get("daemon_version") or "")

    def _spawn(self, *arguments: str):
        return Gio.Subprocess.new([*self.command, *arguments], Gio.SubprocessFlags.STDOUT_PIPE)

    # ── What each offer needs ────────────────────────────────────────────

    def key(self, update: FirmwareUpdate) -> str:
        from luma_installer.depot_firmware_safety import attempt_key
        return attempt_key(update.guids[0] if update.guids else update.device_id, update.plugin,
                           update.current_version, update.version, self.fwupd_build)

    def offer(self, update: FirmwareUpdate) -> Offer:
        from luma_installer import depot_firmware_safety as safety
        if update.blocked_reason:
            return Offer("paused", reason=update.blocked_reason)
        issue = safety.known_issue(update, self.fwupd_build)
        if issue is not None:
            return Offer("paused", reason=issue.reason)
        held = safety.hold(self.attempts, self.key(update))
        if held is not None:
            return Offer(held.state, reason=held.reason, until=held.until)
        facts = {**self.host, **self.facts.get(update.device_id, {})}
        needs = tuple(safety.requirements(update, facts, device_name=_device_words(update),
                                          kind=update.description.kind))
        return Offer("unmet" if safety.blocking(needs) else "ready", needs)

    def attention(self) -> tuple[FirmwareUpdate, ...]:
        """Updates a person can act on now: not paused or held by Luma."""
        return tuple(u for u in self.updates if self.offer(u).state in ("ready", "unmet")
                     or u.device_id in self.failures)

    def _report_offers(self) -> None:
        """Journal each paused, held or unmet offer once per check result."""
        from luma_installer import depot_firmware_safety as safety
        for update in self.updates:
            offer = self.offer(update)
            if offer.state == "ready":
                continue
            detail = offer.reason or "; ".join(r.text for r in offer.requirements if r.blocking) or \
                "; ".join(r.text for r in offer.requirements)
            marker = (update.device_id, update.version, offer.state, detail)
            if marker in self._reported:
                continue
            self._reported.add(marker)
            safety.journal({"paused": "blocked" if update.blocked_reason else "paused", "held": "held",
                            "unmet": "unmet"}[offer.state], update, fwupd_build=self.fwupd_build,
                           detail=detail, requirements=",".join(r.key for r in offer.requirements))

    # ── Checking ─────────────────────────────────────────────────────────

    def load(self) -> None:
        if self._check is not None:
            self._check_again = True
            return
        try:
            process = self._spawn("list")
        except GLib.Error as error:
            self._checked(None, f"could not start: {error.message}")
            return
        check = {"process": process, "timer": 0, "timed_out": False}
        check["timer"] = GLib.timeout_add_seconds(self.check_seconds, self._check_timed_out, check)
        self._check = check
        process.communicate_utf8_async(None, None, self._check_finished, check)

    def _check_timed_out(self, check) -> bool:
        check["timer"] = 0
        check["timed_out"] = True
        check["process"].force_exit()
        return GLib.SOURCE_REMOVE

    def _check_finished(self, process, result, check) -> None:
        if check["timer"]:
            GLib.source_remove(check["timer"])
            check["timer"] = 0
        try:
            _ok, output, _errors = process.communicate_utf8_finish(result)
        except GLib.Error:
            output = ""
        payload = _probe_result(output)
        if check["timed_out"]:
            detail = f"no answer after {self.check_seconds} seconds"
        elif payload is None:
            detail = _exit_words(process)
        else:
            detail = ""
        self._check = None
        self._checked(payload, detail)
        if self._check_again:
            self._check_again = False
            self.load()

    def _checked(self, payload: dict | None, detail: str) -> None:
        self.loaded = True
        if payload is not None and payload.get("ok"):
            try:
                values = list(payload.get("updates") or ())
                updates = tuple(_firmware_update(value) for value in values)
                facts = {update.device_id: dict(value.get("facts") or {})
                         for update, value in zip(updates, values)}
                host = dict(payload.get("host") or {})
            except (TypeError, KeyError, AttributeError, ValueError) as error:
                payload, detail = None, f"unreadable answer: {error}"
            else:
                self.available = True
                self.problem = ""
                self.updates = updates
                self.facts = facts
                self.host = host
                self.on_battery = bool(payload.get("on_battery", host.get("on_battery")))
                offered = {update.device_id for update in updates}
                self.failures = {device: failure for device, failure in self.failures.items()
                                 if device in offered or failure.stage != "before"}
                self._report_offers()
        if payload is not None and not payload.get("ok") and payload.get("code") == "missing":
            self.available = False
            self.problem = ""
            self.updates = ()
        elif payload is None or not payload.get("ok"):
            if payload is not None:
                detail = f"{payload.get('code', 'failed')}: {payload.get('hint', '')}".strip()
            self.available = True
            self.problem = FIRMWARE_CHECK_FAILED
            self.updates = ()
            _journal_firmware(f"Depot: the firmware check did not finish ({detail})",
                              check="failed", detail=detail)
        self.on_change()

    # ── Refreshing update information ────────────────────────────────────

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.refresh_error = ""
        self.on_change()
        try:
            process = self._spawn("refresh")
        except GLib.Error as error:
            self._refreshed(None, error.message)
            return
        process.communicate_utf8_async(None, None, self._refresh_finished)

    def _refresh_finished(self, process, result) -> None:
        try:
            _ok, output, _errors = process.communicate_utf8_finish(result)
        except GLib.Error:
            output = ""
        self._refreshed(_probe_result(output), _exit_words(process))

    def _refreshed(self, payload, detail) -> None:
        self.refreshing = False
        if payload is None or not payload.get("ok"):
            self.refresh_error = ("Luma couldn’t refresh hardware update information. Check your connection "
                                  "and try again.")
            _journal_firmware("Depot: refreshing firmware metadata failed "
                              f"({(payload or {}).get('hint') or detail})", check="refresh",
                              detail=(payload or {}).get("hint") or detail)
        self.load()
        self.on_change()

    # ── Installing ───────────────────────────────────────────────────────

    def install(self, update: FirmwareUpdate) -> None:
        from luma_installer import depot_firmware_safety as safety
        if self.installing:
            return
        if self.offer(update).state != "ready":
            # Install is not offered in the other states; a stale click changes nothing.
            return
        self.installing = update.device_id
        self.progress = 0.0
        self.phase = "prepare"
        self.error = ""
        self.failures.pop(update.device_id, None)
        safety.journal("started", update, fwupd_build=self.fwupd_build)
        self.on_change()
        try:
            process = self._spawn("install", update.device_id)
        except GLib.Error as error:
            self._installed(None, {"update": update}, f"could not start: {error.message}")
            return
        job = {"process": process, "result": None, "update": update}
        job["stream"] = Gio.DataInputStream.new(process.get_stdout_pipe())
        job["stream"].read_line_async(GLib.PRIORITY_DEFAULT, None, self._install_line, job)

    def _install_line(self, stream, result, job) -> None:
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:
            job["process"].wait_async(None, self._install_exited, job)
            return
        try:
            payload = json.loads(line)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            if "ok" in payload:
                job["result"] = payload
            elif isinstance(payload.get("progress"), (int, float)):
                self._progress(float(payload["progress"]))
            elif isinstance(payload.get("phase"), str):
                self.phase = payload["phase"]
                self.on_change()
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._install_line, job)

    def _install_exited(self, process, result, job) -> None:
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        self._installed(job["result"], job, _exit_words(process) if job["result"] is None else "")

    def _installed(self, payload: dict | None, job, detail: str) -> None:
        from luma_installer import depot_firmware_safety as safety
        device = self.installing
        update = (job or {}).get("update")
        self.installing = ""
        phase, self.phase = self.phase, ""
        if payload is not None and payload.get("ok"):
            if update is not None:
                self.attempts.record(self.key(update), "succeeded", about=_about(update, self.fwupd_build))
                safety.journal("succeeded", update, fwupd_build=self.fwupd_build,
                               needs_reboot=int(bool(payload.get("needs_reboot"))))
            self.load()
            self.on_change()
            return
        if payload is None:
            # The helper ended without a result: say what is known, never guess
            # that nothing was written.
            # Only a helper that ended while still downloading and checking is
            # known to have changed nothing.
            failure = safety.explain(None, detail, phase="prepare" if phase == "prepare" and not self.progress
                                     else "unknown",
                                     device_name=_device_words(update) if update else "this device")
            _journal_firmware(f"Depot: a firmware install stopped before it reported back ({detail})",
                              check="install", detail=detail, device_id=device)
            self.error = FIRMWARE_INSTALL_STOPPED
        else:
            code = payload.get("fwupd_code")
            message = payload.get("message") or payload.get("hint") or ""
            if payload.get("code") == "gone" and code is None:
                code = 8
            failure = safety.explain(code, message, phase=str(payload.get("phase") or ""),
                                     device_after=str(payload.get("device_after") or "present"),
                                     device_name=_device_words(update) if update else "this device",
                                     removable=bool(getattr(update, "removable", False)),
                                     log=str(payload.get("log") or ""))
            self.error = failure.body
        self.failures[device] = failure
        if update is not None:
            self.attempts.record(self.key(update), "failed", stage=failure.stage, code=failure.code,
                                 kind=failure.kind, about=_about(update, self.fwupd_build))
            safety.journal("failed", update, fwupd_build=self.fwupd_build, failure=failure,
                           device_after=(payload or {}).get("device_after", "unknown"),
                           attempts=len(self.attempts.history(self.key(update))))
            held = safety.hold(self.attempts, self.key(update))
            if held is not None:
                safety.journal(held.state, update, fwupd_build=self.fwupd_build, detail=held.reason,
                               failures=held.failures)
        self.load()
        self.on_change()

    def _progress(self, value):
        self.progress = max(0.0, min(1.0, value))
        self.on_change()
        return GLib.SOURCE_REMOVE


def _device_words(update) -> str:
    """"your mobile broadband modem" from the plain title, for sentences."""
    title = update.description.title if update is not None else ""
    for prefix in ("Security update for ", "Update for "):
        if title.startswith(prefix):
            return title[len(prefix):]
    return "this device"


def _about(update, fwupd_build: str) -> dict:
    return {"device": update.device, "plugin": update.plugin, "current_version": update.current_version,
            "version": update.version, "fwupd": fwupd_build,
            "guid": update.guids[0] if update.guids else ""}


def _strings(item, getter) -> list[str]:
    method = getattr(item, getter, None)
    if method is None:
        return []
    try:
        values = method()
    except (GLib.Error, TypeError):
        return []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in (values or ()) if value]


def _security_release(Fwupd, release) -> bool:
    flag = getattr(getattr(Fwupd, "ReleaseFlags", None), "IS_SECURITY", None) if Fwupd else None
    try:
        if flag is not None and release.has_flag(flag):
            return True
    except (GLib.Error, TypeError):
        pass
    issues = _strings(release, "get_issues")
    return any(issue.upper().startswith("CVE-") for issue in issues)


def _journal_blocked(device, release, entry) -> None:
    from luma_installer.depot_firmware import BLOCKED_MESSAGE_ID
    line = (f"Depot: firmware {release.get_version() or ''} for {device.get_name() or 'a device'} is blocked "
            f"by Luma{': ' + entry.reason if entry.reason else ''}")
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, MESSAGE_ID=BLOCKED_MESSAGE_ID, PRIORITY="5", SYSLOG_IDENTIFIER="luma-depot",
                             LUMA_FIRMWARE_GUID=entry.guid, LUMA_FIRMWARE_VERSION=release.get_version() or "",
                             LUMA_FIRMWARE_DEVICE=device.get_name() or "", LUMA_FIRMWARE_REASON=entry.reason)
    except Exception:  # noqa: BLE001
        pass


def battery_low(threshold: int = 30) -> bool:
    """On battery at or below ``threshold`` percent. Unknown counts as not low only
    when UPower says the computer is on mains power."""
    if not on_battery():
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice",
                              "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", ("org.freedesktop.UPower.Device", "Percentage")),
                              GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
        return float(reply.unpack()[0]) <= threshold
    except (GLib.Error, TypeError, ValueError):
        return True


def on_battery() -> bool:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync("org.freedesktop.UPower", "/org/freedesktop/UPower",
                              "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", ("org.freedesktop.UPower", "OnBattery")),
                              GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
        return bool(reply.unpack()[0])
    except GLib.Error:
        return True  # Unknown power must defer automatic downloads, not assume mains.
