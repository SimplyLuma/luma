#!/usr/bin/python3
"""Serve net.catcrafts.IMS1 on top of ModemManager Voice (Pixel 9 Pro Fold).

Prairie Phone speaks only the FP6 IMS service ABI (luma-fp6-imsd).  On the
Fold the modem is driven by ModemManager, so this daemon owns the IMS1 name
and maps every method and signal onto the Modem.Voice and Call interfaces.

Phone numbers never reach the journal: only call ids and states are logged.
Set LUMA_IMS_BRIDGE_BUS=session to run against a private session bus.
"""
import os
import signal
import sys
import time
import warnings

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

warnings.filterwarnings("ignore", category=DeprecationWarning)  # PyGObject API churn

NAME = "net.catcrafts.IMS1"
PATH = "/net/catcrafts/IMS1"
ERROR = "net.catcrafts.IMS1.Error"
MM = "org.freedesktop.ModemManager1"
MM_PATH = "/org/freedesktop/ModemManager1"
MM_MODEM = MM + ".Modem"
MM_VOICE = MM + ".Modem.Voice"
MM_CALL = MM + ".Call"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"

# Same ABI as src/imsd/implementations/main.cpp, plus Decline from newer
# Prairie Phone builds.
XML = """<node><interface name="net.catcrafts.IMS1">
  <method name="Dial"><arg type="s" name="number" direction="in"/><arg type="s" name="callUni" direction="out"/></method>
  <method name="DialEmergency"><arg type="s" name="number" direction="in"/><arg type="s" name="callUni" direction="out"/></method>
  <method name="HangUpEmergency"><arg type="s" name="callUni" direction="in"/></method>
  <method name="GetEmergencyCallState"><arg type="s" name="callUni" direction="in"/>
    <arg type="s" name="state" direction="out"/><arg type="s" name="reason" direction="out"/></method>
  <method name="HangUp"><arg type="s" name="callUni" direction="in"/></method>
  <method name="Accept"><arg type="s" name="callUni" direction="in"/></method>
  <method name="Decline"><arg type="s" name="callUni" direction="in"/><arg type="b" name="declined" direction="out"/></method>
  <method name="SendDtmf"><arg type="s" name="callUni" direction="in"/><arg type="s" name="tones" direction="in"/></method>
  <method name="GetCalls"><arg type="aa{sv}" name="calls" direction="out"/></method>
  <method name="GetStatus"><arg type="a{sv}" name="status" direction="out"/></method>
  <method name="AcquireESimReservation"><arg type="s" name="token" direction="in"/><arg type="b" name="accepted" direction="out"/></method>
  <method name="ReleaseESimReservation"><arg type="s" name="token" direction="in"/><arg type="b" name="released" direction="out"/></method>
  <signal name="CallAdded"><arg type="s" name="callUni"/><arg type="a{sv}" name="info"/></signal>
  <signal name="CallStateChanged"><arg type="s" name="callUni"/><arg type="s" name="state"/><arg type="s" name="reason"/></signal>
  <signal name="CallDeleted"><arg type="s" name="callUni"/></signal>
  <signal name="RegistrationChanged"><arg type="b" name="registered"/></signal>
</interface></node>"""

# MMCallState -> IMS1 state string (imsd: dialing/ringing/incoming/active/terminated).
STATES = {1: "dialing", 2: "ringing", 3: "incoming", 4: "active",
          5: "held", 6: "waiting", 7: "terminated"}
TERMINATED = 7
DIRECTIONS = {1: "incoming", 2: "outgoing"}
FAILURE_REASONS = {5, 6, 7}  # MMCallStateReason refused-or-busy, error, audio-setup-failed
REGISTERED = 8               # MM_MODEM_STATE_REGISTERED and above
EMERGENCY_NUMBERS = {"112", "911", "999", "000", "08", "110", "118", "119"}
NUMBER_WAIT_MS = 1500        # how long an incoming call may wait for its CLIP
ESIM = "org.projectluma.ESim1"
ESIM_PATH = "/org/projectluma/ESim1"
# luma-fold-mobile-mode: in data mode the attach PDN is the data APN and IMS
# cannot come up (one PDN only), so calls are refused up front.
MODE_FILE = os.environ.get("LUMA_MOBILE_MODE_FILE", "/run/luma-fold-mobile/mode")
DATA_MODE_TEXT = "Calls and texts are off while Mobile data is on."


