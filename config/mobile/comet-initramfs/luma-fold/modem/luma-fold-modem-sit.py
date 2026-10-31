#!/usr/bin/python3
"""Fold modem bring-up: a minimal, read-mostly SIT client for the journal.

Drains the indications the CP queued on wwan0sit0/wwan0sit1 since MAIN armed,
asks both stacks for radio state, SIM status and the SIM slot inventory, the
IMEI (presence only) and the baseband version, then sends the harmless first
half of the RIL boot handshake (AP system time, device info) and asks again.
With --carrier it completes the boot batch (software version, modem count),
resolves the SIM's carrier against the staged carrier database the way the
samsung-sit ModemManager plugin does, and sends the carrier-config manifest so
the CP pulls its profile over RFS. With --esim it opens a logical channel to
the eUICC's ISD-R on the SIM's stack and sends two read-only ES10 queries
(GetEuiccInfo1, GetProfilesInfo), printing the SGP.22 version and each
profile's state, class and service provider -- never an ICCID, EID or
nickname. The radio is never switched on.

Never prints an identifier: no IMEI, IMSI, ICCID, EID or ATR bytes; only
states, counts, lengths and the CP's own version strings.

Wire format (samsung-sit crate, GPL-2.0-or-later): 8-byte header -- type
(0 request, 1 response, 2 indication), reserved, id LE16, total length LE16,
token LE16 -- then the body; a response carries a 32-bit token at @6 and its
status byte at @10, an indication's payload starts at @8.
"""
import json
import os
import re
import select
import struct
import sys
import time

PORTS = ("/dev/wwan0sit0", "/dev/wwan0sit1")
NAMES = {}
try:
    NAMES = {int(k): v for k, v in json.load(open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "luma-fold-modem-sit-names.json"))).items()}
except (OSError, ValueError):
    pass

GET_SIM_STATUS = 0x0200
GET_SIM_SLOT_STATUS = 0x024D
SIM_SLOT_STATUS_CHANGED = 0x024E
SET_LOGICAL_SLOT_MAPPING = 0x0250
SET_STACK_STATUS = 0x080F
GET_STACK_STATUS = 0x0810
GET_IMEI = 0x0401
GET_RADIO_POWER = 0x0801
RADIO_STATE_CHANGED = 0x0802
GET_BASE_BAND_VERSION = 0x0901
DEVICE_INFO = 0x0922
SET_AP_SYSTEM_TIME = 0x0949
GET_IMEISV = 0x0402
OEM_CONFIG = 0x4605
SET_MODEM_CONFIG = 0x093F
CARRIER_CONFIG_MANIFEST = 0x4600
CARRIER_CONFIG_STATUS = 0x4601
CARRIER_CONFIG_SIM_INFO = 0x4602
CARRIER_DIR = "/var/lib/luma-fold/firmware/google/s5400/carrierconfig"
SIM_OPEN_CHANNEL = 0x0247
SIM_TRANSMIT_APDU = 0x020F
SIM_CLOSE_CHANNEL = 0x020E
ISD_R = bytes.fromhex("A0000005591010FFFFFFFF8900000100")
CARD = {0: "absent", 1: "present", 2: "error", 3: "restricted"}
APP = {0: "unknown", 1: "detected", 2: "pin", 3: "puk", 4: "perso", 5: "ready"}
RADIO = {0: "unknown", 1: "off", 2: "on"}
_token = 0x100
# The bootloader's IMEIs, compared in memory only (never printed).
CHOSEN_IMEI = {}
for _k in ("imei1", "imei2"):
    try:
        _v = open("/proc/device-tree/chosen/config/" + _k, "rb").read().split(b"\0")[0].decode("ascii", "replace").strip()
        if _v:
            CHOSEN_IMEI[_k] = _v
    except OSError:
        pass
SIM_INFO = {}	# port -> (iccid, imsi); memory only, never printed
LAST_SLOT_IND = []	# the latest SIM_SLOT_STATUS_CHANGED frame
SET_SIM_CARD_POWER = 0x024C
SET_NR_MODE = 0x0748
GET_NR_MODE = 0x0749
GET_ENDC_MODE = 0x073E


def digits(field):
    s = field.split(b"\0")[0]
    return s.decode() if s and s.isdigit() else None


def name(i):
    return NAMES.get(i, "0x%04x" % i)


def frame(msg_id, body):
    global _token
    _token += 1
    return struct.pack("<BBHHH", 0, 0, msg_id, 8 + len(body), _token) + body, _token


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0] if len(b) >= off + 4 else None


