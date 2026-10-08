#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Install one FP6 eSIM profile without exposing bearer or profile secrets."""

import argparse
import ctypes
import json
import os
import re
import resource
import subprocess
import sys


def fail(reason: str) -> "NoReturn":
    print(f"ESIM_INSTALL=stopped:{reason}")
    raise SystemExit(1)


def payload(stdout: bytes):
    try:
        value = json.loads(stdout)
        result = value["payload"]
        if result.get("code") != 0:
            fail("lpac-rejected-operation")
        return result.get("data")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        fail("unexpected-lpac-response")


def run_lpac(
    binary: str,
    slot: int,
    arguments: list[str],
    input_data: bytes | None = None,
    parse_response: bool = True,
):
    environment = {
        **os.environ,
        "LPAC_APDU": "qmi_qrtr",
        "LPAC_APDU_QMI_UIM_SLOT": str(slot),
        "LPAC_HTTP": "curl",
        "LPAC_APDU_DEBUG": "false",
        "LPAC_HTTP_DEBUG": "false",
    }
    try:
        completed = subprocess.run(
            [binary, *arguments],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("lpac-timeout")
    if completed.returncode != 0:
        fail("lpac-client-failed")
    return payload(completed.stdout) if parse_response else None


def profiles(binary: str, slot: int) -> list[dict]:
    value = run_lpac(binary, slot, ["profile", "list"])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        fail("unexpected-profile-list")
    return value


def key(profile: dict) -> tuple[str, str]:
    return str(profile.get("iccid") or ""), str(profile.get("isdpAid") or "")


def selector(profile: dict) -> str:
    for field, pattern in (("iccid", r"[0-9]{19,20}"), ("isdpAid", r"[0-9A-Fa-f]{32}")):
        value = str(profile.get(field) or "")
        if re.fullmatch(pattern, value):
            return value
    fail("new-profile-has-no-safe-selector")


def clear(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lpac", required=True)
    parser.add_argument("--slot", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE
    except (AttributeError, OSError):
        pass

    activation = bytearray(sys.stdin.buffer.readline(4098))
    if activation.endswith(b"\n"):
        activation.pop()
    if activation.endswith(b"\r"):
        activation.pop()
    try:
        if not re.fullmatch(rb"LPA:1\$[^$\r\n]{1,512}\$[^$\r\n]{1,2048}(?:\$[^\r\n]{0,1024})?", activation):
            fail("invalid-activation-payload")

        before = profiles(args.lpac, args.slot)
        if len(before) < 1 or sum(p.get("profileState") == "enabled" for p in before) != 1:
            fail("unexpected-existing-profile-state")
        before_keys = {key(profile) for profile in before}
        if len(before_keys) != len(before):
            fail("non-unique-existing-profile-identity")

        # lpac's download applet emits a different success document than the
        # list applet. Treat only its process result as provisional; the exact
        # before/after profile-set comparison below is authoritative.
        run_lpac(
            args.lpac,
            args.slot,
            ["profile", "download", "-A"],
            bytes(activation) + b"\n",
            parse_response=False,
        )
        clear(activation)

        after_download = profiles(args.lpac, args.slot)
        added = [profile for profile in after_download if key(profile) not in before_keys]
        if len(after_download) != len(before) + 1 or len(added) != 1:
            fail("download-did-not-add-exactly-one-profile")

        new_profile = added[0]
        if new_profile.get("profileState") != "enabled":
            profile_id = bytearray(selector(new_profile).encode("ascii"))
            try:
                # Do not request an implicit modem refresh; the caller performs
                # one bounded refresh only after post-enable verification.
                run_lpac(args.lpac, args.slot, ["profile", "enable", "-", "0"], bytes(profile_id) + b"\n")
            finally:
                clear(profile_id)

        final = profiles(args.lpac, args.slot)
        final_new = [profile for profile in final if key(profile) not in before_keys]
        if len(final) != len(before) + 1 or len(final_new) != 1:
            fail("unexpected-final-profile-count")
        if final_new[0].get("profileState") != "enabled":
            fail("new-profile-not-enabled")
        if sum(profile.get("profileState") == "enabled" for profile in final) != 1:
            fail("unexpected-enabled-profile-count")

        print("ESIM_INSTALL=accepted")
        print(f"PROFILE_COUNT={len(final)}")
        print("ENABLED_PROFILE_COUNT=1")
        print("DISABLED_PROFILE_COUNT=1")
        print("IDENTIFIERS_REDACTED=true")
    finally:
        clear(activation)


if __name__ == "__main__":
    main()
