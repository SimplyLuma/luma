#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Registration/session/challenge probe for the isolated QREL fingerprint HAL.

This client never enrolls, authenticates, deletes, or persists biometric data.
It exists to prove the stock AIDL session/callback boundary before the separate
root-only enrollment broker is allowed to accept a PIN or a finger touch.
"""

import signal
import sys

import gbinder
from gi.repository import GLib


FINGERPRINT_SERVICE = (
    "android.hardware.biometrics.fingerprint.IFingerprint/default"
)
FINGERPRINT_INTERFACE = "android.hardware.biometrics.fingerprint.IFingerprint"
SESSION_INTERFACE = "android.hardware.biometrics.fingerprint.ISession"
CALLBACK_INTERFACE = "android.hardware.biometrics.fingerprint.ISessionCallback"

TRANSACTION_CREATE_SESSION = 2
TRANSACTION_GENERATE_CHALLENGE = 1
CALLBACK_CHALLENGE_GENERATED = 1
CALLBACK_ERROR = 4
TRANSACTION_GET_INTERFACE_HASH = 16777214
TRANSACTION_GET_INTERFACE_VERSION = 16777215


def read_status(reader, operation):
    ok, exception = reader.read_int32()
    if not ok:
        raise RuntimeError(f"{operation}: missing Binder status")
    if exception != 0:
        raise RuntimeError(f"{operation}: Binder exception {exception}")


def main():
    loop = GLib.MainLoop()
    state = {"challenge": None, "error": None}

    manager = gbinder.ServiceManager("/dev/binder", "aidl3", "aidl3")
    if not manager.is_present():
        raise RuntimeError("isolated service manager is not present")
    remote, status = manager.get_service_sync(FINGERPRINT_SERVICE)
    if status or remote is None:
        raise RuntimeError(f"fingerprint service unavailable: {status}")

    callback = None

    def callback_handler(request, code, flags):
        del flags
        reader = request.init_reader()
        reply = callback.new_reply()
        if code == CALLBACK_CHALLENGE_GENERATED:
            ok, challenge = reader.read_int64()
            if not ok:
                state["error"] = "malformed challenge callback"
            else:
                state["challenge"] = challenge
            reply.append_int32(0)
            loop.quit()
            return reply, 0
        if code == CALLBACK_ERROR:
            ok_error, error = reader.read_int32()
            ok_vendor, vendor = reader.read_int32()
            state["error"] = (
                f"HAL error {error if ok_error else 'malformed'} "
                f"vendor {vendor if ok_vendor else 'malformed'}"
            )
            reply.append_int32(0)
            loop.quit()
            return reply, 0
        if code == TRANSACTION_GET_INTERFACE_VERSION:
            reply.append_int32(0)
            reply.append_int32(3)
            return reply, 0
        if code == TRANSACTION_GET_INTERFACE_HASH:
            reply.append_int32(0)
            reply.append_string8("")
            return reply, 0
        reply.append_int32(0)
        return reply, 0

    callback = manager.new_local_object(CALLBACK_INTERFACE, callback_handler)

    fingerprint = gbinder.Client(remote, FINGERPRINT_INTERFACE)
    request = fingerprint.new_request()
    request.append_int32(0)  # stock FP6 sensor id
    request.append_int32(0)  # primary Linux user mapping
    request.append_local_object(callback)
    reply, status = fingerprint.transact_sync_reply(
        TRANSACTION_CREATE_SESSION, request
    )
    if status or reply is None:
        raise RuntimeError(f"createSession transport failure: {status}")
    reader = reply.init_reader()
    read_status(reader, "createSession")
    session_remote = reader.read_object()
    if session_remote is None:
        raise RuntimeError("createSession returned no session")

    session = gbinder.Client(session_remote, SESSION_INTERFACE)
    reply, status = session.transact_sync_reply(
        TRANSACTION_GENERATE_CHALLENGE, session.new_request()
    )
    if status or reply is None:
        raise RuntimeError(f"generateChallenge transport failure: {status}")
    read_status(reply.init_reader(), "generateChallenge")

    GLib.timeout_add_seconds(10, lambda: loop.quit())
    GLib.unix_signal_add(
        GLib.PRIORITY_HIGH, signal.SIGTERM, lambda *_: loop.quit()
    )
    loop.run()
    if state["error"]:
        raise RuntimeError(state["error"])
    if state["challenge"] is None:
        raise RuntimeError("challenge callback timed out")

    print(
        "event=fp6_stock_fingerprint_aidl_probe_passed "
        "session=true challenge=true enrollment=false authentication=false "
        "raw_images=0 templates_written=0"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            f"event=fp6_stock_fingerprint_aidl_probe_failed reason={error}",
            file=sys.stderr,
        )
        raise SystemExit(1)