def describe(f):
    """One line about a frame, decoding only fields that carry no identifier."""
    if len(f) < 8:
        return "short frame (%d bytes)" % len(f)
    typ, _, mid, length = struct.unpack_from("<BBHH", f, 0)
    kind = {0: "REQ", 1: "RSP", 2: "IND"}.get(typ, "type%d" % typ)
    line = "%s %s len %d" % (kind, name(mid), len(f))
    if typ == 1 and len(f) > 10:
        line += " status %d" % f[10]
        if f[10]:
            return line
    if mid == RADIO_STATE_CHANGED and typ == 2:
        line += " state %s" % RADIO.get(u32(f, 8), u32(f, 8))
    elif mid == GET_RADIO_POWER and typ == 1:
        line += " state %s" % RADIO.get(u32(f, 12), u32(f, 12))
    elif mid == GET_SIM_STATUS and typ == 1 and len(f) >= 17:
        apps = []
        for i in range(f[14]):
            r = f[17 + 63 * i:17 + 63 * (i + 1)]
            if len(r) == 63:
                apps.append("type %d %s pin1 %d" % (r[0], APP.get(r[62], r[62]), r[55]))
        line += " card %s upin %d apps %d [%s]" % (CARD.get(f[12], f[12]), f[13], f[14],
                                                   "; ".join(apps))
    elif mid in (GET_SIM_SLOT_STATUS, SIM_SLOT_STATUS_CHANGED):
        p = 12 if typ == 1 else 8
        if len(f) > p:
            n, recs = f[p], f[p + 1:]
            size = 105 if len(recs) >= 4 * 105 else 65
            slots = []
            for i in range(min(n, 4)):
                r = recs[size * i:size * (i + 1)]
                if size == 105 and len(r) == 105:
                    ports = [r[53 + 13 * j:66 + 13 * j] for j in range(min(r[52], 4))]
                    slots.append("slot%d card %s euicc %s ports [%s]" % (
                        i, CARD.get(r[0], r[0]), "yes" if r[35] else "no",
                        ", ".join("iccid %s lslot %s active %d" % (
                            "set" if q[0] else "none",
                            "-" if q[11] == 0xFF else q[11], q[12]) for q in ports)))
                elif len(r) == 65:
                    slots.append("slot%d card %s legacy" % (i, CARD.get(r[0], r[0])))
            line += " slots %d [%s]" % (n, "; ".join(slots))
    elif mid in (GET_NR_MODE, GET_ENDC_MODE) and typ == 1 and len(f) > 12:
        line += " mode %d" % f[12]
    elif mid == GET_STACK_STATUS and typ == 1 and len(f) > 12:
        line += " stack %s" % ("enabled" if f[12] else "disabled")
    elif mid == GET_IMEI and typ == 1 and len(f) > 12:
        d = f[13:13 + f[12]].decode("ascii", "replace")
        ok = False
        if len(d) == 15 and d.isdigit():
            s = sum((int(c) if k % 2 == 0 else sum(divmod(2 * int(c), 10)))
                    for k, c in enumerate(d[:14]))
            ok = (10 - s % 10) % 10 == int(d[14])
        line += " imei %s" % ("absent" if not d else "%d digits, %s, %s" % (
            len(d), "valid" if ok else "invalid", "all zero" if not d.strip("0") else "nonzero"))
        if d:
            m = [k for k, v in sorted(CHOSEN_IMEI.items()) if v == d]
            line += ", %s (bootloader has %s)" % (
                "matches " + "+".join(m) if m else "matches neither bootloader IMEI",
                "+".join(sorted(CHOSEN_IMEI)) or "none")
    elif mid == GET_BASE_BAND_VERSION and typ == 1:
        versions = sorted(set(re.findall(rb"[a-z][a-z0-9]{3,}-[0-9]{6}-[0-9]{6}-[A-Z]-[0-9]+", f)))
        line += " versions [%s]" % ", ".join(v.decode() for v in versions)
    elif mid == CARRIER_CONFIG_STATUS and typ == 2:
        line += " words %s" % " ".join("%#x" % u32(f, o) for o in range(8, min(len(f), 24), 4))
    elif mid == DEVICE_INFO and typ == 1:
        line += " %s" % " ".join(s.decode() for s in re.findall(rb"[A-Za-z]+=[0-9]+", f))
    return line


