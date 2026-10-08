#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""GSMA RCC.14 RCS autoconfiguration client for the Fairphone 6.

Stage 0 of native RCS: ask the carrier's Autoconfiguration Server (ACS) whether
this subscriber is provisioned for RCS, and if so retrieve the configuration
document that supplies every parameter the rest of the stack needs -- the RCS
profile version, the chat/file-transfer feature set, the MSRP and HTTP content
server URIs, and the IMS parameters RCS registers with.

Feasibility rests on two facts established before this was written:

  * AT&T operates a standards ACS at config.rcs.mnc<MNC>.mcc<MCC>.
    pub.3gppnetwork.org (TLS subject: AT&T Services, CN=xdmar.wireless.att.com),
    so RCS on this carrier is GSMA Universal Profile over IMS rather than a
    closed vendor service with no client entry point.
  * Luma already holds a working protected IMS registration on this line, which
    is the hard prerequisite RCS builds on.

Privacy model, matching the rest of the FP6 work: subscriber identity is read
from ModemManager in-process and placed only in the HTTPS query to the
subscriber's own carrier. It is never printed, never written to the journal,
and never passed as a process argument -- the request URL is handed to curl
through a config file on stdin, so it cannot appear in `ps`. Only status,
structure and counts reach stdout. The retrieved configuration is written
root-only.

Exit codes:
  0  configuration retrieved
  2  ACS reachable but this subscriber is not provisioned for RCS
  3  ACS requires an OTP that could not be read from the modem
  4  ACS unreachable / network failure
  5  precondition failure (not root, no modem, no SIM)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

STATE_DIR = Path("/var/lib/luma/rcs")
CONFIG_PATH = STATE_DIR / "rcs-config.xml"

# RCC.14 2.3. client_vendor is a 4-character vendor code and client_version is
# limited to 15 characters. These are deliberately truthful: a carrier ACS may
# refuse an unrecognised client, and that refusal is itself the result we want
# from Stage 0. Do not substitute another vendor's identifiers to get past it.
CLIENT_VENDOR = "LUMA"
CLIENT_VERSION = "LUMA-1.0"
RCS_VERSION = "UP2.4"
RCS_PROFILE = "UP_2.4"
TERMINAL_VENDOR = "Fairphone"
TERMINAL_MODEL = "FP6"


def log(message: str) -> None:
    print(f"rcs-provision: {message}", flush=True)


def run(argv: list[str], timeout: int = 25) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout


