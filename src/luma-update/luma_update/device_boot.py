# SPDX-License-Identifier: Apache-2.0
"""Keep a phone's A/B boot partition in step with the booted deployment.

A phone boots its own kernel from an Android boot partition, outside the OS
commit. The device package that carries that kernel's modules also carries the
boot image they pair with, and declares it in
/usr/lib/luma/device-boot/<name>/device-boot.json:

  {"schema": 1, "device": "fairphone/fp6", "compatible": ["fairphone,fp6"],
   "slots": "qbootctl", "partitions": ["boot_a", "boot_b"],
   "kernel_release": "...", "kernel_notes_sha256": "<64 hex or empty>",
   "images": [{"partition": "boot", "file": "boot.bin",
               "sha256": "<64 hex>", "size": N, "partition_size": N}]}

`apply` runs early on every boot (luma-device-boot.service). When the running
slot's partitions already hold the images, nothing happens. Otherwise, once:

1. the images are checked against their digests, and the running slot must be
   marked successful (it is the fallback);
2. only partitions the manifest names, of the kinds in WRITABLE_KINDS and never
   one in PROTECTED (modem settings, IMEI, calibration), are opened for writing,
   each found by a unique GPT name with the declared size;
3. the slot tool swaps every A/B partition pair (qbootctl switches the whole
   chain and the XBL boot LUN), so every pair the manifest does not write must
   be identical on both slots;
4. each image is written to the INACTIVE slot, flushed, read back past the page
   cache and checked; the attempt is recorded in /var/lib/luma-device-boot;
5. the inactive slot is set active for the next boot (qbootctl gives it one
   try, clears its successful flag and keeps the running slot successful),
   and the phone restarts once.

`confirm` runs after multi-user.target on the trial boot: when the running
slot is the one written, holds the images and runs the declared kernel, the
slot is marked successful. A trial boot that never gets there is retried by
nobody: the bootloader's A/B fallback boots the previous slot, and the next
`apply` records the images as failed and never writes them again
(`luma-device-boot retry` forgets that).

Restarting at once rather than at the next restart keeps the deployment's
modules and the kernel together: the modules are signed with their build's key
and carry its BTF, so the old kernel cannot load them, and a whole session on
the old kernel would run without its drivers. The restart happens before the
display manager starts, only after a verified write, and at most once per
images (a boot that resolved an attempt never starts another).

`luma.device_boot=0` on the kernel command line turns the unit off, for
`fastboot boot` test kernels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

SCHEMA = 1
MANIFEST_DIR = "usr/lib/luma/device-boot"
MANIFEST_NAME = "device-boot.json"
STATE_DIR = "var/lib/luma-device-boot"
# Partition kinds this tool may ever write, whatever a manifest says.
WRITABLE_KINDS = ("boot", "dtbo", "init_boot", "vendor_boot")
WRITABLE = re.compile(r"^(%s)_[ab]$" % "|".join(WRITABLE_KINDS))
# Never opened, not even for reading: the modem's own settings (the IMEI is in
# modemst1/modemst2), its file system images, calibration and identity.
PROTECTED = ("modemst1", "modemst2", "fsg", "fsc", "persist")
PROTECTED_WORDS = re.compile(r"modemst|fsg|fsc|persist|imei|study|traceab|devinfo|keystore|frp|secdata|rpmb")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMPATIBLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9,._+-]*$")
DEVICE = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")
FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RELEASE = re.compile(r"^[0-9]+\.[0-9]+[A-Za-z0-9._+-]*$")
CHUNK = 8 << 20


class BootError(Exception):
    """A reason the boot partition is left alone; logged as is."""


class Refused(BootError):
    """The partitions disagree with the package and this tool will not fix it."""


def log(message: str) -> None:
    print(f"luma-device-boot: {message}", file=sys.stderr, flush=True)


def other(slot: str) -> str:
    return {"a": "b", "b": "a"}[slot]


def protected(name: str) -> bool:
    return name in PROTECTED or bool(PROTECTED_WORDS.search(name))


def file_sha256(path: Path, length: int | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        left = length
        while left is None or left > 0:
            block = stream.read(CHUNK if left is None else min(CHUNK, left))
            if not block:
                break
            digest.update(block)
            if left is not None:
                left -= len(block)
        if left:
            raise BootError(f"{path} is shorter than {length} bytes")
    return digest.hexdigest()


@dataclass(frozen=True)
class Image:
    partition: str
    path: Path
    sha256: str
    size: int
    partition_size: int


@dataclass(frozen=True)
class Manifest:
    path: Path
    device: str
    compatible: tuple[str, ...]
    slots: str
    partitions: tuple[str, ...]
    kernel_release: str
    kernel_notes_sha256: str
    images: tuple[Image, ...]

    @property
    def key(self) -> str:
        """Names one set of images: what an attempt, success or failure is about."""
        return ",".join(f"{image.partition}:{image.sha256}" for image in self.images)


def load_manifest(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BootError(f"{path}: {error}") from error
    where = str(path)

    def need(ok: bool, what: str) -> None:
        if not ok:
            raise BootError(f"{where}: {what}")

    need(isinstance(data, dict) and data.get("schema") == SCHEMA, f"schema is not {SCHEMA}")
    need(isinstance(data.get("device"), str) and bool(DEVICE.match(data["device"])), "bad device")
    compatible = data.get("compatible")
    need(isinstance(compatible, list) and bool(compatible)
         and all(isinstance(c, str) and COMPATIBLE.match(c) for c in compatible), "bad compatible")
    need(data.get("slots") in SLOT_TOOLS, f"unknown slots tool {data.get('slots')!r}")
    partitions = data.get("partitions")
    need(isinstance(partitions, list) and bool(partitions) and len(set(partitions)) == len(partitions),
         "bad partitions")
    for name in partitions:
        need(isinstance(name, str) and bool(WRITABLE.match(name)) and not protected(name),
             f"partition {name!r} may not be written")
        need(f"{name[:-2]}_{other(name[-1])}" in partitions, f"{name} has no other slot listed")
    release = data.get("kernel_release")
    need(isinstance(release, str) and bool(RELEASE.match(release)), "bad kernel_release")
    notes = data.get("kernel_notes_sha256", "")
    need(isinstance(notes, str) and (notes == "" or bool(SHA256.match(notes))), "bad kernel_notes_sha256")
    images = data.get("images")
    need(isinstance(images, list) and bool(images), "no images")
    parsed = []
    for entry in images:
        need(isinstance(entry, dict), "bad image entry")
        kind = entry.get("partition")
        need(kind in WRITABLE_KINDS and f"{kind}_a" in partitions, f"image partition {kind!r} is not listed")
        need(isinstance(entry.get("file"), str) and bool(FILE.match(entry["file"])), "bad image file")
        need(isinstance(entry.get("sha256"), str) and bool(SHA256.match(entry["sha256"])), "bad image sha256")
        size, part_size = entry.get("size"), entry.get("partition_size")
        need(type(size) is int and type(part_size) is int and 0 < size <= part_size, "bad image size")
        parsed.append(Image(kind, path.parent / entry["file"], entry["sha256"], size, part_size))
    need(len({image.partition for image in parsed}) == len(parsed), "two images for one partition")
    return Manifest(path, data["device"], tuple(compatible), data["slots"], tuple(partitions),
                    release, notes, tuple(sorted(parsed, key=lambda image: image.partition)))


# --- the slot tool -----------------------------------------------------------

class Qbootctl:
    """Qualcomm A/B slot attributes in the GPT (qbootctl 0.2)."""

    # qbootctl -s rewrites the A/B attributes of every *_a/*_b pair and moves
    # the XBL boot LUN: the whole chain changes slot, not only the boot image.
    swaps_every_pair = True

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 program: str = "/usr/bin/qbootctl"):
        self._run = run
        self.program = program

    def _call(self, *args: str) -> str:
        try:
            result = self._run([self.program, *args], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise BootError(f"qbootctl {' '.join(args)}: {error}") from error
        out = (result.stdout or "").strip()
        if result.returncode != 0:
            raise BootError(f"qbootctl {' '.join(args)} failed: {(result.stderr or out).strip()}")
        return out

    def _slot(self, out: str, prefix: str) -> str:
        match = re.fullmatch(prefix + r" _([ab])", out)
        if not match:
            raise BootError(f"unexpected qbootctl answer {out!r}")
        return match.group(1)

    def current(self) -> str:
        return self._slot(self._call("-c"), "Current slot:")

    def active(self) -> str:
        return self._slot(self._call("-a"), "Active slot:")

    def _flag(self, option: str, slot: str, word: str) -> bool:
        out = self._call(option, slot)
        if out == f"SLOT _{slot}: is marked {word}":
            return True
        if out == f"SLOT _{slot}: is not marked {word}":
            return False
        raise BootError(f"unexpected qbootctl answer {out!r}")

    def successful(self, slot: str) -> bool:
        return self._flag("-n", slot, "successful")

    def bootable(self, slot: str) -> bool:
        return self._flag("-b", slot, "bootable")

    def set_active(self, slot: str) -> None:
        self._call("-s", slot)

    def mark_successful(self, slot: str) -> None:
        self._call("-m", slot)

    def mark_unbootable(self, slot: str) -> None:
        self._call("-u", slot)


SLOT_TOOLS: dict[str, Callable[[], object]] = {"qbootctl": Qbootctl}


# --- partitions --------------------------------------------------------------

class Disks:
    """GPT partitions by name, from sysfs (no udev needed this early)."""

    def __init__(self, root: Path = Path("/"), block_only: bool = True):
        self.root = root
        self.block_only = block_only

    def names(self) -> dict[str, list[tuple[str, int]]]:
        found: dict[str, list[tuple[str, int]]] = {}
        base = self.root / "sys/class/block"
        for entry in sorted(base.iterdir()) if base.is_dir() else []:
            try:
                fields = dict(line.split("=", 1) for line in
                              (entry / "uevent").read_text().splitlines() if "=" in line)
                size = int((entry / "size").read_text().strip()) * 512
            except (OSError, ValueError):
                continue
            if fields.get("PARTNAME") and fields.get("DEVNAME"):
                found.setdefault(fields["PARTNAME"], []).append((fields["DEVNAME"], size))
        return found

    def find(self, name: str, table: dict[str, list[tuple[str, int]]] | None = None) -> tuple[Path, int]:
        if protected(name):
            raise Refused(f"{name} is protected and never opened")
        matches = (table if table is not None else self.names()).get(name, [])
        if len(matches) != 1:
            raise BootError(f"partition {name}: {len(matches)} found, need exactly one")
        devname, size = matches[0]
        path = self.root / "dev" / devname
        try:
            mode = os.stat(path).st_mode
        except OSError as error:
            raise BootError(f"partition {name}: {error}") from error
        if self.block_only and not stat.S_ISBLK(mode):
            raise BootError(f"partition {name}: {path} is not a block device")
        return path, size

    def read_sha256(self, path: Path, length: int) -> str:
        return file_sha256(path, length)

    def identical(self, left: Path, right: Path, length: int) -> bool:
        with open(left, "rb") as a, open(right, "rb") as b:
            while length > 0:
                want = min(CHUNK, length)
                x, y = a.read(want), b.read(want)
                if x != y or len(x) != want:
                    return False
                length -= want
        return True

    def write(self, path: Path, image: Path, size: int) -> None:
        flags = os.O_WRONLY | (os.O_EXCL if self.block_only else 0)
        fd = os.open(path, flags)
        try:
            with open(image, "rb") as source:
                left = size
                while left > 0:
                    block = source.read(min(CHUNK, left))
                    if not block:
                        raise BootError(f"{image} ended early")
                    view = memoryview(block)
                    while view:
                        view = view[os.write(fd, view):]
                    left -= len(block)
            os.fsync(fd)
            # The readback must come from the device, not the page cache.
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)


# --- the state on /var -------------------------------------------------------

class State:
    """pending.json while an attempt is unconfirmed; state.json for outcomes."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.pending_path = directory / "pending.json"
        self.state_path = directory / "state.json"

    def _read(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise BootError(f"{path}: {error}") from error
        return data if isinstance(data, dict) else None

    def _write(self, path: Path, data: dict | None) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            temporary = path.with_suffix(".tmp")
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=1, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        directory = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @property
    def pending(self) -> dict | None:
        return self._read(self.pending_path)

    def set_pending(self, data: dict | None) -> None:
        self._write(self.pending_path, data)

    @property
    def record(self) -> dict:
        data = self._read(self.state_path) or {}
        data.setdefault("failed", {})
        return data

    def save(self, data: dict) -> None:
        self._write(self.state_path, data)


# --- the updater -------------------------------------------------------------

@dataclass
class System:
    root: Path = Path("/")
    disks: Disks | None = None
    slots: object | None = None
    reboot: Callable[[], None] = lambda: subprocess.run(["systemctl", "--no-block", "reboot"], check=True)
    kernel_release: Callable[[], str] = lambda: os.uname().release
    clock: Callable[[], float] = time.time
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.disks is None:
            self.disks = Disks(self.root)

    def read(self, relative: str) -> bytes | None:
        try:
            return (self.root / relative).read_bytes()
        except OSError:
            return None

    def compatible(self) -> list[str]:
        raw = self.read("sys/firmware/devicetree/base/compatible") or self.read("proc/device-tree/compatible")
        return [word for word in (raw or b"").decode("ascii", "replace").split("\0") if word]

    def cmdline_slot(self) -> str | None:
        words = (self.read("proc/cmdline") or b"").decode("ascii", "replace").split()
        slots = [w.split("=", 1)[1] for w in words if w.startswith("androidboot.slot_suffix=")]
        if len(slots) != 1 or slots[0] not in ("_a", "_b"):
            return None
        return slots[0][1]

    def boot_id(self) -> str:
        return (self.read("proc/sys/kernel/random/boot_id") or b"").decode().strip()

    def notes_sha256(self) -> str | None:
        raw = self.read("sys/kernel/notes")
        return hashlib.sha256(raw).hexdigest() if raw is not None else None


class DeviceBoot:
    def __init__(self, system: System | None = None):
        self.sys = system or System()
        self.state = State(self.sys.root / STATE_DIR)

    # -- what this device declares

    def manifest(self) -> Manifest | None:
        base = self.sys.root / MANIFEST_DIR
        paths = sorted(base.glob(f"*/{MANIFEST_NAME}")) if base.is_dir() else []
        if not paths:
            return None
        compatible = set(self.sys.compatible())
        mine = [m for m in (load_manifest(p) for p in paths) if compatible & set(m.compatible)]
        if not mine:
            log(f"no boot image here is for this device ({', '.join(sorted(compatible)) or 'no device tree'})")
            return None
        if len(mine) > 1:
            raise BootError("more than one boot image declared for this device: "
                            + ", ".join(str(m.path) for m in mine))
        return mine[0]

    def slots(self, manifest: Manifest):
        return self.sys.slots if self.sys.slots is not None else SLOT_TOOLS[manifest.slots]()

    def running_slot(self, tool) -> str:
        slot = tool.current()
        if self.sys.cmdline_slot() != slot:
            raise BootError(f"the slot tool says _{slot}, the kernel command line says "
                            f"{self.sys.cmdline_slot() and '_' + self.sys.cmdline_slot()}")
        return slot

    def verify_images(self, manifest: Manifest) -> None:
        for image in manifest.images:
            try:
                size = image.path.stat().st_size
            except OSError as error:
                raise BootError(f"{image.path}: {error}") from error
            if size != image.size or file_sha256(image.path) != image.sha256:
                raise BootError(f"{image.path} does not match its manifest (corrupt package?)")

    def holds(self, manifest: Manifest, slot: str, table) -> bool:
        for image in manifest.images:
            path, size = self.sys.disks.find(f"{image.partition}_{slot}", table)
            if size != image.partition_size:
                raise Refused(f"{image.partition}_{slot} is {size} bytes, the manifest says {image.partition_size}")
            if self.sys.disks.read_sha256(path, image.size) != image.sha256:
                return False
        return True

    def check_other_pairs(self, manifest: Manifest, table) -> None:
        """Switching slots must change nothing but what was written."""
        for name in sorted(table):
            if not name.endswith("_a") or name in manifest.partitions or protected(name):
                continue
            twin = name[:-2] + "_b"
            if twin not in table:
                continue
            left, left_size = self.sys.disks.find(name, table)
            right, right_size = self.sys.disks.find(twin, table)
            if left_size != right_size or not self.sys.disks.identical(left, right, left_size):
                raise Refused(f"{name} and {twin} differ: switching slots would change more than the boot image")

    # -- apply (early boot)

    def apply(self, reboot: bool = True) -> int:
        manifest = self.manifest()
        if manifest is None:
            return 0
        tool = self.slots(manifest)
        current = self.running_slot(tool)
        record = self.state.record
        resolved = self.resolve_pending(manifest, current, record)
        self.verify_images(manifest)
        table = self.sys.disks.names()
        if self.holds(manifest, current, table):
            log(f"slot _{current} holds this deployment's boot image ({manifest.kernel_release})")
            return 0
        if self.state.pending:
            raise Refused(f"slot _{current} is on trial with other images; not starting another attempt")
        if manifest.key in record["failed"]:
            raise Refused(f"the boot image for {manifest.kernel_release} did not boot on slot "
                          f"_{record['failed'][manifest.key].get('slot', '?')}; staying on _{current} "
                          "(luma-device-boot retry tries again)")
        if resolved:
            raise Refused("this boot already resolved an attempt; not starting another")
        if not tool.successful(current):
            log(f"slot _{current} is not marked successful yet; the boot image waits for the next boot")
            return 0
        target = other(current)
        if tool.swaps_every_pair:
            self.check_other_pairs(manifest, table)
        if self.holds(manifest, target, table):
            log(f"slot _{target} already holds the boot image")
        else:
            for image in manifest.images:
                self.write_image(image, f"{image.partition}_{target}", table, tool)
        self.state.set_pending({
            "key": manifest.key, "slot": target, "from": current,
            "kernel_release": manifest.kernel_release, "kernel_notes_sha256": manifest.kernel_notes_sha256,
            "boot_id": self.sys.boot_id(), "time": int(self.sys.clock()),
        })
        try:
            tool.set_active(target)
            if tool.active() != target or not tool.bootable(target):
                raise BootError(f"slot _{target} is not the active, bootable slot after switching")
        except BootError as error:
            self.state.set_pending(None)
            self.restore_active(tool, current)
            raise BootError(f"switching to slot _{target} failed: {error}") from error
        log(f"slot _{target} holds {manifest.kernel_release} and boots next; slot _{current} is the fallback")
        if reboot:
            log("restarting into the new kernel")
            try:
                self.sys.reboot()
            except (OSError, subprocess.SubprocessError) as error:
                raise BootError(f"cannot restart ({error}); slot _{target} boots at the next restart") from error
        return 0

    def write_image(self, image: Image, name: str, table, tool) -> None:
        if not WRITABLE.match(name) or protected(name):
            raise Refused(f"{name} may not be written")
        path, size = self.sys.disks.find(name, table)
        if size != image.partition_size:
            raise Refused(f"{name} is {size} bytes, the manifest says {image.partition_size}")
        log(f"writing {image.path.name} ({image.size} bytes) to {name}")
        self.sys.disks.write(path, image.path, image.size)
        if self.sys.disks.read_sha256(path, image.size) != image.sha256:
            # The inactive slot is the fallback of nothing yet; keep the
            # bootloader from ever choosing it.
            try:
                tool.mark_unbootable(name[-1])
            except BootError as error:
                log(f"cannot mark slot _{name[-1]} unbootable: {error}")
            raise BootError(f"{name} reads back different from {image.path.name}; slot _{name[-1]} marked unbootable")

    def restore_active(self, tool, slot: str) -> None:
        try:
            if tool.active() != slot:
                tool.set_active(slot)
        except BootError as error:
            log(f"could not make slot _{slot} active again: {error}")

    def resolve_pending(self, manifest: Manifest, current: str, record: dict) -> bool:
        pending = self.state.pending
        if not pending:
            return False
        if pending.get("slot") == current:
            return False                       # the trial boot; confirm decides
        record["failed"][pending.get("key", "?")] = {
            "slot": pending.get("slot"), "kernel_release": pending.get("kernel_release"),
            "time": int(self.sys.clock()),
        }
        record["last"] = {"result": "fell back", "slot": pending.get("slot"), "to": current,
                          "key": pending.get("key"), "time": int(self.sys.clock())}
        self.state.save(record)
        self.state.set_pending(None)
        log(f"slot _{pending.get('slot')} did not boot {pending.get('kernel_release')}; "
            f"the bootloader fell back to _{current}")
        return True

    # -- confirm (after multi-user.target)

    def confirm(self) -> int:
        pending = self.state.pending
        if not pending:
            return 0
        manifest = self.manifest()
        if manifest is None:
            raise BootError("an attempt is pending but no boot image is declared")
        tool = self.slots(manifest)
        current = self.running_slot(tool)
        if pending.get("slot") != current:
            log(f"running slot _{current}, not the written _{pending.get('slot')}; apply records the fallback")
            return 0
        problems = []
        if pending.get("key") != manifest.key:
            problems.append("the deployment's boot image changed since the attempt")
        elif not self.holds(manifest, current, self.sys.disks.names()):
            problems.append(f"slot _{current} does not hold the written image")
        if self.sys.kernel_release() != manifest.kernel_release:
            problems.append(f"running kernel {self.sys.kernel_release()}, expected {manifest.kernel_release}")
        if manifest.kernel_notes_sha256 and self.sys.notes_sha256() != manifest.kernel_notes_sha256:
            problems.append(f"running kernel notes {self.sys.notes_sha256()}, expected {manifest.kernel_notes_sha256}")
        record = self.state.record
        if problems:
            record["last"] = {"result": "unconfirmed", "slot": current, "key": pending.get("key"),
                              "problems": problems, "time": int(self.sys.clock())}
            self.state.save(record)
            raise BootError("not marking the slot successful: " + "; ".join(problems))
        tool.mark_successful(current)
        if not tool.successful(current):
            raise BootError(f"slot _{current} is still not marked successful")
        record["last"] = {"result": "confirmed", "slot": current, "key": manifest.key,
                          "kernel_release": manifest.kernel_release, "time": int(self.sys.clock())}
        self.state.save(record)
        self.state.set_pending(None)
        log(f"slot _{current} booted {manifest.kernel_release}; marked successful")
        return 0

    # -- commands for people

    def retry(self) -> int:
        record = self.state.record
        record["failed"] = {}
        self.state.save(record)
        self.state.set_pending(None)
        log("forgot failed and unconfirmed boot images; the next boot tries again")
        return 0

    def status(self) -> int:
        out: dict = {"pending": self.state.pending, **self.state.record}
        try:
            manifest = self.manifest()
        except BootError as error:
            out["error"] = str(error)
            manifest = None
        if manifest:
            out["manifest"] = str(manifest.path)
            out["kernel_release"] = manifest.kernel_release
            out["images"] = manifest.key
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-device-boot", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply", help="write the boot image to the inactive slot when it changed (boot service)")
    apply.add_argument("--no-reboot", action="store_true", help="switch slots but do not restart")
    sub.add_parser("confirm", help="mark the trial slot successful (boot service)")
    sub.add_parser("status", help="print the declared image and past attempts")
    sub.add_parser("retry", help="forget failed or unconfirmed images so the next boot tries again")
    args = parser.parse_args(argv)
    if args.command != "status" and os.geteuid() != 0:
        log("run as root")
        return 2
    updater = DeviceBoot()
    try:
        if args.command == "apply":
            return updater.apply(reboot=not args.no_reboot)
        if args.command == "confirm":
            return updater.confirm()
        if args.command == "retry":
            return updater.retry()
        return updater.status()
    except BootError as error:
        log(str(error))
        return 1
