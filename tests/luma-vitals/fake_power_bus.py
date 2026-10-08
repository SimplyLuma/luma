#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""A fake UPower, power-profiles service and logind on a private bus.

Just enough of each for luma-vitals-power-guard: UPower's OnBattery, the
profiles API under both of its names with one shared state (as tuned-ppd and
power-profiles-daemon serve it), and logind's PrepareForSleep. Test-only
methods live on org.luma.Test:

  SetOnBattery(b, quiet)  change the power source; quiet skips the signal, as
                          when it changed while the machine was asleep
  Resume()                emit PrepareForSleep(false)
  Hold(s) / Release()     an application holding a profile
  Sets() -> a(ss)         every ActiveProfile write so far: (sender, profile)
"""
import sys
import warnings

from gi.repository import Gio, GLib

# register_object's closure form is the one every PyGObject on Fedora 44 has.
warnings.filterwarnings("ignore", category=DeprecationWarning)

XML = """
<node>
  <interface name="org.freedesktop.UPower"><property name="OnBattery" type="b" access="read"/></interface>
  <interface name="%s">
    <property name="ActiveProfile" type="s" access="readwrite"/>
    <property name="ActiveProfileHolds" type="aa{sv}" access="read"/>
    <property name="Profiles" type="aa{sv}" access="read"/>
  </interface>
  <interface name="org.freedesktop.login1.Manager"><signal name="PrepareForSleep"><arg type="b"/></signal></interface>
  <interface name="org.luma.Test">
    <method name="SetOnBattery"><arg type="b" direction="in"/><arg type="b" direction="in"/></method>
    <method name="Resume"/>
    <method name="Hold"><arg type="s" direction="in"/></method>
    <method name="Release"/>
    <method name="Sets"><arg type="a(ss)" direction="out"/></method>
  </interface>
</node>
"""
PROFILE_OBJECTS = (("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
                   ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"))
PROPS = "org.freedesktop.DBus.Properties"


class Fake:
    def __init__(self, conn, on_battery, profile):
        self.conn, self.on_battery, self.profile = conn, on_battery, profile
        self.base = profile
        self.hold = None
        self.sets = []

    def holds(self):
        if not self.hold:
            return GLib.Variant("aa{sv}", [])
        return GLib.Variant("aa{sv}", [{"Profile": GLib.Variant("s", self.hold),
                                        "Reason": GLib.Variant("s", "test"),
                                        "ApplicationId": GLib.Variant("s", "org.luma.Test")}])

    def active(self):
        return self.hold or self.profile

    def emit_profile(self, *names):
        changed = {}
        for name in names:
            changed[name] = GLib.Variant("s", self.active()) if name == "ActiveProfile" else self.holds()
        for iface, path in PROFILE_OBJECTS:
            self.conn.emit_signal(None, path, PROPS, "PropertiesChanged",
                                  GLib.Variant("(sa{sv}as)", (iface, changed, [])))

    def get(self, _c, _s, _p, iface, prop):
        if prop == "OnBattery":
            return GLib.Variant("b", self.on_battery)
        if prop == "ActiveProfile":
            return GLib.Variant("s", self.active())
        if prop == "ActiveProfileHolds":
            return self.holds()
        if prop == "Profiles":
            return GLib.Variant("aa{sv}", [{"Profile": GLib.Variant("s", p), "Driver": GLib.Variant("s", "fake")}
                                           for p in ("power-saver", "balanced", "performance")])
        return None

    def set(self, _c, sender, _p, _iface, prop, value):
        if prop != "ActiveProfile":
            return False
        profile = value.unpack()
        self.sets.append((sender, profile))
        before = self.active()
        self.hold = None  # setting a profile cancels holds, as the real services do
        self.profile = profile
        if before != self.active():
            self.emit_profile("ActiveProfile", "ActiveProfileHolds")
        return True

    def call(self, _c, _s, _p, _i, method, params, invocation):
        if method == "SetOnBattery":
            value, quiet = params.unpack()
            self.on_battery = value
            if not quiet:
                self.conn.emit_signal(None, "/org/freedesktop/UPower", PROPS, "PropertiesChanged",
                                      GLib.Variant("(sa{sv}as)", ("org.freedesktop.UPower",
                                                                  {"OnBattery": GLib.Variant("b", value)}, [])))
        elif method == "Resume":
            self.conn.emit_signal(None, "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                                  "PrepareForSleep", GLib.Variant("(b)", (False,)))
        elif method == "Hold":
            self.hold = params.unpack()[0]
            self.emit_profile("ActiveProfile", "ActiveProfileHolds")
        elif method == "Release":
            self.hold = None
            self.emit_profile("ActiveProfile", "ActiveProfileHolds")
        elif method == "Sets":
            invocation.return_value(GLib.Variant("(a(ss))", (self.sets,)))
            return
        invocation.return_value(None)


def main():
    address, on_battery, profile = sys.argv[1], sys.argv[2] == "battery", sys.argv[3]
    conn = Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    fake = Fake(conn, on_battery, profile)
    registrations = [
        ("/org/freedesktop/UPower", "org.freedesktop.UPower", "org.freedesktop.UPower.PowerProfiles"),
        ("/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.UPower.PowerProfiles",
         "org.freedesktop.UPower.PowerProfiles"),
        ("/net/hadess/PowerProfiles", "net.hadess.PowerProfiles", "net.hadess.PowerProfiles"),
        ("/org/luma/Test", "org.luma.Test", "net.hadess.PowerProfiles"),
    ]
    for path, iface, profiles_iface in registrations:
        info = Gio.DBusNodeInfo.new_for_xml(XML % profiles_iface).lookup_interface(iface)
        conn.register_object(path, info, fake.call, fake.get, fake.set)
    for name in ("org.freedesktop.UPower", "org.freedesktop.UPower.PowerProfiles", "net.hadess.PowerProfiles",
                 "org.freedesktop.login1", "org.luma.Test"):
        reply = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "RequestName", GLib.Variant("(su)", (name, 4)), GLib.VariantType("(u)"),
                               Gio.DBusCallFlags.NONE, 5000, None)
        if reply.unpack()[0] != 1:
            raise SystemExit(f"could not own {name}")
    print("ready", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