def read_frames(fds, seconds, want=None):
    """Print every frame for up to @seconds; stop early once @want arrives."""
    end, got = time.time() + seconds, None
    while time.time() < end:
        r = select.select(list(fds), [], [], max(0, end - time.time()))[0]
        for fd in r:
            try:
                f = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not f:
                continue
            print("  %s <- %s" % (fds[fd], describe(f)))
            if len(f) >= 8 and f[0] == 2 and struct.unpack_from("<H", f, 2)[0] == SIM_SLOT_STATUS_CHANGED:
                LAST_SLOT_IND[:] = [f]
            if len(f) >= 49 and f[0] == 2 and struct.unpack_from("<H", f, 2)[0] == CARRIER_CONFIG_SIM_INFO:
                SIM_INFO[fds[fd]] = (digits(f[12:33]), digits(f[33:49]))
            if want and len(f) >= 10 and f[0] == 1 and fds[fd] == want[0] and \
                    struct.unpack_from("<H", f, 6)[0] == want[1]:
                got = f
        if got:
            return got
    return got


def ask(fds, fd, label, msg_id, body=b"\0\0\0\0"):
    f, tok = frame(msg_id, body)
    try:
        os.write(fd, f)
    except OSError as e:
        print("  %s -> %s: write failed: %s" % (label, name(msg_id), e))
        return None
    print("  %s -> %s" % (label, name(msg_id)))
    got = read_frames(fds, 4, (label, tok))
    if got is None:
        print("  %s: no response to %s" % (label, name(msg_id)))
    return got


def like(value, pattern):
    """SQL LIKE with % and _, as the plugin's like_match()."""
    if not pattern or pattern == "%":
        return True
    if value is None:
        return False
    rx = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, value, re.S) is not None


def resolve_carrier(db_path, imsi, iccid):
    """(carrier_id, name, sha1, branch) per mm-sit-carrier-config.c, minus the
    SIM-certificate privilege rule; SPN/GID unknown, so rank 7 rows win."""
    import sqlite3
    db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    best = None
    for row in db.execute(
            "SELECT ci.rowid,ci.carrier_id,ci.mccmnc,ci.imsi_prefix_xpattern,ci.spn,ci.gid1,"
            "ci.gid2,ci.iccid_prefix,cm.confman,COALESCE(n.name,pn.name,'') FROM carrier_info ci "
            "INNER JOIN confmap cm ON cm.carrier_id=CAST(ci.carrier_id AS TEXT) "
            "LEFT JOIN confnames n ON n.carrier_id=ci.carrier_id "
            "LEFT JOIN confnames pn ON pn.carrier_id=CAST(cm.parent_id AS INTEGER) "
            "WHERE SUBSTR(?1,1,LENGTH(ci.mccmnc))=ci.mccmnc ORDER BY ci.rowid", (imsi,)):
        rowid, cid, mccmnc, pat, spn, gid1, gid2, iccid_prefix, sha1, name = row
        if not like(imsi, pat):
            continue
        if iccid and iccid.startswith("894936") and mccmnc == "20801" and \
                (not iccid_prefix or not like("894936", iccid_prefix)):
            continue
        if not (spn == "%" and gid1 == "%" and gid2 == "%"):
            continue			# rank 7 is all an unknown SPN/GID can match
        key = (0 if (not pat or pat == "%") else -1, -len(mccmnc or ""), rowid)
        if best is None or key < best[0]:
            best = (key, (cid, name, sha1, "carrier_info"))
    if best is None and iccid:
        strength = -1
        for cid, prefix, sha1, name in db.execute(
                "SELECT i.carrier_id,i.iccid_prefix,cm.confman,COALESCE(n.name,pn.name,'') FROM iin i "
                "INNER JOIN confmap cm ON cm.carrier_id=i.carrier_id "
                "LEFT JOIN confnames n ON n.carrier_id=CAST(i.carrier_id AS INTEGER) "
                "LEFT JOIN confnames pn ON pn.carrier_id=CAST(cm.parent_id AS INTEGER)"):
            s = len(re.split(r"[%_]", prefix or "")[0])
            if like(iccid, prefix) and s > strength:
                strength, best = s, (None, (int(cid), name, sha1, "iccid"))
    if best is None:
        row = db.execute(
            "SELECT r.carrier_id,cm.confman,COALESCE(n.name,'') FROM regional_fallback r "
            "INNER JOIN confmap cm ON cm.carrier_id=r.carrier_id "
            "LEFT JOIN confnames n ON n.carrier_id=CAST(r.carrier_id AS INTEGER) "
            "WHERE CAST(r.country_code AS INTEGER)=0 ORDER BY CAST(r.carrier_id AS INTEGER) LIMIT 1").fetchone()
        if row:
            best = (None, (int(row[0]), row[2], row[1], "global fallback"))
    db.close()
    return best[1] if best else None


