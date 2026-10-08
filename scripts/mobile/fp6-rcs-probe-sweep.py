#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Sweep the RCS provisioning parameters we are legitimately free to choose.

The carrier's ACS answers version=-1 and its TS.43 entitlement server answers
"Not match Client Authentication Config." Before concluding that the network
allowlists devices, it is worth eliminating the parameters that are genuinely
our own implementation choices rather than claims about the hardware:

  * rcs_version / rcs_profile -- which GSMA profile OUR client implements.
    Carriers commonly serve exactly one profile generation and reject the rest.
    Declaring UP 2.4 was an arbitrary starting guess with no evidence behind it.
  * entitlement_version / app -- the TS.43 revision and service being asked
    about. A server that speaks an older revision can reject a newer one.
  * The egress bearer. Some carriers require entitlement and configuration
    requests to arrive over a particular APN.

This sweep deliberately does NOT vary terminal_vendor, terminal_model or the
IMEI. Those describe the hardware, and claiming to be another vendor's device
would be misrepresentation to the carrier, fragile (a claimed model that
disagrees with the IMEI TAC is trivially detectable), and not something a
shipping product can rest on.

Requests are paced, and the total is small, so this stays well clear of
hammering a production carrier endpoint.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

STATE_DIR = Path("/var/lib/luma/rcs")

# (rcs_version, rcs_profile) pairs seen in real deployments, oldest to newest.
RCS_SHAPES = [
    ("5.1B", "joyn_blackbird"),
    ("6.0", "UP_1.0"),
    ("7.0", "UP_2.0"),
    ("8.0", "UP_2.3"),
    ("9.0", "UP_2.4"),
    ("UP2.4", "UP_2.4"),
]

ENTITLEMENT_VERSIONS = ["0.9", "1.0", "2.0", "8.0"]
TS43_APPS = ["ap2003", "ap2004", "ap2005", "ap2009"]


def log(message: str) -> None:
    print(message, flush=True)


def run(argv: list[str], timeout: int = 25) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def keyvalues(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip()] = v.strip()
    return out


def suffix(values: dict[str, str], ending: str) -> str:
    return next((v for k, v in values.items() if k.endswith(ending)), "")


def fetch(url: str, interface: str | None, resolve: str | None,
          timeout: int) -> tuple[int, bytes]:
    body = STATE_DIR / ".sweep-body"
    argv = ["curl", "--silent", "--config", "-", "--max-time", str(timeout),
            "--output", str(body), "--write-out", "%{http_code}"]
    if interface:
        argv[2:2] = ["--interface", interface]
    if resolve:
        argv[2:2] = ["--resolve", resolve]
    try:
        done = subprocess.run(argv, input=f'url = "{url}"\n', capture_output=True,
                              text=True, timeout=timeout + 10)
    except (OSError, subprocess.SubprocessError):
        return 0, b""
    m = re.search(r"(\d{3})\s*$", done.stdout or "")
    code = int(m.group(1)) if m else 0
    payload = body.read_bytes() if body.exists() else b""
    body.unlink(missing_ok=True)
    return code, payload


def acs_verdict(document: bytes) -> str:
    try:
        root = ET.fromstring(document.decode("utf-8", "replace"))
    except ET.ParseError:
        return f"unparsable ({len(document)}B)"
    version = validity = None
    kinds = set()
    for node in root.iter():
        if node.get("type"):
            kinds.add(node.get("type"))
        if node.get("name") == "version":
            version = node.get("value")
        if node.get("name") == "validity":
            validity = node.get("value")
    if version is None:
        return f"no VERS; characteristics: {','.join(sorted(kinds)) or 'none'}"
    if version and version.lstrip("-").isdigit() and int(version) > 0:
        return f"*** CONFIGURATION version={version} chars={','.join(sorted(kinds))} ***"
    return f"version={version} validity={validity}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default=None)
    parser.add_argument("--acs-ip", default=None)
    parser.add_argument("--aes-ip", default=None)
    parser.add_argument("--imei", default=None)
    parser.add_argument("--msisdn", default=None)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--pace", type=float, default=2.0)
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("must run as root")
        return 5
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    modem = keyvalues(run(["mmcli", "-m", "any", "-K"]))
    sim_path = suffix(modem, ".generic.sim")
    sim = keyvalues(run(["mmcli", "-i", sim_path, "-K"])) if sim_path not in ("", "--") else {}
    imsi = suffix(sim, ".properties.imsi")
    operator = suffix(sim, ".properties.operator-code") or imsi[:6]
    if not imsi or imsi == "--":
        log("no IMSI available")
        return 5
    mcc, mnc = operator[:3], operator[3:]

    acs = f"config.rcs.mnc{mnc.zfill(3)}.mcc{mcc.zfill(3)}.pub.3gppnetwork.org"
    aes = f"aes.mnc{mnc.zfill(3)}.mcc{mcc.zfill(3)}.pub.3gppnetwork.org"
    acs_resolve = f"{acs}:443:{args.acs_ip}" if args.acs_ip else None
    aes_resolve = f"{aes}:443:{args.aes_ip}" if args.aes_ip else None

    base = {
        "terminal_vendor": "Fairphone",
        "terminal_model": "FP6",
        "terminal_sw_version": os.uname().release[:15],
    }
    if args.imei:
        base["IMEI"] = args.imei
    if args.msisdn:
        base["msisdn"] = args.msisdn

    log("")
    log("=== RCC.14 ACS: sweeping the profile WE claim to implement ===")
    for version, profile in RCS_SHAPES:
        query = dict(base)
        query.update({
            "vers": "0", "provisioning_version": "0", "IMSI": imsi,
            "client_vendor": "LUMA", "client_version": "LUMA-1.0",
            "rcs_version": version, "rcs_profile": profile,
            "default_sms_app": "1",
        })
        url = f"https://{acs}/?{urllib.parse.urlencode(query)}"
        code, payload = fetch(url, args.interface, acs_resolve, args.timeout)
        verdict = acs_verdict(payload) if code == 200 and payload else f"HTTP {code or 'none'}"
        log(f"  rcs_version={version:<6} rcs_profile={profile:<16} -> {verdict}")
        time.sleep(args.pace)

    log("")
    log("=== TS.43 entitlement: sweeping revision and service ===")
    eap_id = f"0{imsi}@nai.epc.mnc{mnc.zfill(3)}.mcc{mcc.zfill(3)}.3gppnetwork.org"
    for ent in ENTITLEMENT_VERSIONS:
        for app in TS43_APPS:
            query = dict(base)
            query.pop("IMEI", None)
            query.update({
                "app": app, "vers": "0", "entitlement_version": ent,
                "terminal_id": args.imei or "", "EAP_ID": eap_id,
            })
            url = f"https://{aes}/?{urllib.parse.urlencode(query)}"
            code, payload = fetch(url, args.interface, aes_resolve, args.timeout)
            body = payload.decode("utf-8", "replace").strip()[:60].replace("\n", " ")
            marker = " ***" if code == 200 else ""
            log(f"  entitlement_version={ent:<4} app={app} -> HTTP {code or 'none'} {body}{marker}")
            time.sleep(args.pace)

    log("")
    log("A line marked *** is a response worth pursuing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
