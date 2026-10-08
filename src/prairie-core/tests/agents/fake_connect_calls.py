#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A stand-in org.projectluma.Connect1 for Phone's call provider, for agent tests only.

Answers GetCallState and ExchangeCall the way the Connect daemon relays a
paired phone (luma_continuity.call_provider's relay path), keeps the calls a
test sets with org.projectluma.Test.Calls.SetCalls(s json) and emits
CallsChanged, and records every control request in $LUMA_TEST_CALLS_LOG.
"""
import json
import os

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = "org.projectluma.Connect1"
PATH = "/org/projectluma/Connect"
LOG = os.environ.get("LUMA_TEST_CALLS_LOG", "/tmp/calls.jsonl")
ACCOUNT, EPOCH = os.environ["LUMA_TEST_ACCOUNT"], os.environ["LUMA_TEST_EPOCH"]
XML = f"""<node>
<interface name="{BUS}">
  <method name="GetCallState"><arg type="s" direction="out"/></method>
  <method name="ExchangeCall"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="StartCallAudio"><arg type="s" direction="in"/></method>
  <method name="StopCallAudio"/>
  <method name="SetCallAudioMuted"><arg type="b" direction="in"/></method>
  <method name="GetQuickState"><arg type="s" direction="out"/></method>
  <signal name="CallsChanged"/>
</interface>
<interface name="org.projectluma.Test.Calls">
  <method name="SetCalls"><arg type="s" direction="in"/></method>
</interface>
</node>"""
calls = []


def record(entry):
    with open(LOG, "a") as stream:
        stream.write(json.dumps(entry) + "\n")


def call(connection, sender, path, interface, method, parameters, invocation):
    global calls
    if method == "GetCallState":
        invocation.return_value(GLib.Variant("(s)", (json.dumps(
            {"account": ACCOUNT, "epoch": EPOCH, "connected": True, "control": True,
             "audio": {"status": "phone", "muted": False}}),)))
    elif method == "GetQuickState":
        invocation.return_value(GLib.Variant("(s)", ("{}",)))
    elif method == "ExchangeCall":
        request = json.loads(parameters.unpack()[0])
        payload = request.get("payload") or {}
        if request.get("capability") == "calls.read":
            result = {"calls": calls, "dial_token": None, "voice_available": True}
        else:
            record({"control": payload.get("operation"), "call": payload.get("call")})
            operation = payload.get("operation")
            for row in calls:
                if row["id"] == payload.get("call"):
                    if operation == "answer":
                        row["phase"], row["answered_at"] = "active", 1
                    elif operation in {"decline", "hangup"}:
                        row["phase"] = "ended"
            result = {"accepted": True}
            GLib.idle_add(lambda: (connection.emit_signal(None, PATH, BUS, "CallsChanged", None), False)[1])
        invocation.return_value(GLib.Variant("(s)", (json.dumps({"state": "complete", "result": result}),)))
    elif method in {"StartCallAudio", "StopCallAudio", "SetCallAudioMuted"}:
        invocation.return_value(None)
    elif method == "SetCalls":
        calls = json.loads(parameters.unpack()[0])
        record({"set": calls})
        connection.emit_signal(None, PATH, BUS, "CallsChanged", None)
        invocation.return_value(None)


connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
node = Gio.DBusNodeInfo.new_for_xml(XML)
for interface in node.interfaces:
    connection.register_object(PATH, interface, call, None, None)
owner = Gio.bus_own_name_on_connection(connection, BUS, Gio.BusNameOwnerFlags.NONE, None, None)
print("fake Connect1 ready", flush=True)
GLib.MainLoop().run()
