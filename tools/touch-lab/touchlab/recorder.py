#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Luma Touch Lab recorder.

Started as root in a transient system unit (see `touchlab sudo-command`). It
opens the touchpad's hidraw and evdev nodes, the keyboards' evdev nodes and a
`libinput debug-events` instance, then forks a writer that drops to the
owning user before it touches the filesystem. Root never writes into the
user's home and never reads anything the user can write, except
settings.json, whose keys are matched against a fixed table.

Keyboards: only the time of a key press (rounded to 10 ms) and which
keyboard it came from are recorded. The key code is read from the kernel
event, compared with a range, and dropped; it is never stored, sent to the
writer or logged. libinput's own key events are removed from its output
before it leaves this process. This is not, and must never become, a key
logger.

Nothing here grabs a device or writes to one. If this process stops, the
compositor's input is untouched. Standard library only; one file, so it can
be read in full before it runs as root.
"""

from __future__ import annotations

import argparse
import array
import ctypes
import errno
import fcntl
import gzip
import json
import os
import pwd
import re
import selectors
import signal
import socket
import struct
import sys
import time
import zlib

VERSION = 1
FORMAT = "luma-touch-lab/1"

EV_SYN, EV_KEY, EV_ABS, EV_MSC, EV_SW = 0x00, 0x01, 0x03, 0x04, 0x05
SYN_REPORT, SYN_DROPPED = 0, 3
BTN_MISC = 0x100
EVENT = struct.Struct("llHHi")          # struct input_event on 64-bit Linux
KEY_QUANTUM_NS = 10_000_000             # keyboard timestamps are rounded to 10 ms


def _ioc(direction: int, kind: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


def EVIOCGNAME(n): return _ioc(2, "E", 0x06, n)
def EVIOCGPROP(n): return _ioc(2, "E", 0x09, n)
def EVIOCGBIT(ev, n): return _ioc(2, "E", 0x20 + ev, n)
def EVIOCGABS(code): return _ioc(2, "E", 0x40 + code, 24)
EVIOCGID = _ioc(2, "E", 0x02, 8)
EVIOCSCLOCKID = _ioc(1, "E", 0xA0, 4)
def HIDIOCGFEATURE(n): return _ioc(3, "H", 0x07, n)
HIDIOCGRAWINFO = _ioc(2, "H", 0x03, 8)
CLOCK_MONOTONIC = 1

# libinput debug-events flags that settings.json may turn on; nothing else
# from that file reaches a command line.
SETTING_FLAGS = {
    ("tap-to-click", True): "--enable-tap", ("tap-to-click", False): "--disable-tap",
    ("tap-and-drag", True): "--enable-drag", ("tap-and-drag", False): "--disable-drag",
    ("tap-and-drag-lock", True): "--enable-drag-lock", ("tap-and-drag-lock", False): "--disable-drag-lock",
    ("natural-scroll", True): "--enable-natural-scrolling",
    ("natural-scroll", False): "--disable-natural-scrolling",
    ("disable-while-typing", True): "--enable-dwt", ("disable-while-typing", False): "--disable-dwt",
    ("middle-click-emulation", True): "--enable-middlebutton",
    ("middle-click-emulation", False): "--disable-middlebutton",
    ("click-method", "fingers"): "--set-click-method=clickfinger",
    ("click-method", "areas"): "--set-click-method=buttonareas",
    ("click-method", "none"): "--set-click-method=none",
    ("scroll-method", "two-finger"): "--set-scroll-method=twofinger",
    ("scroll-method", "edge"): "--set-scroll-method=edge",
    ("scroll-method", "none"): "--set-scroll-method=none",
    ("tap-button-map", "lrm"): "--set-tap-map=lrm", ("tap-button-map", "lmr"): "--set-tap-map=lmr",
    ("accel-profile", "flat"): "--set-profile=flat", ("accel-profile", "adaptive"): "--set-profile=adaptive",
}
DEBUG_EVENTS = "/usr/libexec/libinput/libinput-debug-events"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
KEYNAME = re.compile(r"\b(?:KEY|BTN)_[A-Z0-9_]+\b(?:\s*\(\d+\))?")
KEEP_FROM_KEYBOARDS = re.compile(r"dwt|disable-while-typing|typing|pair", re.I)
DEVICE_PREFIX = re.compile(r"^\s*(?:\.\.\.|\d+:)?\s*-?(event\d+)\s")
FAILED_DEVICE = re.compile(r"(?:Failed to initialize device|Invalid path) (/dev/input/event\d+)")


def log(message: str) -> None:
    print(f"luma-touch-lab: {message}", file=sys.stderr, flush=True)


# ── Device discovery ────────────────────────────────────────────────────────

def udev_props(dev: str) -> dict[str, str]:
    """Properties from the udev database for /dev/input/eventN (no libudev needed)."""
    try:
        st = os.stat(dev)
        path = f"/run/udev/data/c{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
        out = {}
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("E:") and "=" in line:
                    k, _, v = line[2:].rstrip("\n").partition("=")
                    out[k] = v
        return out
    except OSError:
        return {}


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def input_nodes() -> list[dict]:
    nodes = []
    base = "/sys/class/input"
    for name in sorted(os.listdir(base), key=lambda n: (len(n), n)):
        if not name.startswith("event"):
            continue
        sys_path = os.path.realpath(os.path.join(base, name, "device"))
        dev = f"/dev/input/{name}"
        props = udev_props(dev)
        nodes.append({
            "dev": dev, "sysname": name, "sys_path": sys_path,
            "name": read_text(os.path.join(sys_path, "name")),
            "phys": read_text(os.path.join(sys_path, "phys")),
            "props": props,
        })
    return nodes


def hidraw_for(sys_path: str) -> tuple[str | None, str | None]:
    """The hidraw node of the HID device that owns an input device."""
    hid = os.path.dirname(sys_path)              # .../0018:2C2F:0034.0002/input/inputN -> .../input
    hid = os.path.dirname(hid) if os.path.basename(hid) == "input" else hid
    raw = os.path.join(hid, "hidraw")
    try:
        names = sorted(os.listdir(raw))
    except OSError:
        return None, None
    return (f"/dev/{names[0]}" if names else None), hid


def is_internal_keyboard(node: dict) -> bool:
    path, bus = node["sys_path"], node["props"].get("ID_BUS", "")
    if "/i8042/" in path or "/serio" in path or node["phys"].startswith("isa0060"):
        return True
    if "/uhid/" in path or "/virtual/" in path or bus in ("usb", "bluetooth"):
        return False
    return bus in ("i2c", "spi", "") and "Touchpad" not in node["name"]


def discover(want: str | None) -> dict:
    nodes = input_nodes()
    touchpad = None
    for n in nodes:
        if want and want not in (n["dev"], n["name"]):
            continue
        if n["props"].get("ID_INPUT_TOUCHPAD") == "1":
            touchpad = n
            if not want and "2C2F" in n["name"]:
                break
    keyboards, switches = [], []
    for n in nodes:
        if touchpad and n["dev"] == touchpad["dev"]:
            continue
        p = n["props"]
        if p.get("ID_INPUT_KEYBOARD") == "1" or p.get("ID_INPUT_KEY") == "1" and "keyboard" in n["name"].lower():
            keyboards.append(n)
        elif p.get("ID_INPUT_SWITCH") == "1":
            switches.append(n)
    siblings = []
    if touchpad:
        group = touchpad["props"].get("LIBINPUT_DEVICE_GROUP")
        siblings = [n for n in nodes if group and n["dev"] != touchpad["dev"]
                    and n["props"].get("LIBINPUT_DEVICE_GROUP") == group]
    return {"touchpad": touchpad, "keyboards": keyboards, "switches": switches, "siblings": siblings}


def evdev_description(fd: int) -> dict:
    def bits(ev: int, nbytes: int) -> list[int]:
        buf = array.array("B", bytes(nbytes))
        try:
            fcntl.ioctl(fd, EVIOCGBIT(ev, nbytes), buf, True)
        except OSError:
            return []
        return [i for i in range(nbytes * 8) if buf[i // 8] & (1 << (i % 8))]

    name = array.array("B", bytes(256))
    fcntl.ioctl(fd, EVIOCGNAME(256), name, True)
    ident = array.array("H", [0, 0, 0, 0])
    fcntl.ioctl(fd, EVIOCGID, ident, True)
    prop = array.array("B", bytes(8))
    try:
        fcntl.ioctl(fd, EVIOCGPROP(8), prop, True)
    except OSError:
        pass
    absinfo = {}
    for code in bits(EV_ABS, 8):
        info = array.array("i", [0] * 6)
        fcntl.ioctl(fd, EVIOCGABS(code), info, True)
        absinfo[str(code)] = dict(zip(("value", "minimum", "maximum", "fuzz", "flat", "resolution"), info))
    return {
        "name": bytes(name).split(b"\0", 1)[0].decode("utf-8", "replace"),
        "id": dict(zip(("bustype", "vendor", "product", "version"), ident)),
        "properties": [i for i in range(64) if prop[i // 8] & (1 << (i % 8))],
        "codes": {str(ev): bits(ev, 96) for ev in bits(0, 4) if ev != EV_SYN},
        "absinfo": absinfo,
    }


def hid_features(fd: int, ids: list[int]) -> dict:
    """GET_FEATURE for each feature report: a read, never a SET."""
    out = {}
    for rid in ids:
        buf = array.array("B", bytes(64))
        buf[0] = rid
        try:
            n = fcntl.ioctl(fd, HIDIOCGFEATURE(64), buf, True)
            out[str(rid)] = bytes(buf[:n]).hex()
        except OSError as exc:
            out[str(rid)] = f"error {exc.errno}"
    return out


def feature_ids(descriptor: bytes) -> list[int]:
    """Feature report IDs, from a minimal walk of the descriptor's items."""
    ids, rid, i = set(), 0, 0
    while i < len(descriptor):
        b = descriptor[i]
        if b == 0xFE:
            i += 3 + (descriptor[i + 1] if i + 1 < len(descriptor) else 0)
            continue
        size = (0, 1, 2, 4)[b & 3]
        raw = descriptor[i + 1:i + 1 + size]
        if b & 0xFC == 0x84:
            rid = int.from_bytes(raw, "little")
        elif b & 0xFC == 0xB0:
            ids.add(rid)
        i += 1 + size
    # Report 7 is the 256-byte certification blob; it says nothing about touches.
    return sorted(r for r in ids if r and r != 7)