def carrier(fds, sit0):
    print("== SIT: boot batch, second half")
    got = ask(fds, sit0, "wwan0sit0", GET_IMEISV)
    if got and len(got) > 13 and got[10] == 0 and got[12] >= 3:
        sv = got[13:13 + got[12]].decode("ascii", "replace")[-2:]
        if sv.isdigit():
            print("  software version %s" % sv)
            ask(fds, sit0, "wwan0sit0", OEM_CONFIG, b"\0\0\0\0" + sv.encode())
    ask(fds, sit0, "wwan0sit0", SET_MODEM_CONFIG, b"\0\0\0\0\x01")
    print("== SIT: carrier configuration")
    if not os.path.isfile(os.path.join(CARRIER_DIR, "cfg.db")):
        print("  no staged carrier database")
        return
    if not SIM_INFO:
        print("  no CARRIER_CONFIG_SIM_INFO from the CP: no SIM to configure")
        return
    for label, (iccid, imsi) in sorted(SIM_INFO.items()):
        fd = next(k for k, v in fds.items() if v == label)
        if not imsi:
            print("  %s: SIM info carries no IMSI" % label)
            continue
        c = resolve_carrier(os.path.join(CARRIER_DIR, "cfg.db"), imsi, iccid)
        if not c:
            print("  %s: no carrier profile" % label)
            continue
        cid, cname, sha1, branch = c
        sha1 = sha1.lower()
        exists = os.path.isfile(os.path.join(CARRIER_DIR, "manifests", sha1))
        print("  %s: carrier %s (id %s) via %s, manifest %s %s" % (
            label, cname or "?", cid, branch, sha1[:12], "present" if exists else "MISSING"))
        if not exists or len(sha1) != 40:
            continue
        body = bytearray(524)
        d = CARRIER_DIR.encode()
        body[4:8] = struct.pack("<I", len(d))
        body[8:8 + len(d)] = d
        body[264:268] = struct.pack("<I", 20)
        body[268:288] = bytes.fromhex(sha1)
        ask(fds, fd, label, CARRIER_CONFIG_MANIFEST, bytes(body))
    print("== SIT: frames after the manifest (15 s)")
    read_frames(fds, 15)


def tlv(data):
    """Yield (tag, value) for each BER-TLV object at this level."""
    i = 0
    while i < len(data):
        tag = data[i]
        i += 1
        if tag & 0x1F == 0x1F:
            while i < len(data):
                tag = (tag << 8) | data[i]
                i += 1
                if not data[i - 1] & 0x80:
                    break
        if i >= len(data):
            return
        n = data[i]
        i += 1
        if n & 0x80:
            k = n & 0x7F
            n = int.from_bytes(data[i:i + k], "big")
            i += k
        yield tag, data[i:i + n]
        i += n


def apdu(fds, fd, label, channel, cla, ins, p1, p2, data):
    """One APDU on a logical channel; follows 61xx with GET RESPONSE."""
    def send(c, i, a, b, p3, d):
        body = bytearray(30 + 2 * len(d))
        struct.pack_into("<IIIIII", body, 4, channel, c, i, a, b, p3)
        struct.pack_into("<H", body, 28, len(d))
        body[30:30 + len(d)] = d
        r = ask(fds, fd, label, SIM_TRANSMIT_APDU, bytes(body))
        if not r or len(r) < 16 or r[10]:
            return None
        n = struct.unpack_from("<H", r, 14)[0]
        return r[12], r[13], r[16:16 + n]
    r = send(cla, ins, p1, p2, len(data), data)
    out = b""
    while r:
        sw1, sw2, d = r
        out += d
        if sw1 != 0x61:
            return sw1, sw2, out
        r = send(channel, 0xC0, 0, 0, sw2, b"")
    return None


