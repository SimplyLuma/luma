#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""GSMA TS.43 Service Entitlement client for the Fairphone 6.

Asks the carrier's entitlement server what this line is actually entitled to --
VoLTE, VoWiFi, SMSoIP, and where supported the messaging/RCS status -- and
captures any activation URL the server hands back for a user-facing flow.

Why this exists: the RCC.14 autoconfiguration server reports RCS as disabled for
this subscriber (see docs/research/fp6-rcs-feasibility.md), yet the carrier
publicly documents RCS as supported on this plan. TS.43 is the mechanism that
reports per-service entitlement and can return an activation path, so it is the
closest thing to the "turn it on" switch a handset performs -- and unlike the
Android chat-features toggle, which enrols with Google's proprietary Jibe
service, it is a standards interface a native client can implement.

Authentication is EAP-AKA against the SIM (TS.43 2.8.1, RFC 4187). The AKA
computation is delegated to the card over the same qmicli UIM logical channel
imsd uses for IMS registration, so no key material ever leaves the SIM: the card
returns RES/CK/IK, and only derived values are used.

Privacy model: subscriber identity is read in-process and placed only in the
HTTPS request to the subscriber's own carrier. Request URLs go to curl on stdin
so identity never reaches argv or the journal. Only status, structure and
service verdicts are printed; the raw response is stored root-only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import subprocess
import sys
import urllib.parse
from pathlib import Path

STATE_DIR = Path("/var/lib/luma/rcs")
RESPONSE_PATH = STATE_DIR / "ts43-response.json"
# TS.43 runs EAP-AKA across two HTTP round trips, and HTTP is stateless: the
# server correlates the challenge with the response through a session cookie.
# Without carrying it the server treats the second request as a brand-new EAP
# session and simply re-issues the same challenge.
COOKIE_JAR = STATE_DIR / ".ts43-cookies"

TERMINAL_VENDOR = "Fairphone"
TERMINAL_MODEL = "FP6"
ENTITLEMENT_VERSION = "0.9"

# TS.43 application identifiers.
APPS = {
    "ap2003": "VoWiFi",
    "ap2004": "VoLTE",
    "ap2005": "SMSoIP",
    "ap2006": "ODSA companion device",
    "ap2009": "ODSA primary device",
}


def log(message: str) -> None:
    print(f"ts43: {message}", flush=True)


def run(argv: list[str], timeout: int = 30) -> str:
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


# ---------------------------------------------------------------- SHA-1 PRF

def _sha1_compress(state: list[int], block: bytes) -> list[int]:
    """One raw SHA-1 compression. FIPS 186-2's PRF needs G(), not a full hash."""
    w = list(struct.unpack(">16I", block))
    for i in range(16, 80):
        v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]
        w.append(((v << 1) | (v >> 31)) & 0xFFFFFFFF)
    a, b, c, d, e = state
    for i in range(80):
        if i < 20:
            f, k = (b & c) | (~b & 0xFFFFFFFF & d), 0x5A827999
        elif i < 40:
            f, k = b ^ c ^ d, 0x6ED9EBA1
        elif i < 60:
            f, k = (b & c) | (b & d) | (c & d), 0x8F1BBCDC
        else:
            f, k = b ^ c ^ d, 0xCA62C1D6
        temp = ((((a << 5) | (a >> 27)) & 0xFFFFFFFF) + (f & 0xFFFFFFFF)
                + e + k + w[i]) & 0xFFFFFFFF
        e, d, c, b, a = d, c, ((b << 30) | (b >> 2)) & 0xFFFFFFFF, a, temp
    return [(x + y) & 0xFFFFFFFF for x, y in zip(state, [a, b, c, d, e])]


def _g(xval: bytes) -> bytes:
    iv = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    state = _sha1_compress(iv, xval + b"\x00" * 44)
    return b"".join(struct.pack(">I", x) for x in state)


def fips186_2_prf(seed: bytes, length: int) -> bytes:
    """FIPS 186-2 change notice 1 PRF, as RFC 4187 3.3 specifies for EAP-AKA."""
    xkey = seed
    out = b""
    while len(out) < length:
        for _ in range(2):
            w = _g(xkey)
            out += w
            xkey = (((int.from_bytes(xkey, "big") + int.from_bytes(w, "big") + 1)
                     % (1 << 160)).to_bytes(20, "big"))
    return out[:length]