def read_mode():
    try:
        with open(MODE_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def log(message):
    print(f"luma-fold-ims-bridge: {message}", flush=True)


class Call:
    def __init__(self, uni, path):
        self.uni, self.path = uni, path
        self.number, self.direction = "", "outgoing"
        self.state, self.reason = "dialing", ""
        self.started, self.answered = int(time.time()), 0
        self.local = False      # we asked for the hangup
        self.announced = False  # CallAdded emitted
        self.timer = 0

    def row(self):
        return {"uni": GLib.Variant("s", self.uni), "number": GLib.Variant("s", self.number),
                "state": GLib.Variant("s", self.state), "reason": GLib.Variant("s", self.reason),
                "startedAt": GLib.Variant("x", self.started),
                "answeredAt": GLib.Variant("x", self.answered),
                "direction": GLib.Variant("s", self.direction)}


class Bridge:
    def __init__(self, bus):
        self.bus = bus
        self.modem = None
        self.registered = False
        self.modem_registered = False
        self.data_mode = read_mode() == "data"
        self.mode_monitor = Gio.File.new_for_path(os.path.dirname(MODE_FILE)).monitor_directory(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self.mode_monitor.connect("changed", self.on_mode_file)
        self.calls = {}      # ModemManager call path -> Call
        self.emergency = {}  # uni -> final reason ("" while live)
        self.seq = 0
        self.reservation = None  # (owner, token, name watch): luma-esim holds the eUICC
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        bus.register_object(PATH, node.interfaces[0], self.on_method, None, None)
        for iface, member, handler, path in (
                (OBJECT_MANAGER, "InterfacesAdded", self.on_interfaces_added, MM_PATH),
                (OBJECT_MANAGER, "InterfacesRemoved", self.on_interfaces_removed, MM_PATH),
                (MM_VOICE, "CallAdded", self.on_mm_call_added, None),
                (MM_VOICE, "CallDeleted", self.on_mm_call_deleted, None),
                (MM_CALL, "StateChanged", self.on_mm_state_changed, None),
                (PROPERTIES, "PropertiesChanged", self.on_mm_properties, None)):
            bus.signal_subscribe(MM, iface, member, path, None, Gio.DBusSignalFlags.NONE, handler)
        Gio.bus_watch_name_on_connection(bus, MM, Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self.scan(), lambda *_: self.drop_modem())

    # -- ModemManager plumbing --------------------------------------------
    def mm(self, path, iface, method, args=None, reply=None, done=None):
        def finished(connection, result):
            try:
                value = connection.call_finish(result)
            except GLib.Error as error:
                # The remote message may echo a number; log only its name.
                log(f"{method} failed: {Gio.DBusError.get_remote_error(error) or error.code}")
                if done:
                    done(None, error)
                return
            if done:
                done(value, None)
        self.bus.call(MM, path, iface, method, args,
                      GLib.VariantType(reply) if reply else None,
                      Gio.DBusCallFlags.NONE, 30000, None, finished)

    def scan(self):
        def found(value, error):
            if error or self.modem:
                return
            for path, interfaces in sorted(value.unpack()[0].items()):
                if MM_VOICE in interfaces:
                    self.adopt(path, interfaces.get(MM_MODEM, {}))
                    return
            log("waiting for a modem with voice support")
        self.mm(MM_PATH, OBJECT_MANAGER, "GetManagedObjects", reply="(a{oa{sa{sv}}})", done=found)

    def adopt(self, path, modem_properties):
        self.modem = path
        log(f"using modem {path}")
        self.set_registered(modem_properties.get("State", 0) >= REGISTERED)

        def listed(value, error):
            if not error and self.modem == path:
                for call_path in value.unpack()[0]:
                    self.track(call_path)
        self.mm(path, MM_VOICE, "ListCalls", reply="(ao)", done=listed)

    def drop_modem(self):
        if self.modem:
            log(f"modem {self.modem} went away")
        for call in list(self.calls.values()):
            self.finish(call, "error", delete=False)
        self.modem = None
        self.set_registered(False)

    def on_mode_file(self, _monitor, _file, _other, _event):
        data_mode = read_mode() == "data"
        if data_mode != self.data_mode:
            self.data_mode = data_mode
            log(f"data mode={data_mode}")
            self.set_registered(self.modem_registered)

    def set_registered(self, modem_registered):
        self.modem_registered = modem_registered
        registered = modem_registered and not self.data_mode
        if registered != self.registered:
            self.registered = registered
            log(f"registered={registered}")
            self.emit("RegistrationChanged", "(b)", (registered,))

    def on_interfaces_added(self, _c, _s, _p, _i, _m, params):
        path, interfaces = params.unpack()
        if not self.modem and MM_VOICE in interfaces:
            self.adopt(path, interfaces.get(MM_MODEM, {}))

    def on_interfaces_removed(self, _c, _s, _p, _i, _m, params):
        path, interfaces = params.unpack()
        if path == self.modem and (MM_VOICE in interfaces or MM_MODEM in interfaces):
            self.drop_modem()
            self.scan()

    def on_mm_call_added(self, _c, _s, path, _i, _m, params):
        if path == self.modem:
            self.track(params.unpack()[0])

    def on_mm_call_deleted(self, _c, _s, path, _i, _m, params):
        call = self.calls.get(params.unpack()[0]) if path == self.modem else None
        if call:
            self.finish(call, "local-hangup" if call.local else "remote-hangup", delete=False)

    def on_mm_state_changed(self, _c, _s, path, _i, _m, params):
        call = self.calls.get(path)
        if call:
            _old, new, reason = params.unpack()
            self.apply_state(call, new, reason)

    def on_mm_properties(self, _c, _s, path, _i, _m, params):
        iface, changed, _invalidated = params.unpack()
        if iface == MM_MODEM and path == self.modem and "State" in changed:
            self.set_registered(changed["State"] >= REGISTERED)
        call = self.calls.get(path) if iface == MM_CALL else None
        if call and "Number" in changed:
            call.number = changed["Number"]
            if call.number and not call.announced:
                self.announce(call)
        if call and "State" in changed:
            self.apply_state(call, changed["State"], changed.get("StateReason", 0))

    # -- call bookkeeping ---------------------------------------------------
    def track(self, path):
        if path in self.calls:
            return self.calls[path]
        self.seq += 1
        call = self.calls[path] = Call(f"mm-call-{self.seq}", path)

        def loaded(value, error):
            if self.calls.get(path) is not call:
                return
            props = {} if error else value.unpack()[0]
            call.number = props.get("Number", "") or call.number
            call.direction = DIRECTIONS.get(props.get("Direction", 0), call.direction)
            if call.direction == "incoming" and call.state == "dialing":
                call.state = "incoming"
            if call.direction == "incoming" and not call.number:
                call.timer = GLib.timeout_add(NUMBER_WAIT_MS, self.announce, call)
            else:
                self.announce(call)
            self.apply_state(call, props.get("State", 0), props.get("StateReason", 0))
        self.mm(path, PROPERTIES, "GetAll", GLib.Variant("(s)", (MM_CALL,)), "(a{sv})", loaded)
        return call

    def announce(self, call):
        if call.timer:
            GLib.source_remove(call.timer)
            call.timer = 0
        if not call.announced:
            call.announced = True
            log(f"call {call.uni} added direction={call.direction} state={call.state}")
            self.emit("CallAdded", "(sa{sv})", (call.uni, call.row()))
        return False

    def apply_state(self, call, mm_state, mm_reason):
        if mm_state == TERMINATED:
            if call.local:
                reason = "local-hangup"
            elif mm_reason in FAILURE_REASONS and call.direction == "outgoing" and not call.answered:
                reason = "error"
            else:
                reason = "remote-hangup"
            self.finish(call, reason)
            return
        state = STATES.get(mm_state) or ("incoming" if call.direction == "incoming" else "dialing")
        if state == "active" and not call.answered:
            call.answered = int(time.time())
        if state != call.state:
            call.state = state
            if call.announced:
                log(f"call {call.uni} state={state}")
                self.emit("CallStateChanged", "(sss)", (call.uni, state, ""))

    def finish(self, call, reason, delete=True):
        if self.calls.get(call.path) is not call:
            return
        del self.calls[call.path]
        self.announce(call)
        call.state, call.reason = "terminated", reason
        log(f"call {call.uni} state=terminated reason={reason}")
        self.emit("CallStateChanged", "(sss)", (call.uni, "terminated", reason))
        self.emit("CallDeleted", "(s)", (call.uni,))
        if call.uni in self.emergency:
            self.emergency[call.uni] = reason
        if delete and self.modem:
            self.mm(self.modem, MM_VOICE, "DeleteCall", GLib.Variant("(o)", (call.path,)))

    def by_uni(self, uni):
        return next((call for call in self.calls.values() if call.uni == uni), None)

    def emit(self, member, signature, values):
        self.bus.emit_signal(None, PATH, NAME, member, GLib.Variant(signature, values))

    # -- IMS1 methods -----------------------------------------------------
    def on_method(self, _c, sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method in {"HangUpEmergency", "GetEmergencyCallState"} and args[0] not in self.emergency:
            return invocation.return_dbus_error(f"{ERROR}.NotEmergencyCall",
                                                "the call was not created by DialEmergency")
        if method == "AcquireESimReservation":
            return invocation.return_value(GLib.Variant("(b)", (self.acquire(sender, args[0]),)))
        if method == "ReleaseESimReservation":
            return invocation.return_value(GLib.Variant("(b)", (self.release(sender, args[0]),)))
        if method == "Dial":
            if self.reservation:
                return invocation.return_dbus_error(f"{ERROR}.Busy", "the eSIM is being changed")
            if self.data_mode:
                return invocation.return_dbus_error(f"{ERROR}.NotReady", DATA_MODE_TEXT)
            return self.dial(args[0], invocation, emergency=False)
        if method == "DialEmergency":
            if args[0].strip() not in EMERGENCY_NUMBERS:
                return invocation.return_dbus_error(f"{ERROR}.NotEmergencyNumber",
                                                    "only emergency numbers are accepted")
            self.cancel_reservation()
            return self.dial(args[0].strip(), invocation, emergency=True)
        if method == "GetCalls":
            rows = [call.row() for call in self.calls.values() if call.announced]
            return invocation.return_value(GLib.Variant("(aa{sv})", (rows,)))
        if method == "GetStatus":
            status = {"registered": GLib.Variant("b", self.registered),
                      "local": GLib.Variant("s", ""), "pcscf": GLib.Variant("s", ""),
                      "expiry": GLib.Variant("x", 0), "cseq": GLib.Variant("x", 0),
                      "ppi": GLib.Variant("s", ""), "backend": GLib.Variant("s", "modemmanager"),
                      "mode": GLib.Variant("s", "data" if self.data_mode else "calls")}
            return invocation.return_value(GLib.Variant("(a{sv})", (status,)))
        call = self.by_uni(args[0])
        if method == "GetEmergencyCallState":
            if call:
                return invocation.return_value(GLib.Variant("(ss)", (call.state, call.reason)))
            final = self.emergency[args[0]]
            return invocation.return_value(GLib.Variant("(ss)", ("terminated", final) if final else ("dialing", "")))
        if method == "Decline":
            if not call or call.direction != "incoming" or call.state not in {"incoming", "waiting"}:
                return invocation.return_value(GLib.Variant("(b)", (False,)))
            call.local = True
            self.mm(call.path, MM_CALL, "Hangup")
            return invocation.return_value(GLib.Variant("(b)", (True,)))
        if not call:  # imsd ignores stale ids for these too
            return invocation.return_value(None)
        log(f"{method} {call.uni}")
        if method in {"HangUp", "HangUpEmergency"}:
            call.local = True
            self.mm(call.path, MM_CALL, "Hangup")
            return invocation.return_value(None)
        if method == "SendDtmf":
            self.mm(call.path, MM_CALL, "SendDtmf", GLib.Variant("(s)", (args[1],)))
            return invocation.return_value(None)
        if method == "Accept":
            def accepted(_value, error):
                if error:
                    invocation.return_dbus_error(f"{ERROR}.Failed", "the modem did not answer the call")
                else:
                    invocation.return_value(None)
            return self.mm(call.path, MM_CALL, "Accept", done=accepted)
        invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "no such method")

    # -- eSIM reservation (same ABI as luma-fp6-imsd) ----------------------
    # luma-esim holds it while lpac talks to the eUICC: no calls start then,
    # and none may be live when it is granted. An emergency call cancels it.
    def acquire(self, owner, token):
        if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            return False
        if self.reservation:
            return self.reservation[:2] == (owner, token)
        if any(call.state != "terminated" for call in self.calls.values()):
            return False
        watch = Gio.bus_watch_name_on_connection(self.bus, owner, Gio.BusNameWatcherFlags.NONE,
                                                 None, lambda *_: self.drop_reservation())
        self.reservation = (owner, token, watch)
        log("eSIM reservation held")
        return True

    def release(self, owner, token):
        if not self.reservation or self.reservation[:2] != (owner, token):
            return False
        self.drop_reservation()
        return True

    def drop_reservation(self):
        if self.reservation:
            Gio.bus_unwatch_name(self.reservation[2])
            self.reservation = None
            log("eSIM reservation released")

    def cancel_reservation(self):
        if not self.reservation:
            return
        owner, token, _watch = self.reservation
        self.bus.call(owner, ESIM_PATH, ESIM, "CancelReservation", GLib.Variant("(s)", (token,)),
                      None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None, None)
        self.drop_reservation()

    def dial(self, number, invocation, emergency):
        if not self.modem:
            return invocation.return_dbus_error(f"{ERROR}.NotReady", "no voice modem is available")
        modem = self.modem

        def created(value, error):
            if error:
                return invocation.return_dbus_error(f"{ERROR}.Failed", "the modem did not create the call")
            call = self.track(value.unpack()[0])
            if emergency:
                self.emergency[call.uni] = ""
            log(f"dial {call.uni} emergency={emergency}")
            invocation.return_value(GLib.Variant("(s)", (call.uni,)))

            def started(_value, start_error):
                if start_error:
                    self.finish(call, "error")
            self.mm(call.path, MM_CALL, "Start", done=started)
        self.mm(modem, MM_VOICE, "CreateCall",
                GLib.Variant("(a{sv})", ({"number": GLib.Variant("s", number)},)), "(o)", created)


def main():
    session = os.environ.get("LUMA_IMS_BRIDGE_BUS") == "session"
    bus = Gio.bus_get_sync(Gio.BusType.SESSION if session else Gio.BusType.SYSTEM, None)
    loop = GLib.MainLoop()
    Bridge(bus)
    status = []

    def lost(*_):
        log(f"lost {NAME}, exiting")
        status.append(1)
        loop.quit()
    Gio.bus_own_name_on_connection(bus, NAME, Gio.BusNameOwnerFlags.NONE,
                                   lambda *_: log(f"owning {NAME}"), lost)
    for number in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, lambda: loop.quit() or False)
    loop.run()
    return status[0] if status else 0


if __name__ == "__main__":
    sys.exit(main())
