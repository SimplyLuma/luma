# SPDX-License-Identifier: Apache-2.0

"""Read-only readiness discovery for Prairie Phone."""

from __future__ import annotations

import shutil
import subprocess
import re
import os
import json
import sqlite3
import time
import uuid
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from .messages_backend import normalize_address


@dataclass(frozen=True)
class PhoneCapability:
    available: bool
    reason: str
    modem_count: int = 0
    has_audio_route: bool = False
    # No cellular modem at all, as on most desktops and laptops: calls need a
    # phone paired through Luma Connect. This is a setup to offer, not a fault.
    no_modem: bool = False


# Said once, calmly, where the call button is. Never names a service or a tool.
NO_MODEM_REASON = "To make calls, pair your phone in Connect."


@dataclass(frozen=True)
class CallRecord:
    uid: str
    address: str
    direction: str
    started: int
    duration: int
    outcome: str


class CallPhase(str, Enum):
    """Presentation-neutral call lifecycle derived from native service state."""

    IDLE = "idle"
    PREPARING = "preparing"
    DIALLING = "dialling"
    RINGING_OUTGOING = "ringing-outgoing"
    INCOMING = "incoming"
    CONNECTING = "connecting"
    ACTIVE = "active"
    HELD = "held"
    WAITING = "waiting"
    MULTI_CALL = "multi-call"
    ENDING = "ending"
    ENDED = "ended"
    FAILED = "failed"


@dataclass(frozen=True)
class NativeCall:
    call_id: str
    address: str
    direction: str
    phase: CallPhase
    reason: str = ""
    started_at: int = 0
    answered_at: int = 0


@dataclass(frozen=True)
class CallSession:
    """Authoritative UI snapshot for one call.

    `connected_at` is populated only from the native `active` state.  Keeping
    this reducer free of GTK makes restart, ordering and race behavior directly
    testable.
    """

    call_id: str = ""
    address: str = ""
    direction: str = "outgoing"
    phase: CallPhase = CallPhase.IDLE
    reason: str = ""
    started_at: int = 0
    connected_at: int = 0
    input_locked: bool = False

    @classmethod
    def preparing(cls, address: str, now: int | None = None) -> "CallSession":
        return cls(
            address=normalize_address(address),
            phase=CallPhase.PREPARING,
            started_at=int(time.time() if now is None else now),
            input_locked=True,
        )

    @classmethod
    def from_native(cls, call: NativeCall) -> "CallSession":
        return cls(
            call_id=call.call_id,
            address=call.address,
            direction=call.direction,
            phase=call.phase,
            reason=call.reason,
            started_at=call.started_at,
            connected_at=call.answered_at if call.phase is CallPhase.ACTIVE else 0,
            input_locked=call.phase in {CallPhase.ENDING, CallPhase.ENDED},
        )

    def with_call_id(self, call_id: str) -> "CallSession":
        return self._replace(call_id=call_id, phase=CallPhase.DIALLING, input_locked=False)

    def native_state(
        self, state: str, reason: str = "", now: int | None = None
    ) -> "CallSession":
        phase = phase_from_native(state, self.direction)
        connected_at = self.connected_at
        if phase is CallPhase.ACTIVE and connected_at == 0:
            connected_at = int(time.time() if now is None else now)
        return self._replace(
            phase=phase,
            reason=reason,
            connected_at=connected_at,
            input_locked=phase in {CallPhase.ENDING, CallPhase.ENDED, CallPhase.FAILED},
        )

    def ending(self) -> "CallSession":
        if self.input_locked:
            return self
        return self._replace(phase=CallPhase.ENDING, input_locked=True)

    def deleted(self, reason: str = "") -> "CallSession":
        failed = bool(reason) and reason not in {
            "completed", "local-hangup", "remote-hangup", "cancelled"
        }
        return self._replace(
            phase=CallPhase.FAILED if failed else CallPhase.ENDED,
            reason=reason or self.reason,
            input_locked=True,
        )

    def elapsed(self, now: int | None = None) -> int:
        if self.connected_at <= 0:
            return 0
        return max(0, int(time.time() if now is None else now) - self.connected_at)

    def _replace(self, **changes) -> "CallSession":
        values = self.__dict__ | changes
        return CallSession(**values)


@dataclass(frozen=True)
class RecentCallGroup:
    address: str
    direction: str
    outcome: str
    started: int
    duration: int
    count: int
    records: tuple[CallRecord, ...]