def derive_keys(identity: str, ik: bytes, ck: bytes) -> tuple[bytes, bytes]:
    """RFC 4187 7: MK = SHA1(Identity|IK|CK); K_encr/K_aut from the PRF."""
    mk = hashlib.sha1(identity.encode("utf-8") + ik + ck).digest()
    stream = fips186_2_prf(mk, 160)
    return stream[0:16], stream[16:32]      # K_encr, K_aut


# ------------------------------------------------------------- EAP-AKA wire

EAP_REQUEST, EAP_RESPONSE = 1, 2
EAP_TYPE_AKA = 23
SUBTYPE_CHALLENGE = 1
AT_RAND, AT_AUTN, AT_RES, AT_MAC = 1, 2, 3, 11
AT_CHECKCODE = 134


def parse_eap_attributes(packet: bytes) -> tuple[int, int, dict[int, bytes]]:
    if len(packet) < 8:
        raise ValueError("EAP packet too short")
    code, identifier, length = struct.unpack(">BBH", packet[:4])
    eap_type, subtype = packet[4], packet[5]
    attrs: dict[int, bytes] = {}
    i = 8
    while i + 2 <= len(packet):
        atype, alen = packet[i], packet[i + 1]
        if alen == 0:
            break
        total = alen * 4
        attrs[atype] = packet[i + 2:i + total]
        i += total
    return identifier, subtype, attrs


def verify_server_mac(packet: bytes, k_aut: bytes) -> bool:
    """Check the server's AT_MAC with our K_aut (RFC 4187 10.15).

    This isolates key derivation from response construction: if the server's own
    MAC verifies, MK/K_aut are right and any rejection is our response shape.
    """
    i = 8
    while i + 2 <= len(packet):
        atype, alen = packet[i], packet[i + 1]
        if alen == 0:
            return False
        if atype == AT_MAC:
            zeroed = packet[:i + 4] + b"\x00" * 16 + packet[i + alen * 4:]
            want = packet[i + 4:i + 20]
            return hmac.new(k_aut, zeroed, hashlib.sha1).digest()[:16] == want
        i += alen * 4
    return False