def keyvalues(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values


def suffix(values: dict[str, str], ending: str) -> str:
    return next((v for k, v in values.items() if k.endswith(ending)), "")


class Identity:
    """Subscriber and terminal identity. Never logged, never in argv."""

    def __init__(self) -> None:
        modem = keyvalues(run(["mmcli", "-m", "any", "-K"]))
        sim_path = suffix(modem, ".generic.sim")
        if not sim_path or sim_path == "--":
            raise RuntimeError("no SIM object")
        sim = keyvalues(run(["mmcli", "-i", sim_path, "-K"]))

        self.imsi = suffix(sim, ".properties.imsi")
        self.iccid = suffix(sim, ".properties.iccid")
        self.operator = suffix(sim, ".properties.operator-code")
        self.imei = suffix(modem, ".3gpp.imei")
        own = suffix(modem, ".generic.own-numbers.value[1]")
        self.msisdn = own if own and own != "--" else ""

        if not self.imsi or self.imsi == "--":
            raise RuntimeError("SIM did not expose an IMSI")
        if not self.operator or self.operator == "--":
            self.operator = self.imsi[:6]

    @property
    def mcc(self) -> str:
        return self.operator[:3]

    @property
    def mnc(self) -> str:
        return self.operator[3:]

    def describe(self) -> str:
        """Structure only -- deliberately reveals no identifier values."""
        return (
            f"MCC/MNC width {len(self.mcc)}/{len(self.mnc)}, "
            f"IMSI {len(self.imsi)} digits, "
            f"MSISDN {'known' if self.msisdn else 'not exposed by ModemManager'}, "
            f"IMEI {'known' if self.imei and self.imei != '--' else 'not exposed'}"
        )


def acs_host(identity: Identity) -> str:
    return (
        f"config.rcs.mnc{identity.mnc.zfill(3)}"
        f".mcc{identity.mcc.zfill(3)}.pub.3gppnetwork.org"
    )


def build_query(identity: Identity, version: str, otp: str | None) -> dict[str, str]:
    """RCC.14 2.3 configuration request parameters."""
    query = {
        "vers": version,
        "IMSI": identity.imsi,
        "terminal_vendor": TERMINAL_VENDOR,
        "terminal_model": TERMINAL_MODEL,
        "terminal_sw_version": os.uname().release[:15],
        "client_vendor": CLIENT_VENDOR,
        "client_version": CLIENT_VERSION,
        "rcs_version": RCS_VERSION,
        "rcs_profile": RCS_PROFILE,
        "provisioning_version": version,
        "default_sms_app": "1",
    }
    if identity.msisdn:
        query["msisdn"] = identity.msisdn
    if identity.imei and identity.imei != "--":
        query["IMEI"] = identity.imei
    if otp:
        query["OTP"] = otp
    return query


def fetch(url: str, interface: str | None, timeout: int,
          resolve: str | None = None) -> tuple[int, bytes]:
    """GET via curl, with the URL passed on stdin so it never reaches argv."""
    body = STATE_DIR / ".acs-body"
    argv = [
        "curl", "--silent", "--show-error", "--config", "-",
        "--max-time", str(timeout),
        "--output", str(body),
        "--write-out", "%{http_code}",
    ]
    if interface:
        argv[3:3] = ["--interface", interface]
    if resolve:
        # Pin the ACS address when the local resolver is unusable. Mobile
        # networks and captive Wi-Fi both break DNS often enough that
        # provisioning should not be hostage to it.
        argv[3:3] = ["--resolve", resolve]
    config = f'url = "{url}"\n'
    try:
        done = subprocess.run(
            argv, input=config, capture_output=True, text=True, timeout=timeout + 10
        )
    except (OSError, subprocess.SubprocessError):
        return 0, b""
    code = 0
    match = re.search(r"(\d{3})\s*$", done.stdout or "")
    if match:
        code = int(match.group(1))
    payload = body.read_bytes() if body.exists() else b""
    body.unlink(missing_ok=True)
    return code, payload


def read_otp_from_modem(deadline: float) -> str | None:
    """Read the ACS validation code from an inbound SMS.

    A real RCS client consumes this SMS silently; asking a human to relay the
    code would be both worse UX and worse practice. Chatty and mmsd also
    consume inbound SMS, so this races them -- hence the tight poll.
    """
    seen: set[str] = set()
    pattern = re.compile(r"\b(\d{4,8})\b")
    while time.time() < deadline:
        listing = run(["mmcli", "-m", "any", "--messaging-list-sms"], timeout=15)
        for ident in re.findall(r"/SMS/(\d+)", listing):
            if ident in seen:
                continue
            seen.add(ident)
            detail = keyvalues(run(["mmcli", "-s", ident, "--output-keyvalue"], timeout=15))
            state = suffix(detail, ".state").casefold()
            if state not in {"received", "receiving"}:
                continue
            text = suffix(detail, ".text")
            match = pattern.search(text)
            if match:
                return match.group(1)
        time.sleep(3)
    return None


def summarise(document: bytes) -> tuple[str, dict[str, str]]:
    """Classify the ACS response without echoing subscriber data."""
    try:
        root = ET.fromstring(document.decode("utf-8", "replace"))
    except ET.ParseError:
        return "unparsable", {}
    found: dict[str, str] = {}
    for parm in root.iter():
        name = parm.get("name")
        value = parm.get("value")
        if not name:
            continue
        if name.lower() in {
            "version", "validity", "rcsprofile", "provisioningstatus",
            "messagingui", "chatauth", "ftauth", "grouptchatauth",
            "standalonemsgauth", "geolocpushauth",
        }:
            found[name] = value or ""
    kinds = {c.get("type", "") for c in root.iter() if c.get("type")}
    kind = "characteristics: " + ", ".join(sorted(k for k in kinds if k)) if kinds else "no characteristics"
    return kind, found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default=None,
                        help="egress interface (RCC.14 prefers the mobile bearer)")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--otp-wait", type=int, default=90,
                        help="seconds to wait for the ACS validation SMS")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--acs-ip", default=None,
                        help="pin the ACS to this address when DNS is unusable")
    parser.add_argument("--imei", default=None,
                        help="device IMEI, when ModemManager cannot read it. Carriers "
                             "resolve device capability from the IMEI TAC against their "
                             "device database, so omitting it can itself cause a refusal. "
                             "Supply the device's OWN IMEI only -- never another device's.")
    parser.add_argument("--msisdn", default=None,
                        help="subscriber number in E.164, when ModemManager cannot "
                             "read it from the SIM (no MSISDN provisioned). RCC.14 "
                             "enrolment commonly keys on the number, so its absence "
                             "can itself produce a refusal.")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("must run as root (reads SIM identity, writes root-only state)")
        return 5

    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)

    try:
        identity = Identity()
    except RuntimeError as error:
        log(f"precondition failed: {error}")
        return 5

    if args.imei:
        identity.imei = args.imei
        log("IMEI supplied on the command line (ModemManager reports none)")
    if args.msisdn:
        identity.msisdn = args.msisdn
        log("MSISDN supplied on the command line (SIM does not carry one)")
    log(f"identity acquired ({identity.describe()})")
    host = acs_host(identity)
    log(f"ACS host {host}")

    version = "0"
    otp: str | None = None

    for attempt in range(1, args.retries + 1):
        query = build_query(identity, version, otp)
        url = f"https://{host}/?{urllib.parse.urlencode(query)}"
        resolve = f"{host}:443:{args.acs_ip}" if args.acs_ip else None
        code, document = fetch(url, args.interface, args.timeout, resolve)
        log(f"attempt {attempt}: HTTP {code or 'no-response'}, {len(document)} bytes")

        if code == 0:
            time.sleep(3)
            continue

        if code == 403:
            log("ACS refused: this subscriber or client is not provisioned for RCS")
            return 2

        if code == 511:
            log("ACS requires network authentication (511) -- retry over the mobile bearer")
            return 2

        if code == 200 and document:
            kind, parms = summarise(document)
            log(f"response parsed: {kind}")
            for key, value in sorted(parms.items()):
                log(f"  {key} = {value}")
            CONFIG_PATH.write_bytes(document)
            os.chmod(CONFIG_PATH, 0o600)
            log(f"response stored root-only at {CONFIG_PATH}")

            # RCC.14 2.7: the VERS characteristic carries the verdict. A
            # negative version is not a configuration -- it is the ACS telling
            # the client that RCS is switched off for this subscriber, and
            # (with validity -1) that it must not keep asking. Treating that
            # as success would make every later stage chase a service the
            # network has already declined to provide.
            version_parm = parms.get("version", parms.get("Version", ""))
            try:
                numeric = int(version_parm)
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None and numeric < 0:
                log(f"ACS verdict: RCS is DISABLED for this subscriber (version={numeric})")
                log("this is an account/provisioning state, not a client defect:")
                log("  the ACS accepted the RCC.14 request and identified the line")
                if parms.get("validity") == "-1":
                    log("  validity=-1 -- permanent until a provisioning trigger (e.g. SIM change)")
                return 2
            if numeric == 0:
                log("ACS returned version=0: no new configuration available")
                return 2
            log("configuration retrieved")
            return 0

        if code == 200 and not document:
            # RCC.14: an empty 200 means the ACS has dispatched a validation
            # SMS and expects the same request again carrying OTP=.
            if otp is not None:
                log("ACS returned an empty 200 even with an OTP; giving up")
                return 2
            log("ACS dispatched a validation SMS; reading it from the modem")
            otp = read_otp_from_modem(time.time() + args.otp_wait)
            if not otp:
                log("no validation SMS observed (Chatty/mmsd may have consumed it first)")
                return 3
            log("validation code read from modem; repeating request")
            continue

        log(f"unexpected ACS status {code}")
        time.sleep(3)

    log("ACS unreachable or did not complete provisioning")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
