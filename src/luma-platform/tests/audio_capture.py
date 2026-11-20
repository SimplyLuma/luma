#!/usr/bin/env python3
"""luma_appkit.audio_capture against a stand-in portal on a session bus.

Run under dbus-run-session. The stand-in owns org.freedesktop.portal.Desktop,
answers AudioCapture like xdg-desktop-portal does (Request objects and
Response signals), and hands out a pipe as the PipeWire remote. Checks: the
request flow and its options, the capture's fields and pipeline string, a
restore token passed back, a cancel reported as cancelled, and close()
closing the session and the descriptor.
"""

import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from luma_appkit import audio_capture  # noqa: E402

XML = """
<node>
  <interface name='org.freedesktop.portal.AudioCapture'>
    <method name='CreateSession'><arg type='a{sv}' direction='in'/><arg type='o' direction='out'/></method>
    <method name='SelectSources'><arg type='o' direction='in'/><arg type='a{sv}' direction='in'/><arg type='o' direction='out'/></method>
    <method name='Start'><arg type='o' direction='in'/><arg type='s' direction='in'/><arg type='a{sv}' direction='in'/><arg type='o' direction='out'/></method>
    <method name='OpenPipeWireRemote'><arg type='o' direction='in'/><arg type='a{sv}' direction='in'/><arg type='h' direction='out'/></method>
    <property name='version' type='u' access='read'/>
  </interface>
  <interface name='org.freedesktop.portal.Session'>
    <method name='Close'/>
  </interface>
</node>
"""


class Portal:
    def __init__(self, connection, cancel_start=False):
        self.connection = connection
        self.cancel_start = cancel_start
        self.calls = []
        self.closed = []
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        connection.register_object("/org/freedesktop/portal/desktop", node.interfaces[0],
                                   self.call, self.get, None)
        self.session_node = node.interfaces[1]

    def get(self, *_args):
        return GLib.Variant("u", 1)

    def respond(self, sender, token, response, results):
        path = "/org/freedesktop/portal/desktop/request/{}/{}".format(
            sender.lstrip(":").replace(".", "_"), token)

        def emit():
            self.connection.emit_signal(sender, path, "org.freedesktop.portal.Request", "Response",
                                        GLib.Variant("(ua{sv})", (response, results)))
            return False
        GLib.timeout_add(20, emit)
        return path

    def call(self, _c, sender, _path, _iface, method, parameters, invocation):
        args = parameters.unpack()
        self.calls.append((method, args))
        if method == "CreateSession":
            options = args[0]
            session = "/org/freedesktop/portal/desktop/session/{}/{}".format(
                sender.lstrip(":").replace(".", "_"), options["session_handle_token"])
            self.connection.register_object(session, self.session_node,
                                            lambda *a: (self.closed.append(session), a[-1].return_value(None)),
                                            None, None)
            path = self.respond(sender, options["handle_token"], 0,
                                {"session_handle": GLib.Variant("s", session)})
        elif method == "SelectSources":
            path = self.respond(sender, args[1]["handle_token"], 0, {})
        elif method == "Start":
            if self.cancel_start:
                path = self.respond(sender, args[2]["handle_token"], 1, {})
            else:
                stream = (61, {"source_type": GLib.Variant("u", 1),
                               "applications": GLib.Variant("as", ["Tone A", "Tone B"]),
                               "pipewire-serial": GLib.Variant("t", 4242)})
                path = self.respond(sender, args[2]["handle_token"], 0, {
                    "streams": GLib.Variant("a(ua{sv})", [stream]),
                    "restore_token": GLib.Variant("s", "token-2")})
        else:
            read, write = os.pipe()
            os.close(write)
            fds = Gio.UnixFDList.new()
            index = fds.append(read)
            os.close(read)
            invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (index,)), fds)
            return
        invocation.return_value(GLib.Variant("(o)", (path,)))


def run(cancel_start=False, **options):
    portal_bus = Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SESSION_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    portal = Portal(portal_bus, cancel_start)
    owned = []
    Gio.bus_own_name_on_connection(portal_bus, "org.freedesktop.portal.Desktop",
                                   Gio.BusNameOwnerFlags.NONE, lambda *_a: owned.append(1), None)
    context = GLib.MainContext.default()
    while not owned:
        context.iteration(True)
    app_bus = Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SESSION_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    result = {}
    audio_capture.request_audio_capture("wayland:abc", lambda c, e: result.update(capture=c, error=e),
                                        connection=app_bus, **options)
    deadline = GLib.get_monotonic_time() + 5_000_000
    while "error" not in result and GLib.get_monotonic_time() < deadline:
        context.iteration(True)
    portal.bus = portal_bus
    return portal, result, app_bus


def main():
    portal, result, _bus = run(multiple=False, restore_token="token-1")
    assert result.get("error") is None, result.get("error")
    capture = result["capture"]
    methods = [name for name, _args in portal.calls]
    assert methods == ["CreateSession", "SelectSources", "Start", "OpenPipeWireRemote"], methods
    select = portal.calls[1][1][1]
    assert select["types"] == 3 and select["multiple"] is False, select
    assert select["audio_mode"] == 2 and select["persist_mode"] == 2, select
    assert select["restore_token"] == "token-1", select
    assert portal.calls[2][1][1] == "wayland:abc"
    assert (capture.node_id, capture.serial, capture.target) == (61, 4242, "4242")
    assert capture.applications == ["Tone A", "Tone B"] and capture.description == "Tone A, Tone B"
    assert capture.restore_token == "token-2"
    assert capture.fd >= 0
    assert capture.pipeline_description() == \
        f"pipewiresrc fd={capture.fd} target-object=4242 do-timestamp=true", capture.pipeline_description()
    fd = capture.fd
    capture.close()
    context = GLib.MainContext.default()
    for _ in range(50):
        context.iteration(False)
    assert capture.fd == -1
    try:
        os.fstat(fd)
        raise AssertionError("close() left the remote open")
    except OSError:
        pass
    assert portal.closed == [capture.session_handle], portal.closed

    portal.bus.close_sync(None)
    portal, result, _bus = run(cancel_start=True, include_self=True, persist=False)
    assert result["capture"] is None and result["error"].cancelled
    select = portal.calls[1][1][1]
    assert select["audio_mode"] == 4 and select["persist_mode"] == 0 and "restore_token" not in select
    print("audio capture client: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