def build_challenge_response(identifier: int, res: bytes, k_aut: bytes,
                             echo_checkcode: bool) -> bytes:
    """EAP-Response/AKA-Challenge carrying AT_RES and AT_MAC (RFC 4187 9.4).

    When the server includes AT_CHECKCODE the peer must echo one (RFC 4187
    10.13); an empty checkcode means no preceding AKA-Identity round. Omitting
    it makes the server discard the response and re-send the same challenge.
    """
    # AT_RES: reserved carries the RES length in BITS, then the value, padded.
    res_padded = res + b"\x00" * ((4 - (len(res) + 4) % 4) % 4)
    at_res = bytes([AT_RES, (len(res_padded) + 4) // 4]) + struct.pack(">H", len(res) * 8) + res_padded
    at_mac = bytes([AT_MAC, 5]) + b"\x00" * 2 + b"\x00" * 16

    at_checkcode = bytes([AT_CHECKCODE, 1]) + b"\x00" * 2 if echo_checkcode else b""

    body = (bytes([EAP_TYPE_AKA, SUBTYPE_CHALLENGE]) + b"\x00" * 2
            + at_res + at_checkcode + at_mac)
    length = 4 + len(body)
    packet = struct.pack(">BBH", EAP_RESPONSE, identifier, length) + body

    mac = hmac.new(k_aut, packet, hashlib.sha1).digest()[:16]
    offset = packet.rindex(at_mac)
    return packet[:offset + 4] + mac + packet[offset + 20:]


# ------------------------------------------------------------------- device

class Sim:
    """SIM identity plus the AKA primitive, delegated to the card."""

    def __init__(self) -> None:
        modem = keyvalues(run(["mmcli", "-m", "any", "-K"]))
        sim_path = suffix(modem, ".generic.sim")
        if not sim_path or sim_path == "--":
            raise RuntimeError("no SIM object")
        sim = keyvalues(run(["mmcli", "-i", sim_path, "-K"]))
        self.imsi = suffix(sim, ".properties.imsi")
        self.operator = suffix(sim, ".properties.operator-code") or self.imsi[:6]
        if not self.imsi or self.imsi == "--":
            raise RuntimeError("SIM did not expose an IMSI")
        self.slot, self.aid = self._usim_application()

    @staticmethod
    def _usim_application() -> tuple[int, str]:
        """Slot and USIM ADF AID from the card status."""
        # qmicli prints, per slot:
        #   Slot [2]:
        #     Application [1]:
        #       Application type:  'usim (2)'
        #       Application ID:
        #         A0:00:00:00:87:10:02:...
        # The AID sits on the line AFTER "Application ID:", colon-separated.
        text = run(["qmicli", "-d", "qrtr://0", "--uim-get-card-status"])
        lines = text.splitlines()
        slot = 0
        want_aid = False
        in_usim = False
        for line in lines:
            m = re.search(r"Slot\s*\[(\d+)\]", line)
            if m:
                slot = int(m.group(1))
                in_usim = False
                continue
            if re.search(r"Application \[\d+\]", line):
                in_usim = False
                continue
            if "application type" in line.lower():
                in_usim = "usim" in line.lower() and "isim" not in line.lower()
                continue
            if in_usim and "application id" in line.lower():
                want_aid = True
                continue
            if want_aid:
                candidate = re.sub(r"[^0-9A-Fa-f]", "", line)
                if len(candidate) >= 10:
                    return slot, candidate.upper()
                want_aid = False
        raise RuntimeError("no USIM application identifier on the card")

    @property
    def mcc(self) -> str:
        return self.operator[:3]

    @property
    def mnc(self) -> str:
        return self.operator[3:]

    def eap_identity(self) -> str:
        """EAP-AKA NAI, RFC 4187 4.1.1.1 / TS 23.003."""
        return (f"0{self.imsi}@nai.epc.mnc{self.mnc.zfill(3)}"
                f".mcc{self.mcc.zfill(3)}.3gppnetwork.org")

    def authenticate(self, rand: bytes, autn: bytes) -> tuple[bytes, bytes, bytes]:
        """AUTHENTICATE(AKA) on the card. Returns (RES, CK, IK)."""
        opened = run(["qmicli", "-d", "qrtr://0",
                      f"--uim-open-logical-channel={self.slot},{self.aid}"])
        # qmicli prints: "Open Logical Channel operation successfully completed: 3"
        m = re.search(r"completed:\s*(\d+)", opened)
        if not m:
            raise RuntimeError("could not open a UIM logical channel")
        channel = int(m.group(1))
        try:
            apdu = (f"{channel:02x}880081{(len(rand) + len(autn) + 2):02x}"
                    f"10{rand.hex()}10{autn.hex()}")
            body = self._send(channel, apdu)
            if body[:1].hex() == "61":
                body = self._send(channel, f"{channel:02x}c00000{body[1]:02x}")
            return self._parse(body)
        finally:
            run(["qmicli", "-d", "qrtr://0",
                 f"--uim-close-logical-channel={self.slot},{channel}"])

    def _send(self, channel: int, apdu: str) -> bytes:
        out = run(["qmicli", "-d", "qrtr://0",
                   f"--uim-send-apdu={self.slot},{channel},{apdu}"])
        # "Send APDU operation successfully completed: DB:20:...:90:00"
        m = re.search(r"completed:\s*([0-9A-Fa-f:\s]+)", out)
        if not m:
            raise RuntimeError("card returned no APDU response")
        return bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", m.group(1)))

    @staticmethod
    def _parse(resp: bytes) -> tuple[bytes, bytes, bytes]:
        body = resp[:-2] if len(resp) > 2 else resp
        if not body or body[0] != 0xDB:
            raise RuntimeError(f"AKA refused by card (tag 0x{body[:1].hex() or '??'})")
        i, out = 1, []
        for _ in range(3):
            n = body[i]
            i += 1
            out.append(body[i:i + n])
            i += n
        return out[0], out[1], out[2]


# --------------------------------------------------------------------- HTTP

def fetch(url: str, interface: str | None, resolve: str | None,
          timeout: int, post: bytes | None = None) -> tuple[int, str, bytes]:
    body = STATE_DIR / ".ts43-body"
    headers = STATE_DIR / ".ts43-hdr"
    argv = ["curl", "--silent", "--show-error", "--config", "-",
            "--max-time", str(timeout), "--output", str(body),
            "--cookie", str(COOKIE_JAR), "--cookie-jar", str(COOKIE_JAR),
            "--dump-header", str(headers), "--write-out", "%{http_code}"]
    post_file = STATE_DIR / ".ts43-post"
    if post is not None:
        # TS.43 2.8.1 relays the EAP response in a JSON body, not the query
        # string; the challenge came back as vnd.gsma.eap-relay+json.
        post_file.write_bytes(post)
        argv[3:3] = ["--request", "POST", "--data-binary", f"@{post_file}",
                     "--header", "Content-Type: application/vnd.gsma.eap-relay.v1.0+json",
                     "--header", "Accept: application/vnd.gsma.eap-relay.v1.0+json"]
    if interface:
        argv[3:3] = ["--interface", interface]
    if resolve:
        argv[3:3] = ["--resolve", resolve]
    try:
        done = subprocess.run(argv, input=f'url = "{url}"\n',
                              capture_output=True, text=True, timeout=timeout + 10)
    except (OSError, subprocess.SubprocessError):
        return 0, "", b""
    code = 0
    m = re.search(r"(\d{3})\s*$", done.stdout or "")
    if m:
        code = int(m.group(1))
    payload = body.read_bytes() if body.exists() else b""
    head = headers.read_text("utf-8", "replace") if headers.exists() else ""
    body.unlink(missing_ok=True)
    headers.unlink(missing_ok=True)
    post_file.unlink(missing_ok=True)
    return code, head, payload


def base_query(sim: Sim, imei: str | None, app: str = "ap2004") -> dict[str, str]:
    # TS.43 2.3: `app` names the service being asked about and is REQUIRED --
    # omitting it makes the server reject the request outright with 400.
    return {
        "app": app,
        "terminal_vendor": TERMINAL_VENDOR,
        "terminal_model": TERMINAL_MODEL,
        "terminal_sw_version": os.uname().release[:15],
        "terminal_id": imei or "",
        "entitlement_version": ENTITLEMENT_VERSION,
        "vers": "0",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default=None)
    parser.add_argument("--server-ip", default=None,
                        help="pin the entitlement server when DNS is unusable")
    parser.add_argument("--imei", default=None,
                        help="this device's own IMEI, when ModemManager cannot read it")
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--app", action="append", default=None,
                        help="TS.43 app id to query (repeatable)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        log("must run as root (drives the UIM channel)")
        return 5

    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    COOKIE_JAR.unlink(missing_ok=True)   # one EAP session per run

    try:
        sim = Sim()
    except RuntimeError as error:
        log(f"precondition failed: {error}")
        return 5

    host = f"aes.mnc{sim.mnc.zfill(3)}.mcc{sim.mcc.zfill(3)}.pub.3gppnetwork.org"
    resolve = f"{host}:443:{args.server_ip}" if args.server_ip else None
    log(f"entitlement server {host}")
    log(f"USIM application on slot {sim.slot}; EAP identity built (IMSI withheld)")

    # -- step 1: request the EAP-AKA challenge -----------------------------
    app = (args.app or ["ap2004"])[0]
    log(f"querying app {app} ({APPS.get(app, 'unknown')})")
    query = base_query(sim, args.imei, app)
    query["EAP_ID"] = sim.eap_identity()
    url = f"https://{host}/?{urllib.parse.urlencode(query)}"
    code, head, payload = fetch(url, args.interface, resolve, args.timeout)
    ctype = ""
    m = re.search(r"^content-type:\s*(.+)$", head, re.I | re.M)
    if m:
        ctype = m.group(1).strip()
    log(f"challenge request: HTTP {code or 'no-response'}, {len(payload)} bytes, type={ctype or 'none'}")

    if code != 200 or not payload:
        RESPONSE_PATH.write_bytes(payload or b"")
        os.chmod(RESPONSE_PATH, 0o600)
        log("server did not return an EAP-AKA challenge")
        log(f"raw response stored root-only at {RESPONSE_PATH}")
        return 4 if code == 0 else 2

    try:
        document = json.loads(payload.decode("utf-8", "replace"))
        relay = document.get("eap-relay-packet", "")
    except (ValueError, AttributeError):
        document, relay = {}, ""

    if not relay:
        RESPONSE_PATH.write_bytes(payload)
        os.chmod(RESPONSE_PATH, 0o600)
        log("no eap-relay-packet in the response; storing it for inspection")
        log(f"keys present: {', '.join(sorted(document)) if document else 'not JSON'}")
        return 2

    packet = base64.b64decode(relay)
    identifier, subtype, attrs = parse_eap_attributes(packet)
    log(f"EAP challenge received (subtype {subtype}, "
        f"attributes {sorted(attrs)})")
    if AT_RAND not in attrs or AT_AUTN not in attrs:
        log("challenge lacks AT_RAND/AT_AUTN")
        return 2

    rand = attrs[AT_RAND][2:18]
    autn = attrs[AT_AUTN][2:18]

    # -- step 2: run AKA on the card ---------------------------------------
    try:
        res, ck, ik = sim.authenticate(rand, autn)
    except RuntimeError as error:
        log(f"SIM authentication failed: {error}")
        return 2
    log(f"card authenticated the challenge (RES {len(res)}B, CK/IK {len(ck)}/{len(ik)}B)")

    _, k_aut = derive_keys(sim.eap_identity(), ik, ck)
    if verify_server_mac(packet, k_aut):
        log("server AT_MAC verifies with our K_aut -- key derivation is correct")
    else:
        log("server AT_MAC does NOT verify -- key derivation or identity is wrong")
    response_packet = build_challenge_response(
        identifier, res, k_aut, AT_CHECKCODE in attrs)

    # -- step 3: return the response ---------------------------------------
    relay_body = json.dumps({
        "eap-relay-packet": base64.b64encode(response_packet).decode()
    }).encode()
    url = f"https://{host}/"
    code, head, payload = fetch(url, args.interface, resolve, args.timeout,
                                post=relay_body)
    if code in (0, 400, 404, 405):
        # Some deployments relay the response on the query string instead.
        log(f"POST relay returned {code or 'no-response'}; retrying as a GET")
        query = base_query(sim, args.imei, app)
        query["EAP_ID"] = sim.eap_identity()
        query["eap-relay-packet"] = base64.b64encode(response_packet).decode()
        url = f"https://{host}/?{urllib.parse.urlencode(query)}"
        code, head, payload = fetch(url, args.interface, resolve, args.timeout)
    log(f"challenge response: HTTP {code or 'no-response'}, {len(payload)} bytes")
    if COOKIE_JAR.exists():
        session_cookies = sum(1 for line in COOKIE_JAR.read_text("utf-8", "replace").splitlines()
                              if line and not line.startswith("#"))
        log(f"session cookies carried: {session_cookies}")

    RESPONSE_PATH.write_bytes(payload or b"")
    os.chmod(RESPONSE_PATH, 0o600)
    log(f"raw response stored root-only at {RESPONSE_PATH}")

    if code != 200 or not payload:
        log("server rejected the EAP-AKA response")
        return 2

    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except ValueError:
        log("response was not JSON; stored for inspection")
        return 2

    # TS.43 nests the token under the Token characteristic, not at the root.
    token = ""
    if isinstance(document.get("Token"), dict):
        token = document["Token"].get("token", "")
    token = token or document.get("token", "")

    for key in sorted(document):
        if key in {"eap-relay-packet", "Token"}:
            continue
        log(f"  {key} = {document[key]}")
    if not token:
        log("EAP-AKA did not yield a token")
        return 2
    validity = document.get("Token", {}).get("validity", "?")
    log(f"EAP-AKA SUCCEEDED -- entitlement token issued (validity {validity}s)")

    # ---- authenticated entitlement queries -------------------------------
    log("")
    log("querying services with the token")
    results: dict[str, str] = {}
    for probe_app in [app] + [a for a in APPS if a != app]:
        query = base_query(sim, args.imei, probe_app)
        query["token"] = token
        url = f"https://{host}/?{urllib.parse.urlencode(query)}"
        code, _, payload = fetch(url, args.interface, resolve, args.timeout)
        text = payload.decode("utf-8", "replace").strip()
        if code == 200 and text:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                interesting = {k: v for k, v in parsed.items()
                               if k not in {"Vers", "Token", "AccessControl"}}
                results[probe_app] = json.dumps(interesting) if interesting else "(no service data)"
            else:
                results[probe_app] = text[:160]
        else:
            results[probe_app] = f"HTTP {code or 'no-response'} {text[:80]}"
        log(f"  {probe_app} ({APPS.get(probe_app, '?')}): {results[probe_app][:200]}")

    combined = " ".join(results.values())
    for marker in ("ServiceFlow_URL", "ServiceFlow_UserData", "WebSheet",
                   "ProvisioningStatus", "ServiceStatus"):
        if marker in combined:
            log(f"  >>> {marker} present -- an activation/service flow is offered")
    RESPONSE_PATH.write_text(json.dumps({"auth": document, "services": results},
                                        indent=1), encoding="utf-8")
    os.chmod(RESPONSE_PATH, 0o600)
    log(f"full results stored root-only at {RESPONSE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