def phase_from_native(state: str, direction: str = "outgoing") -> CallPhase:
    normalized = state.strip().casefold().replace("_", "-")
    mapping = {
        "dialing": CallPhase.DIALLING,
        "dialling": CallPhase.DIALLING,
        "ringing": CallPhase.RINGING_OUTGOING,
        "incoming": CallPhase.INCOMING,
        "connecting": CallPhase.CONNECTING,
        "active": CallPhase.ACTIVE,
        "held": CallPhase.HELD,
        "waiting": CallPhase.WAITING,
        "ending": CallPhase.ENDING,
        "terminated": CallPhase.ENDED,
        "failed": CallPhase.FAILED,
    }
    if normalized == "ringing" and direction == "incoming":
        return CallPhase.INCOMING
    return mapping.get(normalized, CallPhase.FAILED)


def classify_call_outcome(
    *,
    direction: str,
    connected_at: int = 0,
    reason: str = "",
    declined: bool = False,
) -> str:
    """Reduce a native final state to its persisted, user-facing outcome.

    An intentional local hangup is not a network failure. In particular, an
    outgoing call cancelled while still ringing has no ``active`` timestamp,
    but imsd still truthfully identifies it as ``local-hangup``.
    """

    normalized_direction = direction.strip().casefold()
    normalized_reason = reason.strip().casefold().replace("_", "-")
    if connected_at:
        return "completed"
    if declined:
        return "rejected"
    if normalized_reason in {"local-hangup", "cancelled"}:
        return "rejected" if normalized_direction == "incoming" else "cancelled"
    if normalized_direction == "incoming" and normalized_reason in {
        "",
        "remote-hangup",
        "no-answer",
    }:
        return "missed"
    return "failed"


def format_phone_number(address: str) -> str:
    """Conservative display formatting which preserves country-code input."""

    raw = address.strip()
    if not raw:
        return ""
    if "*" in raw or "#" in raw:
        return "".join(character for character in raw if character in "+0123456789*#")
    prefix = "+" if raw.startswith("+") else ""
    digits = re.sub(r"\D", "", raw)
    if prefix == "" and len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if prefix == "+" and len(digits) == 11 and digits.startswith("1"):
        return f"+1 {digits[1:4]} {digits[4:7]} {digits[7:]}"
    if len(digits) <= 3:
        return prefix + digits
    groups = []
    while digits:
        size = 3 if len(digits) > 4 else len(digits)
        groups.append(digits[:size])
        digits = digits[size:]
    return prefix + " ".join(groups)


def format_call_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def audio_input_muted(runner=None) -> bool:
    """Return the authoritative PipeWire microphone mute state."""
    output = (runner or _run_checked)(
        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"]
    )
    return "[MUTED]" in output.upper()


def set_audio_input_muted(muted: bool, runner=None) -> None:
    """Set microphone transmission through the native PipeWire route."""
    (runner or _run_checked)(
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1" if muted else "0"]
    )


AUDIO_ROUTE_BUS_NAME = "org.projectluma.AudioRoute1"
AUDIO_ROUTE_OBJECT_PATH = "/org/projectluma/AudioRoute1"
AUDIO_ROUTE_INTERFACE = AUDIO_ROUTE_BUS_NAME


def audio_output_route(runner=None) -> str:
    """Return the native call output endpoint, or ``unknown`` when absent."""
    output = (runner or _run_checked)(
        [
            "busctl", "--system", "call",
            AUDIO_ROUTE_BUS_NAME, AUDIO_ROUTE_OBJECT_PATH, AUDIO_ROUTE_INTERFACE,
            "GetRoute",
        ]
    )
    match = re.fullmatch(r'\s*s\s+"(speaker|earpiece|unknown)"\s*', output)
    return match.group(1) if match else "unknown"


def set_audio_output_route(route: str, runner=None) -> None:
    """Select one of the physically accepted internal call endpoints."""
    if route not in {"speaker", "earpiece"}:
        raise ValueError("Unsupported audio output route")
    (runner or _run_checked)(
        [
            "busctl", "--system", "call",
            AUDIO_ROUTE_BUS_NAME, AUDIO_ROUTE_OBJECT_PATH, AUDIO_ROUTE_INTERFACE,
            "SetRoute", "s", route,
        ]
    )