def clock_offsets() -> dict:
    mono = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    real = time.clock_gettime_ns(time.CLOCK_REALTIME)
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    return {"realtime_minus_monotonic_ns": real - mono, "boottime_minus_monotonic_ns": boot - mono}


# ── Writer (runs as the user) ───────────────────────────────────────────────

class Writer:
    CHUNK_RAW_BYTES = 48 << 20      # rotate a chunk after 48 MiB of JSON (about 5 MiB on disk)
    CHUNK_SECONDS = 15 * 60
    FLUSH_SECONDS = 5

    def __init__(self, root: str, uid: int, cap_bytes: int, floor_bytes: int) -> None:
        self.root = root
        self.sessions = os.path.join(root, "sessions")
        self.control = os.path.join(root, "control")
        self.cap_bytes, self.floor_bytes = cap_bytes, floor_bytes
        self.runtime = f"/run/user/{uid}/luma-touch-lab"
        self.header: bytes | None = None
        self.session_dir = None
        self.chunk = None
        self.chunk_index = 0
        self.chunk_raw = 0
        self.chunk_started = 0.0
        self.last_flush = 0.0
        self.paused_by: str | None = None
        self.disk_full = False
        self.used_bytes = 0
        self.counts: dict[str, int] = {}
        self.dropped = 0
        self.clients: dict[socket.socket, bytearray] = {}
        self.server: socket.socket | None = None
        self.day = None
        self.stop = False

    def setup(self) -> None:
        os.umask(0o077)
        for d in (self.root, self.sessions, self.control):
            os.makedirs(d, exist_ok=True)
        for stale in ("stop",):
            try:
                os.unlink(os.path.join(self.control, stale))
            except FileNotFoundError:
                pass
        try:
            os.makedirs(self.runtime, exist_ok=True)
            path = os.path.join(self.runtime, "live.sock")
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK)
            self.server.bind(path)
            os.chmod(path, 0o600)
            self.server.listen(4)
        except OSError as exc:
            log(f"live view socket unavailable: {exc}")
            self.server = None

    def open_session(self, header: bytes) -> None:
        self.close_chunk()
        record = json.loads(header)
        meta = record[2]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        sid = f"{stamp}-{meta.get('boot_id', '')[:8]}"
        meta["session"] = sid
        self.header = json.dumps([record[0], "H", meta], separators=(",", ":")).encode() + b"\n"
        self.session_dir = os.path.join(self.sessions, sid)
        os.makedirs(self.session_dir, exist_ok=True)
        tmp = os.path.join(self.session_dir, ".session.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1)
        os.replace(tmp, os.path.join(self.session_dir, "session.json"))
        self.chunk_index = 0
        self.day = time.strftime("%Y%m%d")
        self.open_chunk()

    def open_chunk(self) -> None:
        self.chunk_index += 1
        path = os.path.join(self.session_dir, f"stream-{self.chunk_index:06d}.jsonl.gz")
        self.chunk = gzip.open(path, "wb", compresslevel=6)
        self.chunk_raw = 0
        self.chunk_started = time.monotonic()
        self.chunk.write(self.header)

    def close_chunk(self) -> None:
        if self.chunk:
            try:
                self.chunk.close()
            except OSError:
                pass
            self.chunk = None

    def check_disk(self) -> None:
        total = 0
        for dirpath, _dirs, files in os.walk(self.sessions):
            for f in files:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
        self.used_bytes = total
        st = os.statvfs(self.sessions)
        free = st.f_bavail * st.f_frsize
        self.disk_full = total >= self.cap_bytes or free <= self.floor_bytes

    def check_control(self) -> None:
        if os.path.exists(os.path.join(self.control, "stop")):
            self.stop = True
        paused = os.path.exists(os.path.join(self.control, "pause"))
        reason = "user" if paused else ("disk" if self.disk_full else None)
        if reason != self.paused_by:
            now = time.monotonic_ns()
            self.write_record(json.dumps([now, "s", {"event": "pause" if reason else "resume",
                                                     "reason": reason}]).encode() + b"\n", force=True)
            self.paused_by = reason

    def write_status(self) -> None:
        status = {
            "pid": os.getpid(), "session": os.path.basename(self.session_dir or ""),
            "chunk": self.chunk_index, "paused": self.paused_by, "used_bytes": self.used_bytes,
            "cap_bytes": self.cap_bytes, "counts": self.counts, "dropped": self.dropped,
            "live_clients": len(self.clients), "updated_monotonic_ns": time.monotonic_ns(),
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        tmp = os.path.join(self.control, ".status.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        os.replace(tmp, os.path.join(self.control, "status.json"))

    def write_record(self, line: bytes, force: bool = False) -> None:
        if not self.chunk:
            return
        if self.paused_by and not force:
            self.dropped += 1
            return
        self.chunk.write(line)
        self.chunk_raw += len(line)
        now = time.monotonic()
        if now - self.last_flush > self.FLUSH_SECONDS:
            self.chunk.flush(zlib.Z_SYNC_FLUSH)         # a crash loses at most a few seconds
            self.last_flush = now
        if self.chunk_raw > self.CHUNK_RAW_BYTES or now - self.chunk_started > self.CHUNK_SECONDS:
            self.close_chunk()
            if time.strftime("%Y%m%d") != self.day:
                self.open_session(self.header)       # one session per day of a long run
            else:
                self.open_chunk()

    def broadcast(self, line: bytes) -> None:
        for client, buf in list(self.clients.items()):
            if len(buf) > (4 << 20):                 # a stuck viewer is dropped, never waited on
                self.drop_client(client)
                continue
            buf += line
            self.flush_client(client)

    def flush_client(self, client: socket.socket) -> None:
        buf = self.clients.get(client)
        if buf is None:
            return
        try:
            while buf:
                n = client.send(buf)
                del buf[:n]
        except BlockingIOError:
            pass
        except OSError:
            self.drop_client(client)

    def drop_client(self, client: socket.socket) -> None:
        self.clients.pop(client, None)
        try:
            client.close()
        except OSError:
            pass

    def run(self, fd: int) -> int:
        self.setup()
        os.set_blocking(fd, False)
        sel = selectors.DefaultSelector()
        sel.register(fd, selectors.EVENT_READ, "pipe")
        if self.server:
            sel.register(self.server, selectors.EVENT_READ, "server")
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: setattr(self, "stop", True))
        pending = b""
        last_control = last_disk = last_status = 0.0
        eof = False
        while not self.stop and not eof:
            for key, _mask in sel.select(timeout=0.5):
                if key.data == "server":
                    try:
                        client, _ = self.server.accept()
                        client.setblocking(False)
                        self.clients[client] = bytearray(self.header or b"")
                        self.flush_client(client)
                    except OSError:
                        pass
                    continue
                try:
                    data = os.read(fd, 1 << 16)
                except BlockingIOError:
                    continue
                if not data:
                    eof = True
                    break
                pending += data
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    line = raw + b"\n"
                    tag = raw[raw.find(b",") + 2:raw.find(b",") + 3].decode("ascii", "replace")
                    if tag == "H":
                        self.open_session(raw)
                        self.check_disk()
                        continue
                    self.counts[tag] = self.counts.get(tag, 0) + 1
                    self.write_record(line)
                    if self.clients:
                        self.broadcast(line)
            now = time.monotonic()
            if now - last_disk > 30:
                self.check_disk()
                last_disk = now
            if now - last_control > 1:
                self.check_control()
                last_control = now
            if now - last_status > 2:
                self.write_status()
                last_status = now
        self.write_record(json.dumps([time.monotonic_ns(), "s", {"event": "stop"}]).encode() + b"\n", force=True)
        self.close_chunk()
        self.write_status()
        if self.server:
            try:
                os.unlink(os.path.join(self.runtime, "live.sock"))
            except OSError:
                pass
        return 0


# ── Collector (stays root, never writes files) ──────────────────────────────

class Source:
    def __init__(self, kind: str, path: str, fd: int, index: int = 0) -> None:
        self.kind, self.path, self.fd, self.index = kind, path, fd, index
        self.frame: list[list[int]] = []
        self.pending = b""


class Collector:
    def __init__(self, args, root: str, pw) -> None:
        self.args = args
        self.root = root
        self.pw = pw
        self.sel = selectors.DefaultSelector()
        self.sources: dict[int, Source] = {}
        self.out = bytearray()
        self.out_fd = -1
        self.out_dropped = 0
        self.writer_pid = 0
        self.libinput: dict[str, tuple] = {}
        self.devices: dict = {}
        self.device_key = None
        self.stop = False
        self.libinput_args: list[str] = []
        self.libinput_devs: list[str] = []
        self.libinput_bad: set[str] = set()
        self.restart_at: dict[str, float] = {}

    # output -------------------------------------------------------------
    def emit(self, record: list) -> None:
        line = json.dumps(record, separators=(",", ":")).encode() + b"\n"
        if len(self.out) > (16 << 20):          # the writer stalled: drop, never block on it
            self.out_dropped += 1
            return
        self.out += line
        self.flush()

    def flush(self) -> None:
        try:
            while self.out:
                n = os.write(self.out_fd, self.out)
                del self.out[:n]
        except BlockingIOError:
            pass
        except BrokenPipeError:
            self.stop = True

    def status(self, **fields) -> None:
        self.emit([time.monotonic_ns(), "s", fields])

    # devices ------------------------------------------------------------
    def settings_flags(self) -> tuple[list[str], dict]:
        path = os.path.join(self.root, "settings.json")
        data = {}
        try:
            # A user-owned file read by root: regular files only, never blocking, bounded.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                st = os.fstat(fd)
                if st.st_uid == self.pw.pw_uid and (st.st_mode & 0o170000) == 0o100000 and st.st_size < 65536:
                    data = json.loads(os.read(fd, 65536))
            finally:
                os.close(fd)
        except (OSError, ValueError):
            data = {}
        flags = []
        touchpad = data.get("touchpad", {}) if isinstance(data, dict) else {}
        for key in sorted(touchpad) if isinstance(touchpad, dict) else ():
            flag = SETTING_FLAGS.get((key, touchpad[key])) if isinstance(touchpad[key], (bool, str)) else None
            if flag:
                flags.append(flag)
        speed = touchpad.get("speed") if isinstance(touchpad, dict) else None
        if isinstance(speed, (int, float)) and -1.0 <= speed <= 1.0:
            flags.append(f"--set-speed={float(speed):.3f}")
        return flags, touchpad if isinstance(touchpad, dict) else {}

    def open_node(self, kind: str, path: str, index: int = 0) -> Source | None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            self.status(event="open-failed", source=kind, error=exc.errno)
            return None
        if kind in ("evdev", "keyboard"):
            try:
                fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
            except OSError:
                pass
        src = Source(kind, path, fd, index)
        self.sources[fd] = src
        self.sel.register(fd, selectors.EVENT_READ, src)
        return src

    def close_source(self, src: Source) -> None:
        self.sources.pop(src.fd, None)
        try:
            self.sel.unregister(src.fd)
        except (KeyError, ValueError):
            pass
        os.close(src.fd)

    def attach(self, header: bool) -> bool:
        found = discover(self.args.touchpad)
        tp = found["touchpad"]
        if not tp:
            return False
        key = (tp["dev"], tuple(k["dev"] for k in found["keyboards"]),
               tuple(s["dev"] for s in found["switches"]))
        if key == self.device_key and not header:
            return True
        for src in list(self.sources.values()):
            if src.kind != "libinput":
                self.close_source(src)
        hidraw, hid_dir = hidraw_for(tp["sys_path"])
        ev = self.open_node("evdev", tp["dev"])
        raw = self.open_node("hidraw", hidraw) if hidraw else None
        description, descriptor, features = {}, b"", {}
        if ev:
            try:
                description = evdev_description(ev.fd)
            except OSError as exc:
                description = {"error": exc.errno}
        if hid_dir:
            try:
                with open(os.path.join(hid_dir, "report_descriptor"), "rb") as fh:
                    descriptor = fh.read()
            except OSError:
                pass
        if raw and descriptor and not self.args.no_features:
            features = hid_features(raw.fd, feature_ids(descriptor))
        keyboards = []
        for i, k in enumerate(found["keyboards"]):
            self.open_node("keyboard", k["dev"], i)
            keyboards.append({"index": i, "internal": is_internal_keyboard(k), "name": k["name"],
                              "bus": k["props"].get("ID_BUS", "")})
        if key != self.device_key:
            self.libinput_bad.clear()
        self.device_key = key
        self.devices = {
            "touchpad": {
                "evdev": tp["dev"], "sysname": tp["sysname"], "name": tp["name"], "hidraw": hidraw,
                "hid": os.path.basename(hid_dir or ""), "evdev_description": description,
                "udev": {k: v for k, v in tp["props"].items() if k.startswith(("ID_INPUT", "LIBINPUT", "ID_BUS"))},
                "report_descriptor": descriptor.hex(), "feature_reports": features,
                "siblings": [s["dev"] for s in found["siblings"]],
            },
            "keyboards": keyboards,
            "switches": [s["name"] for s in found["switches"]],
        }
        devices_for_libinput = [tp["dev"]] + [s["dev"] for s in found["siblings"]] + \
            [k["dev"] for k in found["keyboards"]] + [s["dev"] for s in found["switches"]]
        flags, settings = self.settings_flags()
        self.libinput_args = flags
        self.libinput_devs = devices_for_libinput
        if header:
            self.emit([time.monotonic_ns(), "H", {
                "format": FORMAT, "recorder_version": VERSION,
                "boot_id": read_text("/proc/sys/kernel/random/boot_id"),
                "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "clock": clock_offsets(),
                "host": {"product": read_text("/sys/class/dmi/id/product_name"),
                         "sku": read_text("/sys/class/dmi/id/product_sku"),
                         "kernel": os.uname().release,
                         "os": read_text("/etc/os-release").split("\n")[0:6]},
                "libinput": {"tool": DEBUG_EVENTS, "flags": flags, "settings": settings,
                             "devices": devices_for_libinput,
                             "shadow_plugins": bool(self.args.shadow_plugins)},
                "devices": self.devices,
                "key_quantum_ns": KEY_QUANTUM_NS,
            }])
        else:
            self.status(event="devices-changed", devices=self.devices)
        self.start_libinput("l", self.libinput_devices(), flags)
        if self.args.shadow_plugins:
            self.start_libinput("p", self.libinput_devices(), flags)
        return True

    def start_libinput(self, tag: str, devices: list[str], flags: list[str]) -> None:
        self.stop_libinput(tag)
        if tag == "l" and not os.access(DEBUG_EVENTS, os.X_OK):
            self.status(event="libinput-missing")
            return
        if tag == "p":
            # Stock tools only read the compositor's plugin directories; shadow.py
            # loads this one directory into a private context instead.
            argv = [sys.executable, "-I", "-B", os.path.join(os.path.dirname(os.path.abspath(__file__)), "shadow.py"),
                    f"--plugins={self.args.shadow_plugins}"] + [f"--flag={f}" for f in flags]
        else:
            argv = [DEBUG_EVENTS, "--verbose", "--enable-plugins"] + flags   # what the compositor loads
        for d in devices:
            argv += ["--device", d]
        master, slave = os.openpty()                 # a terminal keeps its output line-buffered
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                libc = ctypes.CDLL(None, use_errno=True)
                libc.prctl(1, signal.SIGTERM)        # PR_SET_PDEATHSIG: never outlive the recorder
                os.dup2(slave, 1)
                os.dup2(slave, 2)
                devnull = os.open("/dev/null", os.O_RDONLY)
                os.dup2(devnull, 0)
                os.closerange(3, 1 << 16)
                os.nice(5)
                os.execv(argv[0], argv)
            finally:
                os._exit(127)
        os.close(slave)
        os.set_blocking(master, False)
        src = Source("libinput", tag, master)
        self.sources[master] = src
        self.sel.register(master, selectors.EVENT_READ, src)
        self.libinput[tag] = (pid, src)
        self.status(event="libinput-start", stream=tag, argv=argv[1:])

    def stop_libinput(self, tag: str) -> None:
        entry = self.libinput.pop(tag, None)
        if not entry:
            return
        pid, src = entry
        if src.fd in self.sources:
            self.close_source(src)
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

    # reading ------------------------------------------------------------
    def read_evdev(self, src: Source, now: int) -> None:
        try:
            data = os.read(src.fd, EVENT.size * 256)
        except BlockingIOError:
            return
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            sec, usec, etype, code, value = EVENT.unpack_from(data, off)
            if src.kind == "keyboard":
                # Only "a key went down on keyboard N, around now". The code is
                # tested and discarded here; it is not kept or passed on.
                if etype == EV_KEY and value == 1 and 0 < code < BTN_MISC:
                    t = (now // KEY_QUANTUM_NS) * KEY_QUANTUM_NS
                    self.emit([t, "k", src.index])
                continue
            if etype == EV_SYN and code == SYN_REPORT:
                self.emit([now, "e", sec * 1_000_000 + usec, src.frame])
                src.frame = []
            elif etype == EV_SYN and code == SYN_DROPPED:
                self.status(event="evdev-dropped")
                src.frame = []
            else:
                src.frame.append([etype, code, value])

    def read_hidraw(self, src: Source, now: int) -> None:
        while True:
            try:
                data = os.read(src.fd, 4096)
            except BlockingIOError:
                return
            if not data:
                return
            self.emit([now, "h", data.hex()])

    def read_libinput(self, src: Source, now: int) -> None:
        try:
            data = os.read(src.fd, 1 << 16)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno == errno.EIO:            # the tool exited
                data = b""
            else:
                raise
        if not data:
            self.status(event="libinput-exit", stream=src.path)
            self.close_source(src)
            self.libinput.pop(src.path, None)
            self.restart_at[src.path] = time.monotonic() + 3
            return
        src.pending += data
        *lines, src.pending = src.pending.split(b"\n")
        tp = self.devices.get("touchpad", {}).get("sysname")
        for raw in lines:
            line = ANSI.sub("", raw.decode("utf-8", "replace")).rstrip("\r")
            failed = FAILED_DEVICE.search(line)
            if failed and failed.group(1) != self.devices.get("touchpad", {}).get("evdev"):
                self.libinput_bad.add(failed.group(1))   # libinput gives up on every device for one bad one
            clean = sanitize_libinput_line(line, tp)
            if clean is not None:
                self.emit([now, src.path, clean])

    def run(self) -> int:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: setattr(self, "stop", True))
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        while not self.attach(header=True):
            if self.stop:
                return 0
            log("no touchpad yet; retrying")
            time.sleep(5)
        last_scan = time.monotonic()
        while not self.stop:
            events = self.sel.select(timeout=1.0)
            now = time.monotonic_ns()
            for key, _mask in events:
                src: Source = key.data
                try:
                    if src.kind in ("evdev", "keyboard"):
                        self.read_evdev(src, now)
                    elif src.kind == "hidraw":
                        self.read_hidraw(src, now)
                    else:
                        self.read_libinput(src, now)
                except OSError as exc:
                    self.status(event="source-lost", source=src.kind, error=exc.errno)
                    self.close_source(src)
                    self.device_key = None           # rediscover on the next scan
            mono = time.monotonic()
            for tag, when in list(self.restart_at.items()):
                if mono >= when:
                    del self.restart_at[tag]
                    if self.device_key:
                        self.start_libinput(tag, self.libinput_devices(), self.libinput_args)
            if mono - last_scan > 10:
                last_scan = mono
                try:
                    self.attach(header=False)
                except OSError as exc:
                    self.status(event="rescan-failed", error=exc.errno)
            pid, _status = os.waitpid(self.writer_pid, os.WNOHANG)
            if pid:
                break
            if self.out:
                self.flush()
        self.status(event="collector-stop", dropped=self.out_dropped)
        for tag in list(self.libinput):
            self.stop_libinput(tag)
        os.set_blocking(self.out_fd, True)
        try:
            self.flush()
            os.close(self.out_fd)
        except OSError:
            pass
        try:
            os.waitpid(self.writer_pid, 0)
        except ChildProcessError:
            pass
        return 0

    def libinput_devices(self) -> list[str]:
        return [d for d in self.libinput_devs if d not in self.libinput_bad]


def sanitize_libinput_line(line: str, touchpad: str | None) -> str | None:
    """Keep the touchpad's events and libinput's reasoning; drop every key event."""
    if not line.strip():
        return None
    if "KEYBOARD_KEY" in line:
        return None
    m = DEVICE_PREFIX.match(line)
    device = m.group(1) if m else None
    if device and touchpad and device != touchpad:
        if not KEEP_FROM_KEYBOARDS.search(line) and "SWITCH" not in line and "DEVICE_" not in line:
            return None
    return KEYNAME.sub(lambda k: k.group(0) if k.group(0).startswith("BTN_") else "KEY", line)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Luma Touch Lab recorder (run as root via systemd-run)")
    p.add_argument("--user", required=True, help="the user who owns the recordings")
    p.add_argument("--touchpad", help="device node or name (default: the internal touchpad)")
    p.add_argument("--cap-gib", type=float, default=4.0, help="pause when recordings reach this size")
    p.add_argument("--free-floor-gib", type=float, default=20.0, help="pause when the disk has less free")
    p.add_argument("--shadow-plugins", help="also run a private libinput with the Lua plugins in this directory")
    p.add_argument("--no-features", action="store_true", help="skip HID GET_FEATURE reads")
    p.add_argument("--data-root", help="record here instead of ~/.local/share/luma-touch-lab (tests)")
    args = p.parse_args(argv)
    pw = pwd.getpwnam(args.user)
    if pw.pw_uid == 0:
        log("refusing to record for root")
        return 2
    root = os.path.realpath(args.data_root) if args.data_root else \
        os.path.join(os.path.realpath(pw.pw_dir), ".local/share/luma-touch-lab")
    if args.shadow_plugins:
        args.shadow_plugins = os.path.realpath(args.shadow_plugins)
    if os.geteuid() != 0:
        log("must start as root (sudo systemd-run ...); see `touchlab sudo-command`")
        return 2
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:
        os.close(write_fd)
        os.initgroups(pw.pw_name, pw.pw_gid)
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
        if os.getuid() == 0 or os.geteuid() == 0:
            os._exit(3)
        os.environ.clear()
        os.environ.update({"HOME": pw.pw_dir, "USER": pw.pw_name})
        writer = Writer(root, pw.pw_uid, int(args.cap_gib * (1 << 30)), int(args.free_floor_gib * (1 << 30)))
        os._exit(writer.run(read_fd))
    os.close(read_fd)
    os.set_blocking(write_fd, False)
    collector = Collector(args, root, pw)
    collector.out_fd = write_fd
    collector.writer_pid = pid
    return collector.run()


if __name__ == "__main__":
    sys.exit(main())