def esim(fds):
    print("== SIT: eUICC (read-only ES10)")
    for label in sorted(SIM_INFO) or ["wwan0sit1"]:
        fd = next((k for k, v in fds.items() if v == label), None)
        if fd is None:
            continue
        body = bytearray(22)
        body[4] = len(ISD_R)
        body[5:5 + len(ISD_R)] = ISD_R
        r = ask(fds, fd, label, SIM_OPEN_CHANNEL, bytes(body))
        if not r or len(r) < 20 or r[10]:
            print("  %s: no logical channel to the ISD-R" % label)
            continue
        ch = struct.unpack_from("<I", r, 12)[0]
        print("  %s: ISD-R channel %d, SELECT sw %02x%02x" % (label, ch, r[16], r[17]))
        cla = 0x80 | ch
        r = apdu(fds, fd, label, ch, cla, 0xE2, 0x91, 0x00, bytes.fromhex("BF2000"))
        if r:
            ver = "?"
            for tag, val in tlv(r[2]):
                if tag == 0xBF20:
                    for t2, v2 in tlv(val):
                        if t2 == 0x82 and len(v2) == 3:
                            ver = "%d.%d.%d" % tuple(v2)
            print("  %s: GetEuiccInfo1 sw %02x%02x, SGP.22 %s" % (label, r[0], r[1], ver))
        r = apdu(fds, fd, label, ch, cla, 0xE2, 0x91, 0x00, bytes.fromhex("BF2D00"))
        if r:
            profiles = []
            for tag, val in tlv(r[2]):
                if tag != 0xBF2D:
                    continue
                for t2, v2 in tlv(val):
                    if t2 != 0xA0:
                        continue
                    for t3, v3 in tlv(v2):
                        if t3 != 0xE3:
                            continue
                        f = dict(tlv(v3))
                        state = {0: "disabled", 1: "enabled"}.get(f.get(0x9F70, b"\xff")[0], "?")
                        klass = {0: "test", 1: "provisioning", 2: "operational"}.get(
                            f.get(0x95, b"\x02")[0], "?")
                        sp = f.get(0x91, b"").decode("utf-8", "replace")
                        profiles.append("%s %s%s" % (state, klass, " '%s'" % sp if sp else ""))
            print("  %s: GetProfilesInfo sw %02x%02x, %d profile(s): %s" % (
                label, r[0], r[1], len(profiles), "; ".join(profiles)))
        ask(fds, fd, label, SIM_CLOSE_CHANNEL, struct.pack("<II", 0, ch))


def raw_apdu(fds, fd, label, channel, cla, ins, p1, p2, p3, data=b""):
    """One TPDU; returns (sw1, sw2, data) after GET RESPONSE / 6Cxx retries."""
    def send(c, i, a, b, n, d):
        body = bytearray(30 + 2 * len(d))
        struct.pack_into("<IIIIII", body, 4, channel, c, i, a, b, n)
        struct.pack_into("<H", body, 28, len(d))
        body[30:30 + len(d)] = d
        r = ask(fds, fd, label, SIM_TRANSMIT_APDU, bytes(body))
        if not r or len(r) < 16 or r[10]:
            return None
        n = struct.unpack_from("<H", r, 14)[0]
        return r[12], r[13], r[16:16 + n]
    r = send(cla, ins, p1, p2, p3, data)
    if r and r[0] == 0x6C:
        r = send(cla, ins, p1, p2, r[1], data)
    out = b""
    while r:
        sw1, sw2, d = r
        out += d
        if sw1 != 0x61:
            return sw1, sw2, out
        r = send(cla, 0xC0, 0, 0, sw2, b"")
    return None


def fcp_info(fcp):
    """(size, record length, record count, structure) from an FCP template."""
    size = rlen = rnum = 0
    struct_ = "?"
    for tag, val in tlv(fcp):
        if tag != 0x62:
            continue
        for t2, v2 in tlv(val):
            if t2 in (0x80, 0x81) and not size:
                size = int.from_bytes(v2, "big")
            elif t2 == 0x82 and v2:
                struct_ = {1: "transparent", 2: "linear", 6: "cyclic"}.get(v2[0] & 0x07, "%02x" % v2[0])
                if len(v2) >= 5:
                    rlen = int.from_bytes(v2[2:4], "big")
                    rnum = v2[4]
    return size, rlen, rnum, struct_


