#!/usr/bin/python3
"""MMS on the Pixel 9 Pro Fold, in the session beside mmsd-tng.

mmsd-tng looks its MMSC up by the bearer's APN, and the data APN here is not
in the provider database, so it keeps the placeholder settings; this sets the
MMSC from the database by the registered network and the APN of the data mode.

In calls mode there is no data PDN (the CP takes one PDN while IMS is up), so
mmsd-tng reports BearerHandlerError when a message needs fetching or sending.
A received binary SMS (the WAP push of an MMS notification) does the same, in
case mmsd-tng sees it before it looks for a bearer. This asks luma-fold-mobile-mode for an MMS window (data for a short while,
then calls again) and has mmsd-tng run its queue once the bearer is up.
"""
import os
import sys
import warnings
import xml.etree.ElementTree as ET

warnings.filterwarnings("ignore", category=DeprecationWarning)  # PyGObject API churn

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

MMS = "org.ofono.mms"
MMS_PATH = "/org/ofono/mms"
MMS_MM = "org.ofono.mms.ModemManager"
MODE = "org.projectluma.MobileMode1"
MODE_PATH = "/org/projectluma/MobileMode1"
MM = "org.freedesktop.ModemManager1"
PROVIDERS = "/usr/share/mobile-broadband-provider-info/serviceproviders.xml"
DATA_APN = os.environ.get("LUMA_FOLD_DATA_APN_FILE", "/run/luma-fold-mobile/data/apn")
PLACEHOLDERS = {"http://mms.invalid", "apn.invalid", ""}


def log(message):
    print(f"luma-fold-mms: {message}", flush=True)


def mmsc_for(operator, providers=PROVIDERS):
    """(mmsc, proxy) of the first provider entry for this MCC/MNC that has one."""
    mcc, mnc = operator[:3], operator[3:]
    try:
        root = ET.parse(providers).getroot()
    except (OSError, ET.ParseError):
        return None
    for provider in root.iter("provider"):
        gsm = provider.find("gsm")
        if gsm is None or not any(n.get("mcc") == mcc and n.get("mnc") == mnc
                                  for n in gsm.iter("network-id")):
            continue
        for apn in gsm.iter("apn"):
            mmsc = apn.findtext("mmsc")
            if mmsc:
                return mmsc.strip(), (apn.findtext("mmsproxy") or "NULL").strip()
    return None


class Helper:
    def __init__(self):
        self.session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.session.signal_subscribe(MMS, MMS_MM, "BearerHandlerError", None, None,
                                      Gio.DBusSignalFlags.NONE, self.on_bearer_error)
        self.session.signal_subscribe(MMS, MMS_MM, "SettingsChanged", None, None,
                                      Gio.DBusSignalFlags.NONE, lambda *_: self.provision())
        self.system.signal_subscribe(MM, MM + ".Modem.Messaging", "Added", None, None,
                                     Gio.DBusSignalFlags.NONE, self.on_sms)
        self.system.signal_subscribe(MODE, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                     MODE_PATH, None, Gio.DBusSignalFlags.NONE, self.on_mode)
        Gio.bus_watch_name_on_connection(self.session, MMS, Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self.provision(), None)

    def call(self, bus, name, path, iface, method, args=None, reply=None):
        try:
            return bus.call_sync(name, path, iface, method, args,
                                 GLib.VariantType(reply) if reply else None,
                                 Gio.DBusCallFlags.NONE, 30000, None)
        except GLib.Error as error:
            log(f"{method}: {error.message}")
            return None

    def operator(self):
        """The registered network's MCC/MNC, from ModemManager."""
        out = self.call(self.system, MM, "/org/freedesktop/ModemManager1",
                        "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None, "(a{oa{sa{sv}}})")
        for interfaces in (out.unpack()[0] if out else {}).values():
            code = interfaces.get(MM + ".Modem.Modem3gpp", {}).get("OperatorCode", "")
            if code:
                return code
        return ""

    def provision(self):
        out = self.call(self.session, MMS, MMS_PATH, MMS_MM, "ViewSettings", None, "(a{sv})")
        if not out:
            return
        settings = out.unpack()[0]
        apn = open(DATA_APN).read().strip() if os.path.exists(DATA_APN) else ""
        found = mmsc_for(self.operator())
        if not apn or not found:
            log("no data APN or MMSC for this network yet; trying again in 30 s")
            GLib.timeout_add_seconds(30, lambda: self.provision() and False)
            return
        mmsc, proxy = found
        want = {"CarrierMMSC": mmsc, "MMS_APN": apn, "CarrierMMSProxy": proxy}
        if all(settings.get(k) == v for k, v in want.items()):
            return
        if not (settings.get("CarrierMMSC") in PLACEHOLDERS or settings.get("MMS_APN") in PLACEHOLDERS
                or settings.get("MMS_APN") != apn):
            return  # set by hand; leave it
        args = GLib.Variant("(a{sv})", ({k: GLib.Variant("s", v) for k, v in want.items()},))
        if self.call(self.session, MMS, MMS_PATH, MMS_MM, "ChangeAllSettings", args):
            log(f"MMS settings set (APN {apn})")

    def on_sms(self, _c, _sender, _path, _iface, _signal, params):
        path, received = params.unpack()
        if not received:
            return
        out = self.call(self.system, MM, path, "org.freedesktop.DBus.Properties", "Get",
                        GLib.Variant("(ss)", (MM + ".Sms", "Data")), "(v)")
        if out and len(out.unpack()[0]) > 0:
            self.on_bearer_error()

    def on_bearer_error(self, *_):
        state = self.mode_state()
        if state == ("data", "ready") or state[1] == "mms":
            return
        log("mmsd needs a bearer; asking for an MMS window")
        self.call(self.system, MODE, MODE_PATH, MODE, "RequestMmsWindow")

    def mode_state(self):
        out = self.call(self.system, MODE, MODE_PATH, "org.freedesktop.DBus.Properties", "GetAll",
                        GLib.Variant("(s)", (MODE,)), "(a{sv})")
        props = out.unpack()[0] if out else {}
        return props.get("Mode", ""), props.get("State", "")

    def on_mode(self, _c, _sender, _path, _iface, _signal, params):
        changed = params.unpack()[1]
        if changed.get("State") == "mms" or (changed.get("Mode") == "data" and changed.get("State") == "ready"):
            log("bearer up; processing the MMS queue")
            self.call(self.session, MMS, MMS_PATH, MMS_MM, "ProcessMessageQueue")


def main():
    Helper()
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
