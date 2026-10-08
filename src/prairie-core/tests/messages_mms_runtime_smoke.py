#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Run in a private dbus-run-session; the service below never contacts a modem."""
from pathlib import Path
import tempfile
import threading

from gi.repository import Gio, GLib
from prairie_apps.messages_mms import MmsMessagingTransport

XML = """<node>
<interface name="org.ofono.mms.Manager">
 <method name="GetServices"><arg type="a(oa{sv})" direction="out"/></method>
</interface>
<interface name="org.ofono.mms.Service">
 <method name="GetProperties"><arg type="a{sv}" direction="out"/></method>
 <method name="GetMessages"><arg type="a(oa{sv})" direction="out"/></method>
 <method name="SendMessage"><arg type="as" direction="in"/><arg type="v" direction="in"/>
 <arg type="a(sss)" direction="in"/><arg type="o" direction="out"/></method>
 <signal name="MessageAdded"><arg type="o"/><arg type="a{sv}"/></signal>
</interface>
<interface name="org.ofono.mms.Message">
 <signal name="PropertyChanged"><arg type="s"/><arg type="v"/></signal>
</interface></node>"""


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    service = "/org/ofono/mms/test"
    incoming = service + "/incoming"
    outgoing = service + "/outgoing"
    events = []
    sent = []
    errors = []
    loop = GLib.MainLoop()
    transport = MmsMessagingTransport()

    def properties(status):
        return {"Status": GLib.Variant("s", status), "Sender": GLib.Variant("s", "+12025550100")}

    def method(_bus, _sender, path, interface, name, args, invocation):
        if name == "GetServices":
            invocation.return_value(GLib.Variant("(a(oa{sv}))", ([(service, {})],)))
        elif name == "GetProperties":
            invocation.return_value(GLib.Variant("(a{sv})", ({
                "TotalMaxAttachmentSize": GLib.Variant("i", 1000000),
                "MaxAttachments": GLib.Variant("i", 5),
            },)))
        elif name == "GetMessages":
            # A newer status arrives before a stale history reply. It must
            # survive discovery rather than being overwritten by the reply.
            bus.emit_signal(None, incoming, "org.ofono.mms.Message", "PropertyChanged",
                            GLib.Variant("(sv)", ("Status", GLib.Variant("s", "read"))))
            invocation.return_value(GLib.Variant("(a(oa{sv}))", ([(incoming, properties("received"))],)))
        elif name == "SendMessage":
            sent.append(args.unpack())
            bus.emit_signal(None, service, "org.ofono.mms.Service", "MessageAdded",
                            GLib.Variant("(oa{sv})", (outgoing, properties("draft"))))
            invocation.return_value(GLib.Variant("(o)", (outgoing,)))
            def delivered():
                bus.emit_signal(None, outgoing, "org.ofono.mms.Message", "PropertyChanged",
                                GLib.Variant("(sv)", ("Status", GLib.Variant("s", "sent"))))
                return False
            GLib.timeout_add(200, delivered)

    registrations = [
        bus.register_object(transport.MANAGER, node.interfaces[0], method, None, None),
        bus.register_object(service, node.interfaces[1], method, None, None),
    ]
    owner = Gio.bus_own_name_on_connection(bus, transport.NAME, Gio.BusNameOwnerFlags.NONE, None, None)
    with tempfile.TemporaryDirectory() as temporary:
        part = Path(temporary) / "sample.txt"
        part.write_text("fixture", encoding="utf-8")
        started = False

        def capability_ready(capability):
            nonlocal started
            if capability.available and not started:
                started = True
                def send():
                    try:
                        assert transport.send(("+12025550100",), (("sample.txt", "text/plain", str(part)),)) == outgoing
                    except Exception as error:
                        errors.append(str(error))
                        GLib.idle_add(loop.quit)
                threading.Thread(target=send, daemon=True).start()

        def event(path, values):
            events.append((path, values["Status"]))
            if path == outgoing and values["Status"] == "sent":
                loop.quit()

        transport.start(event, capability_ready)
        def timeout():
            errors.append("Timed out waiting for native service signals")
            loop.quit()
            return False
        timer = GLib.timeout_add_seconds(8, timeout)
        loop.run()
        GLib.source_remove(timer)
        transport.stop()
    Gio.bus_unown_name(owner)
    for registration in registrations:
        bus.unregister_object(registration)
    assert not errors, errors
    assert events == [(incoming, "read"), (outgoing, "draft"), (outgoing, "sent")], events
    assert sent[0][0] == ["+12025550100"]
    assert sent[0][1] == {}
    assert sent[0][2][0][:2] == ("sample.txt", "text/plain")
    print("messages-mms-runtime: discovery, exact SendMessage ABI, queue and sent signals PASS")


if __name__ == "__main__":
    main()