def sim5g(fds):
    """USIM 5G files, read-only: UST (service table), the DF 5GS file list with
    sizes, and EF URSP content. Identity-bearing files (5GS3GPPLOCI, NSC,
    SUCI/SUPI) are only selected for their size, never read."""
    print("== SIT: USIM 5G files (read-only)")
    fd = next((k for k, v in fds.items() if v.endswith("sit0")), None)
    if fd is None:
        return
    label = fds[fd]
    # The USIM AID from EF DIR on the basic channel.
    aid = None
    r = raw_apdu(fds, fd, label, 0, 0x00, 0xA4, 0x00, 0x04, 2, bytes.fromhex("2F00"))
    print("  EF DIR select sw %s" % ("%02x%02x" % r[:2] if r else "none"))
    if r and r[0] == 0x90:
        _, rlen, rnum, _ = fcp_info(r[2])
        for rec in range(1, (rnum or 4) + 1):
            rr = raw_apdu(fds, fd, label, 0, 0x00, 0xB2, rec, 0x04, rlen or 0)
            if not rr or rr[0] != 0x90:
                break
            for tag, val in tlv(rr[2]):
                if tag == 0x61:
                    for t2, v2 in tlv(val):
                        if t2 == 0x4F and v2[:7] == bytes.fromhex("A0000000871002"):
                            aid = v2
    if aid is None:
        aid = bytes.fromhex("A0000000871002")
        print("  USIM AID not found in EF DIR; trying the partial RID+app code")
    else:
        print("  USIM AID from EF DIR (%d bytes)" % len(aid))
    body = bytearray(22)
    body[4] = len(aid)
    body[5:5 + len(aid)] = aid
    r = ask(fds, fd, label, SIM_OPEN_CHANNEL, bytes(body))
    if not r or len(r) < 20 or r[10]:
        print("  no logical channel to the USIM")
        return
    ch = struct.unpack_from("<I", r, 12)[0]
    cla = ch if ch < 4 else 0x40 | (ch - 4)
    print("  USIM channel %d, SELECT sw %02x%02x" % (ch, r[16], r[17]))
    try:
        r = raw_apdu(fds, fd, label, ch, cla, 0xA4, 0x00, 0x04, 2, bytes.fromhex("6F38"))
        if r and r[0] == 0x90:
            size = fcp_info(r[2])[0]
            rr = raw_apdu(fds, fd, label, ch, cla, 0xB0, 0, 0, min(size, 255) if size else 0)
            if rr and rr[0] == 0x90:
                ust = rr[2]
                on = [i * 8 + b + 1 for i, byte in enumerate(ust) for b in range(8) if byte >> b & 1]
                print("  UST %d bytes: %s" % (len(ust), ust.hex()))
                print("  UST services 100+: %s" % [s for s in on if s >= 100])
        else:
            print("  UST select sw %s" % ("%02x%02x" % r[:2] if r else "none"))
        r = raw_apdu(fds, fd, label, ch, cla, 0xA4, 0x00, 0x04, 2, bytes.fromhex("5FC0"))
        print("  DF 5GS select sw %s" % ("%02x%02x" % r[:2] if r else "none"))
        if not r or r[0] != 0x90:
            return
        for fid in range(0x4F01, 0x4F13):
            r = raw_apdu(fds, fd, label, ch, cla, 0xA4, 0x00, 0x04, 2, fid.to_bytes(2, "big"))
            if not r or r[0] != 0x90:
                continue
            size, rlen, rnum, st = fcp_info(r[2])
            print("  EF %04X: %s, size %d, record %d x %d" % (fid, st, size, rlen, rnum))
            if fid != 0x4F0B:
                continue
            data = b""
            if st == "transparent":
                off = 0
                while off < size:
                    n = min(size - off, 255)
                    rr = raw_apdu(fds, fd, label, ch, cla, 0xB0, off >> 8, off & 0xFF, n)
                    if not rr or rr[0] != 0x90:
                        break
                    data += rr[2]
                    off += n
            else:
                for rec in range(1, rnum + 1):
                    rr = raw_apdu(fds, fd, label, ch, cla, 0xB2, rec, 0x04, rlen)
                    if rr and rr[0] == 0x90:
                        data += rr[2]
            used = len(data.rstrip(b"\xff"))
            print("  EF URSP %d bytes read, %d before the FF padding:" % (len(data), used))
            for off in range(0, min(used, 2048), 32):
                print("    %04x %s" % (off, data[off:off + 32].hex()))
            print("    text: %s" % "".join(chr(c) if 32 <= c < 127 else "." for c in data[:used])[:400])
    finally:
        ask(fds, fd, label, SIM_CLOSE_CHANNEL, struct.pack("<II", 0, ch))


SOCKS = []


def parse_slots(f):
    """[(euicc, [(logical slot or None, active)])] from a GET_SIM_SLOT_STATUS
    response or a SIM_SLOT_STATUS_CHANGED indication (the layout describe()
    decodes); no identifiers."""
    out = []
    if not f or len(f) <= 12:
        return out
    if f[0] == 1:
        if f[10]:
            return out
        p = 12
    else:
        p = 8
    n, recs = f[p], f[p + 1:]
    if len(recs) < 4 * 105:
        return out
    for i in range(min(n, 4)):
        r = recs[105 * i:105 * (i + 1)]
        ports = [r[53 + 13 * j:66 + 13 * j] for j in range(min(r[52], 4))]
        out.append((bool(r[35]), [(None if q[11] == 0xFF else q[11], q[12]) for q in ports]))
    return out


def stack_status(fds):
    for fd, label in sorted(fds.items(), key=lambda x: x[1]):
        ask(fds, fd, label, GET_STACK_STATUS)


def sim_ready(f):
    """card present and a USIM/SIM application ready, from GET_SIM_STATUS."""
    if not f or len(f) < 17 or f[10] or f[12] != 1:
        return False
    for i in range(f[14]):
        r = f[17 + 63 * i:17 + 63 * (i + 1)]
        if len(r) == 63 and r[62] == 5:
            return True
    return False