def group_recent_calls(
    records: tuple[CallRecord, ...], *, missed_window: int = 3_600
) -> tuple[RecentCallGroup, ...]:
    """Group only adjacent missed calls from one address in a short window."""

    groups: list[RecentCallGroup] = []
    for record in records:
        if (
            groups
            and record.outcome == "missed"
            and groups[-1].outcome == "missed"
            and groups[-1].address == record.address
            and groups[-1].started - record.started <= missed_window
        ):
            previous = groups[-1]
            groups[-1] = RecentCallGroup(
                previous.address,
                previous.direction,
                previous.outcome,
                previous.started,
                previous.duration,
                previous.count + 1,
                previous.records + (record,),
            )
            continue
        groups.append(
            RecentCallGroup(
                record.address,
                record.direction,
                record.outcome,
                record.started,
                record.duration,
                1,
                (record,),
            )
        )
    return tuple(groups)


class FavouriteStore:
    """Stable user ordering for EDS contact identifiers."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from .phone_shared_data import directory
            path = directory("prairie/phone") / "favourites.json"
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def list(self) -> tuple[str, ...]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return ()
        return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()

    def set(self, contact_ids: tuple[str, ...]) -> None:
        ordered = tuple(dict.fromkeys(item for item in contact_ids if item))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ordered), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def toggle(self, contact_id: str) -> bool:
        current = list(self.list())
        if contact_id in current:
            current.remove(contact_id)
            active = False
        else:
            current.append(contact_id)
            active = True
        self.set(tuple(current))
        return active


class CallStore:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from .phone_shared_data import directory
            path=directory("prairie/phone")/"calls.db"
        self.path=Path(path); self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True); os.chmod(self.path.parent,0o700)
        self.connection=sqlite3.connect(self.path); self.connection.row_factory=sqlite3.Row
        self.connection.execute("CREATE TABLE IF NOT EXISTS calls(uid TEXT PRIMARY KEY,address TEXT NOT NULL,direction TEXT NOT NULL,started INTEGER NOT NULL,duration INTEGER NOT NULL,outcome TEXT NOT NULL)"); self.connection.commit(); os.chmod(self.path,0o600)
    def close(self): self.connection.close()
    def add(self,address:str,*,direction:str,started:int|None=None,duration:int=0,outcome:str="completed",uid:str|None=None)->CallRecord:
        address=normalize_address(address)
        if direction not in {"incoming","outgoing"}: raise ValueError("Invalid call direction.")
        if outcome not in {"completed","missed","failed","rejected","cancelled"}: raise ValueError("Invalid call outcome.")
        record=CallRecord(uid or uuid.uuid4().hex,address,direction,int(time.time() if started is None else started),max(0,int(duration)),outcome)
        self.connection.execute("INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?)",(record.uid,record.address,record.direction,record.started,record.duration,record.outcome)); self.connection.commit(); return record
    def delete(self, uid: str) -> bool:
        """Remove one call from the log on this device."""
        cursor = self.connection.execute("DELETE FROM calls WHERE uid=?", (uid,))
        self.connection.commit()
        return cursor.rowcount == 1

    def delete_address(self, address: str) -> int:
        """Remove every call to or from one number from the log."""
        cursor = self.connection.execute("DELETE FROM calls WHERE address=?", (address,))
        self.connection.commit()
        return cursor.rowcount

    def list(self)->tuple[CallRecord,...]:
        rows=self.connection.execute("SELECT * FROM calls ORDER BY started DESC,uid")
        return tuple(CallRecord(row["uid"],row["address"],row["direction"],row["started"],row["duration"],row["outcome"]) for row in rows)


class ModemVoiceTransport:
    def __init__(self,runner=None): self.runner=runner or _run_checked
    def dial(self,address:str)->str:
        capability=inspect_phone_capability()
        if not capability.available: raise RuntimeError(capability.reason)
        address=normalize_address(address); listing=self.runner(["mmcli","-L"]); match=re.search(r"/Modem/(\d+)",listing)
        if not match: raise RuntimeError("No cellular modem is ready.")
        created=self.runner(["mmcli","-m",match.group(1),f"--voice-create-call=number={address}"]); call=re.search(r"/Call/(\d+)",created)
        if not call: raise RuntimeError("The modem did not start the call.")
        self.runner(["mmcli","-o",call.group(1),"--start"]); return f"/org/freedesktop/ModemManager1/Call/{call.group(1)}"
    def hangup(self,path:str)->None:
        match=re.fullmatch(r"/org/freedesktop/ModemManager1/Call/(\d+)",path)
        if not match: raise ValueError("Invalid call identifier.")
        self.runner(["mmcli","-o",match.group(1),"--hangup"])
    def accept(self,path:str)->None:
        match=re.fullmatch(r"/org/freedesktop/ModemManager1/Call/(\d+)",path)
        if not match: raise ValueError("Invalid call identifier.")
        self.runner(["mmcli","-o",match.group(1),"--accept"])


class ImsVoiceTransport:
    """Call transport for Luma's userspace IMS service.

    Gio carries the dialled address in the D-Bus message body.  It is never
    placed in a subprocess argument or journal message by Prairie Phone.
    """

    BUS_NAME = "net.catcrafts.IMS1"
    OBJECT_PATH = "/net/catcrafts/IMS1"
    INTERFACE = "net.catcrafts.IMS1"

    def __init__(self, proxy=None) -> None:
        self.proxy = proxy or self._system_proxy()

    @classmethod
    def _system_proxy(cls):
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,
            cls.BUS_NAME,
            cls.OBJECT_PATH,
            cls.INTERFACE,
            None,
        )

    def _call(self, method: str, signature: str, values: tuple):
        from gi.repository import Gio, GLib

        result = self.proxy.call_sync(
            method,
            GLib.Variant(signature, values),
            Gio.DBusCallFlags.NONE,
            20_000,
            None,
        )
        return result.unpack() if result is not None else ()

    def dial(self, address: str) -> str:
        address = normalize_address(address)
        result = self._call("Dial", "(s)", (address,))
        if not result or not result[0]:
            raise RuntimeError("The IMS service did not return a call identifier.")
        return str(result[0])

    def hangup(self, call_id: str) -> None:
        if not call_id:
            raise ValueError("Invalid call identifier.")
        self._call("HangUp", "(s)", (call_id,))

    def decline(self, call_id: str) -> None:
        if not call_id:
            raise ValueError("Invalid call identifier.")
        # The native reducer compares and declines atomically. Never emulate
        # this with GetCalls followed by HangUp: Accept may win between them.
        if self._call("Decline", "(s)", (call_id,)) != (True,):
            raise PermissionError("The incoming call has changed.")

    def accept(self, call_id: str) -> None:
        if not call_id:
            raise ValueError("Invalid call identifier.")
        self._call("Accept", "(s)", (call_id,))

    def send_dtmf(self, call_id: str, tones: str) -> None:
        if not call_id:
            raise ValueError("Invalid call identifier.")
        if not tones or any(character not in "0123456789*#ABCD" for character in tones):
            raise ValueError("Invalid keypad tone.")
        self._call("SendDtmf", "(ss)", (call_id, tones))

    def calls(self) -> tuple[NativeCall, ...]:
        result = self._call("GetCalls", "()", ())
        rows = result[0] if result else ()
        calls = []
        for row in rows:
            call_id = str(row.get("uni", ""))
            direction = str(row.get("direction", "outgoing"))
            if not call_id:
                continue
            calls.append(
                NativeCall(
                    call_id=call_id,
                    address=str(row.get("number", "")),
                    direction=direction,
                    phase=phase_from_native(str(row.get("state", "")), direction),
                    reason=str(row.get("reason", "")),
                    started_at=int(row.get("startedAt", 0)),
                    answered_at=int(row.get("answeredAt", 0)),
                )
            )
        return tuple(calls)


class IncomingCallMonitor:
    """Subscribe to the IMS service's call signals on the system bus.

    imsd emits CallAdded/CallStateChanged/CallDeleted for every call, incoming
    ones included, but nothing in Prairie Core ever subscribed -- so a
    terminating INVITE created a call object, held the line for the full ring
    window and was never surfaced to the user.  This is the missing consumer.

    Callbacks receive only the call id, the peer address and the state string.
    The address is passed to the UI for display; it is never written to the
    journal or into a subprocess argument.
    """

    BUS_NAME = ImsVoiceTransport.BUS_NAME
    OBJECT_PATH = ImsVoiceTransport.OBJECT_PATH
    INTERFACE = ImsVoiceTransport.INTERFACE

    def __init__(self, on_added=None, on_incoming=None, on_state=None, on_ended=None,
                 on_owner=None, connection=None) -> None:
        self.on_added = on_added
        self.on_incoming = on_incoming
        self.on_state = on_state
        self.on_ended = on_ended
        self.on_owner = on_owner
        self._subscriptions: list[int] = []
        self._connection = connection
        self._watch = 0
        self._owner = None
        self._generation = 0
        self._revision = 0
        self._known = set()
        self._retry = 0
        self._retry_delay = 1
        self._pending = None
        self._stopped = False

    def start(self) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        if self._watch:
            return
        self._stopped = False
        self._connection = self._connection or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        for signal_name, handler in (
            ("CallAdded", self._handle_added),
            ("CallStateChanged", self._handle_state),
            ("CallDeleted", self._handle_deleted),
        ):
            self._subscriptions.append(
                self._connection.signal_subscribe(
                    self.BUS_NAME,
                    self.INTERFACE,
                    signal_name,
                    self.OBJECT_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    handler,
                )
            )
        self._watch = Gio.bus_watch_name_on_connection(
            self._connection, self.BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda _connection, _name, owner: self._owner_changed(owner),
            lambda *_args: self._owner_changed(None))

    def _owner_changed(self, owner):
        from gi.repository import Gio, GLib
        if self._stopped and owner is not None:
            return
        self._generation += 1
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        if self._retry:
            GLib.source_remove(self._retry)
            self._retry = 0
        self._owner = owner
        self._known.clear()
        self._retry_delay = 1
        if callable(self.on_owner):
            self.on_owner(owner)
        if owner:
            self._snapshot()

    def _snapshot(self):
        from gi.repository import Gio, GLib
        self._retry = 0
        if self._stopped or not self._owner or self._pending is not None:
            return False
        generation, revision = self._generation, self._revision
        cancel = Gio.Cancellable()
        self._pending = cancel

        def finished(connection, result):
            try:
                rows = connection.call_finish(result).unpack()[0]
            except GLib.Error as error:
                if generation == self._generation and self._owner:
                    self._pending = None
                    print(f"prairie-phone: IMS snapshot unavailable domain={error.domain} code={error.code}", flush=True)
                    self._retry = GLib.timeout_add_seconds(self._retry_delay, self._snapshot)
                    self._retry_delay = min(30, self._retry_delay * 2)
                return
            if generation != self._generation:
                return
            self._pending = None
            if revision != self._revision:
                # Signals won the race: don't resurrect a removed call from
                # an older snapshot. One coalesced new snapshot reconciles it.
                self._retry = GLib.idle_add(self._snapshot)
                return
            self._retry_delay = 1
            for info in rows:
                call_id = str(info.get("uni", ""))
                if not call_id:
                    continue
                direction = str(info.get("direction", "outgoing"))
                number = str(info.get("number", ""))
                state = str(info.get("state", "")).strip().casefold().replace("_", "-")
                if call_id not in self._known:
                    self._known.add(call_id)
                    if callable(self.on_added): self.on_added(call_id, direction, number)
                    if direction == "incoming" and state in {"incoming", "ringing", "waiting"}:
                        if callable(self.on_incoming): self.on_incoming(call_id, number)
                if callable(self.on_state): self.on_state(call_id, state, str(info.get("reason", "")))
        self._connection.call(self._owner, self.OBJECT_PATH, self.INTERFACE,
                              "GetCalls", GLib.Variant("()", ()), None,
                              Gio.DBusCallFlags.NO_AUTO_START, 5000, cancel, finished)
        return False

    def stop(self) -> None:
        from gi.repository import Gio
        if self._stopped:
            return
        self._stopped = True
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        self._owner_changed(None)
        if self._connection is None:
            return
        for token in self._subscriptions:
            self._connection.signal_unsubscribe(token)
        self._subscriptions.clear()

    # -- signal handlers -------------------------------------------------
    def _handle_added(self, _conn, _sender, _path, _iface, _signal, params):
        if self._stopped or (self._watch and _sender != self._owner): return
        self._revision += 1
        call_id, info = params.unpack()
        if str(call_id) in self._known:
            return
        self._known.add(str(call_id))
        if callable(self.on_added):
            self.on_added(
                str(call_id),
                str(info.get("direction", "")),
                str(info.get("number", "")),
            )
        # Only a terminating call needs to be presented; an outgoing call is
        # already owned by whichever window placed it.
        if str(info.get("direction", "")) != "incoming":
            return
        if callable(self.on_incoming):
            self.on_incoming(str(call_id), str(info.get("number", "")))

    def _handle_state(self, _conn, _sender, _path, _iface, _signal, params):
        if self._stopped or (self._watch and _sender != self._owner): return
        self._revision += 1
        call_id, state, reason = params.unpack()
        if callable(self.on_state):
            self.on_state(str(call_id), str(state), str(reason))

    def _handle_deleted(self, _conn, _sender, _path, _iface, _signal, params):
        if self._stopped or (self._watch and _sender != self._owner): return
        self._revision += 1
        (call_id,) = params.unpack()
        self._known.discard(str(call_id))
        if callable(self.on_ended):
            self.on_ended(str(call_id))


def preferred_voice_transport():
    """Keep the installed native IMS owner through discovery/registration loss."""
    # This packaged service is the FP6 native voice ownership contract.
    # A slow or restarting owner must not redirect dialing to MM voice.
    if Path("/usr/lib/systemd/system/luma-fp6-imsd.service").is_file():
        return ImsVoiceTransport()
    busctl = shutil.which("busctl")
    if busctl:
        status = _run(
            [
                busctl,
                "--system",
                "call",
                ImsVoiceTransport.BUS_NAME,
                ImsVoiceTransport.OBJECT_PATH,
                ImsVoiceTransport.INTERFACE,
                "GetStatus",
            ]
        )
        if '"registered" b true' in status or '"registered" b false' in status:
            # Registration loss must not send the same explicit call through
            # another modem transport. IMS owns rejection and recovery here.
            return ImsVoiceTransport()
    return ModemVoiceTransport()


def _run_checked(arguments:list[str])->str:
    return subprocess.run(arguments,check=True,capture_output=True,text=True,timeout=20).stdout


def _run(arguments: list[str]) -> str:
    try:
        return subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except subprocess.SubprocessError:
        return ""


def inspect_phone_capability() -> PhoneCapability:
    mmcli = shutil.which("mmcli")
    if mmcli is None:
        return PhoneCapability(False, NO_MODEM_REASON, no_modem=True)
    listing = _run([mmcli, "-L"])
    modem_indexes = tuple(dict.fromkeys(re.findall(r"/Modem/(\d+)", listing)))
    modem_count = len(modem_indexes)
    if modem_count == 0:
        return PhoneCapability(False, NO_MODEM_REASON, no_modem=True)
    if modem_count != 1:
        return PhoneCapability(False, "More than one modem needs selection.", modem_count=modem_count)

    # Key/value status is parsed in memory and never logged. Newer ModemManager
    # builds no longer expose the historical --simple-status action.
    status = _run([mmcli, "-m", modem_indexes[0], "--output-keyvalue"])
    if "sim-missing" in status.lower():
        return PhoneCapability(False, "Insert a SIM card to place calls.", modem_count=1)
    values = {}
    for line in status.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip().casefold()
    modem_state = next(
        (value for key, value in values.items() if key.endswith(".generic.state")), ""
    )
    registration = next(
        (
            value
            for key, value in values.items()
            if key.endswith(".3gpp.registration-state")
        ),
        "",
    )
    if modem_state not in {"registered", "connected"} and registration not in {
        "home", "roaming"
    }:
        return PhoneCapability(False, "The cellular modem is not ready.", modem_count=1)

    wpctl = shutil.which("wpctl")
    audio = _run([wpctl, "status"]) if wpctl else ""
    has_sink = _wpctl_section_has_item(audio, "Sinks:")
    has_source = _wpctl_section_has_item(audio, "Sources:")
    has_audio = has_sink and has_source
    if not has_audio:
        return PhoneCapability(
            False,
            "Call audio routing is not available on this device yet.",
            modem_count=1,
            has_audio_route=False,
        )
    return PhoneCapability(True, "Ready to call", modem_count=1, has_audio_route=True)


def _wpctl_section_has_item(status: str, heading: str) -> bool:
    in_section = False
    for line in status.splitlines():
        stripped = line.strip(" │├└─")
        if stripped == heading:
            in_section = True
            continue
        if in_section and stripped.endswith(":"):
            return False
        if in_section and re.search(r"\b\d+\.\s", stripped):
            return True
    return False