def euicc_lslot(slots):
    for eu, ports in slots:
        if eu and ports:
            return ports[0][0]
    return None


def slot_map(fds, sit0, sit1):
    """Put the eUICC's first port on logical slot 0, the way Android maps an
    eSIM-only phone: SET_LOGICAL_SLOT_MAPPING with one entry, logical 0 =
    (eUICC, port 0) -- the form the CP accepted (v3zn). Skipped when the
    mapping already persists. Then wait for SIM_SLOT_STATUS_CHANGED to show
    it and for the SIM on stack 0 to come up ready, power-cycling that card
    (SET_SIM_CARD_POWER 0 then 1) once if it has not after 30 s."""
    print("== SIT: logical slot mapping (eUICC port 0 to logical slot 0)")
    stack_status(fds)
    slots = parse_slots(ask(fds, sit0, "wwan0sit0", GET_SIM_SLOT_STATUS))
    euicc = next((i for i, (eu, _) in enumerate(slots) if eu), None)
    if euicc is None or not slots[euicc][1]:
        print("  no eUICC in the slot status; mapping skipped")
        return
    cur = euicc_lslot(slots)
    print("  eUICC = physical slot %d, port 0 on logical slot %s at the start" % (euicc, cur))
    if cur == 0:
        print("  already on logical slot 0 (the mapping persisted); no remap")
    else:
        got = ask(fds, sit0, "wwan0sit0", SET_LOGICAL_SLOT_MAPPING,
                  b"\0\0\0\0" + bytes([1, euicc, 0]) + b"\0" * 6)
        st = got[10] if got is not None and len(got) > 10 else None
        print("  SET_LOGICAL_SLOT_MAPPING [count 1: l0=(eUICC,0)]: status %s" % st)
        end = time.time() + 15
        while time.time() < end and euicc_lslot(parse_slots(LAST_SLOT_IND[0] if LAST_SLOT_IND else None)) != 0:
            read_frames(fds, 1)
        lnow = euicc_lslot(parse_slots(LAST_SLOT_IND[0] if LAST_SLOT_IND else None))
        if lnow != 0:
            lnow = euicc_lslot(parse_slots(ask(fds, sit0, "wwan0sit0", GET_SIM_SLOT_STATUS)))
        print("  eUICC port 0 now on logical slot %s" % lnow)
        if lnow != 0:
            print("  remap did not take; stack 0 stays as it was")
            return
    t0, cycled, ready = time.time(), False, False
    if "--sim-cycle" in sys.argv:
        # A profile was just switched: power-cycle the card on stack 0 so the
        # CP loads the newly enabled profile, then wait for it below.
        print("  power-cycling the card on stack 0 (SET_SIM_CARD_POWER 0, 1) after the profile switch")
        ask(fds, sit0, "wwan0sit0", SET_SIM_CARD_POWER, b"\0\0\0\0\x00")
        read_frames(fds, 2)
        ask(fds, sit0, "wwan0sit0", SET_SIM_CARD_POWER, b"\0\0\0\0\x01")
        read_frames(fds, 3)
        cycled = True
    while time.time() - t0 < 60:
        ready = sim_ready(ask(fds, sit0, "wwan0sit0", GET_SIM_STATUS))
        if ready:
            break
        if not cycled and time.time() - t0 > 30:
            print("  stack 0 SIM not ready after 30 s: power-cycling the card (SET_SIM_CARD_POWER 0, 1)")
            ask(fds, sit0, "wwan0sit0", SET_SIM_CARD_POWER, b"\0\0\0\0\x00")
            read_frames(fds, 2)
            ask(fds, sit0, "wwan0sit0", SET_SIM_CARD_POWER, b"\0\0\0\0\x01")
            cycled = True
        read_frames(fds, 3)
    print("  stack 0 SIM ready: %s after %.0f s" % ("yes" if ready else "NO", time.time() - t0))


def open_port(index, path):
    """The port through sit-proxy when it owns the device (the raw sockets
    speak plain SIT frames), else the device node itself."""
    sock = "/run/sit-proxy/%d" % index
    if os.path.exists(sock):
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock)
        s.setblocking(False)
        SOCKS.append(s)
        print("%s via %s" % (os.path.basename(path), sock))
        return s.fileno()
    return os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)


def main():
    readonly = "--readonly" in sys.argv
    fds = {}
    for i, p in enumerate(PORTS):
        try:
            fds[open_port(i, p)] = os.path.basename(p)
        except OSError as e:
            print("%s: %s" % (p, e))
    if not fds:
        return
    sit0 = next(fd for fd, n in fds.items() if n.endswith("sit0")) if any(
        n.endswith("sit0") for n in fds.values()) else None
    print("== SIT: queued indications")
    read_frames(fds, 1.5)
    print("== SIT: queries")
    for fd, label in sorted(fds.items(), key=lambda x: x[1]):
        for mid in (GET_RADIO_POWER, GET_SIM_STATUS, GET_IMEI):
            ask(fds, fd, label, mid)
    if sit0 is not None:
        ask(fds, sit0, "wwan0sit0", GET_SIM_SLOT_STATUS)
        ask(fds, sit0, "wwan0sit0", GET_BASE_BAND_VERSION, b"\0\0\0\0\xff")
    if "--slicing" in sys.argv:
        # SLICING_CONFIG (0x0622, stock BuildSlicingConfig: 13 bytes, an int at
        # byte 12): ask with 0 and with 1, dump each answer in full (DNNs,
        # slices, route selection descriptors -- no subscriber identifiers).
        if sit0 is not None:
            for arg in (0, 1):
                got = ask(fds, sit0, "wwan0sit0", 0x0622, b"\0\0\0\0" + bytes([arg]))
                if got is not None:
                    print("  SLICING_CONFIG(%d) answer %d bytes status %s:" % (arg, len(got), got[10] if len(got) > 10 else None))
                    for off in range(0, min(len(got), 1024), 32):
                        print("    %04x %s" % (off, got[off:off + 32].hex()))
                    printable = "".join(chr(c) if 32 <= c < 127 else "." for c in got)
                    print("    text: %s" % printable[:400])
        read_frames(fds, 2)
        for fd in fds:
            os.close(fd)
        return
    if "--sim5g" in sys.argv:
        sim5g(fds)
        read_frames(fds, 1)
        for fd in fds:
            os.close(fd)
        return
    if "--nr-mode" in sys.argv:
        # Read the NR and EN-DC modes on stack 0, then set the NR mode to the
        # value given (stock BuildSetNrMode: 13 bytes, mode at byte 12) and read
        # it back. NRMODE_ORIG=<n> lets the caller put the original back.
        want = int(sys.argv[sys.argv.index("--nr-mode") + 1])
        if sit0 is not None:
            ask(fds, sit0, "wwan0sit0", GET_ENDC_MODE)
            got = ask(fds, sit0, "wwan0sit0", GET_NR_MODE)
            if got is not None and len(got) > 12 and got[10] == 0:
                print("NRMODE_ORIG=%d" % got[12])
                if got[12] != want and want != 9:  # 9: read only
                    ask(fds, sit0, "wwan0sit0", SET_NR_MODE, b"\0\0\0\0" + bytes([want]))
                    ask(fds, sit0, "wwan0sit0", GET_NR_MODE)
        read_frames(fds, 1)
        for fd in fds:
            os.close(fd)
        return
    if "--slot-map" in sys.argv:
        sit1 = next((fd for fd, n in fds.items() if n.endswith("sit1")), None)
        if sit0 is not None:
            slot_map(fds, sit0, sit1)
        read_frames(fds, 1)
        for fd in fds:
            os.close(fd)
        return
    if readonly:
        # ModemManager owns the boot handshake and the carrier configuration.
        read_frames(fds, 1)
        for fd in fds:
            os.close(fd)
        return
    if sit0 is not None:
        print("== SIT: boot handshake, first half (AP time, device info)")
        t = time.localtime()
        ask(fds, sit0, "wwan0sit0", SET_AP_SYSTEM_TIME,
            b"\0\0\0\0" + bytes([t.tm_year - 1900, t.tm_mon - 1, t.tm_mday,
                                 t.tm_hour, t.tm_min, t.tm_sec]))
        info = bytearray(132)
        for off, val in ((4, b"Pixel 9 Pro Fold"), (36, b"Luma"), (68, b"comet")):
            info[off:off + len(val)] = val
        ask(fds, sit0, "wwan0sit0", DEVICE_INFO, bytes(info))
    if "--carrier" in sys.argv and sit0 is not None:
        carrier(fds, sit0)
    if "--esim" in sys.argv:
        esim(fds)
    print("== SIT: after the handshake")
    for fd, label in sorted(fds.items(), key=lambda x: x[1]):
        for mid in (GET_RADIO_POWER, GET_SIM_STATUS):
            ask(fds, fd, label, mid)
    read_frames(fds, 2)
    for fd in fds:
        os.close(fd)
    for s in SOCKS:
        s.close()


if __name__ == "__main__":
    main()
