# SPDX-License-Identifier: Apache-2.0
"""Layer a phone's device kit onto the generic ARM OS on its first boot.

A phone's disk is the one generic phone userdata image plus the device kit
written to its LumaBoot filesystem. The image builder names that filesystem in
/etc/luma/device-kit.conf. On the first boot this command:

1. finds the kit (luma-device-kit/ on that filesystem) and checks kit.json and
   the repository's repomd.xml against the Luma OS Release key in
   /etc/pki/ostree/luma-release.gpg;
2. refuses a kit whose device-tree compatible list does not match this device;
3. copies the repository to /var/lib/luma-device-layer/repo, imports the
   rpm-gpg release key into its gpgdir/ (libdnf checks a file:// repository's
   repomd.xml there and never imports gpgkey= into it), and runs rpm-ostree
   with only that repository configured (no network): one transaction,
   override replace --from repo=ID with --install for the packages layered on
   top (two transactions would assemble and deploy the OS twice);
4. points /etc/yum.repos.d/ID.repo at the device repository's https URL (with
   repo_gpgcheck=1), so rpm-ostree upgrades keep the layer, and restarts once.

A boot whose deployment already carries the layer writes
/var/lib/luma-device-layer/layered, after which the unit no longer starts.
Failures never restart the phone; after MAX_ATTEMPTS staged attempts that did
not boot (a greenboot rollback, power lost before finalisation) it stops and
says so. `luma-device-layer apply --retry` starts over.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

MAX_ATTEMPTS = 2
KIT_DIR = "luma-device-kit"
KIT_SCHEMA = 1
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
REPO_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/%-]*/$")
COMPATIBLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9,._+-]*$")


class LayerError(Exception):
    """A reason the layer cannot be applied; logged as is."""


@dataclass
class Paths:
    root: Path = Path("/")
    conf: str = "etc/luma/device-kit.conf"
    state_dir: str = "var/lib/luma-device-layer"
    mount: str = "run/luma-device-kit"
    repos: str = "etc/yum.repos.d"
    keyring: str = "etc/pki/ostree/luma-release.gpg"
    rpm_key: str = "etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release"
    compatible: str = "sys/firmware/devicetree/base/compatible"
    sysroot: str = "sysroot"

    def __getattribute__(self, name: str):
        value = object.__getattribute__(self, name)
        if name != "root" and isinstance(value, str):
            return object.__getattribute__(self, "root") / value
        return value


@dataclass
class Kit:
    device: str
    name: str
    compatible: list[str]
    repo_id: str
    repo_url: str
    install: list[str]
    replace: list[str]
    packages: list[str]
    version: str
    path: Path = field(default=Path())

    @property
    def repo(self) -> Path:
        return self.path / "repo"


def log(message: str) -> None:
    print(f"luma-device-layer: {message}", flush=True)


def default_run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(argv), text=True, capture_output=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(list(argv), 127, "", f"{argv[0]}: not found")


def parse_conf(text: str) -> dict[str, str]:
    conf = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise LayerError(f"device-kit.conf: malformed line {line!r}")
        conf[key.strip()] = value.strip()
    if "source" not in conf:
        raise LayerError("device-kit.conf names no source")
    return conf


def parse_kit(text: str) -> Kit:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LayerError(f"kit.json is not JSON: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != KIT_SCHEMA:
        raise LayerError(f"kit.json schema is not {KIT_SCHEMA}")
    repo = data.get("repo") or {}

    def names(key: str, required: bool = False) -> list[str]:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and NAME.match(v) for v in value):
            raise LayerError(f"kit.json {key} must be a list of package names")
        if required and not value:
            raise LayerError(f"kit.json {key} is empty")
        return value

    compatible = data.get("compatible")
    if not isinstance(compatible, list) or not compatible or not all(
            isinstance(v, str) and COMPATIBLE.match(v) for v in compatible):
        raise LayerError("kit.json compatible must list device-tree compatible strings")
    if not REPO_ID.match(str(repo.get("id", ""))):
        raise LayerError("kit.json repo.id is invalid")
    if not URL.match(str(repo.get("url", ""))):
        raise LayerError("kit.json repo.url must be an https:// URL ending in /")
    kit = Kit(device=str(data.get("device", "")), name=str(data.get("name", "")), compatible=compatible,
              repo_id=repo["id"], repo_url=repo["url"], install=names("install"), replace=names("replace"),
              packages=names("packages", required=True), version=str(data.get("version", "")))
    if not kit.install and not kit.replace:
        raise LayerError("kit.json installs and replaces nothing")
    missing = sorted(set(kit.install + kit.replace) - set(kit.packages))
    if missing:
        raise LayerError(f"kit.json lists packages its repository lacks: {', '.join(missing)}")
    return kit


def read_compatible(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    return [part.decode("ascii", "replace") for part in raw.split(b"\0") if part]


def parse_origin(text: str) -> tuple[set[str], dict[str, set[str]]]:
    """Requested packages and remote replacements (repo id -> names) of an origin file."""
    requested: set[str] = set()
    replaced: dict[str, set[str]] = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, _, value = line.partition("=")
        if section == "packages" and key == "requested":
            requested |= {v for v in value.split(";") if v}
        elif section == "overrides" and key == "replace":
            for group in (g for g in value.split(";") if g):
                source, *names = group.split(",")
                if source.startswith("repo="):
                    replaced.setdefault(source[5:], set()).update(names)
    return requested, replaced


def layer_in(requested: set[str], replaced: dict[str, set[str]], kit: Kit) -> bool:
    return set(kit.install) <= requested and set(kit.replace) <= replaced.get(kit.repo_id, set())


class _GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


def librepo_import_key(key: Path, gpgdir: Path) -> None:
    """Import an armored key into gpgdir with librepo's own backend (rpm or GPGME),
    in the form lr_gpg_check_signature reads back."""
    try:
        lib = ctypes.CDLL("librepo.so.0")
    except OSError as exc:
        raise LayerError(f"cannot load librepo: {exc}") from None
    func = lib.lr_gpg_import_key
    func.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(_GError))]
    func.restype = ctypes.c_int
    error = ctypes.POINTER(_GError)()
    if not func(os.fsencode(key), os.fsencode(gpgdir), ctypes.byref(error)):
        message = error.contents.message.decode(errors="replace") if error else "no error message"
        raise LayerError(f"importing {key} into {gpgdir} failed: {message}")


def repo_file_text(kit: Kit, baseurl: str, rpm_key: str) -> str:
    return "\n".join([
        f"# {kit.name or kit.device} device packages, layered on the Luma OS with",
        "# rpm-ostree from the device kit (luma-device-layer). Upgrades keep the layer.",
        f"[{kit.repo_id}]",
        f"name=Luma {kit.name or kit.device}",
        f"baseurl={baseurl}",
        "enabled=1",
        "gpgcheck=1",
        "repo_gpgcheck=1",
        f"gpgkey=file://{rpm_key}",
        f"includepkgs={','.join(sorted(kit.packages))}",
        "",
    ])


class DeviceLayer:
    def __init__(self, paths: Paths | None = None, run: Callable = default_run,
                 sleep: Callable[[float], None] = time.sleep,
                 import_key: Callable[[Path, Path], None] = librepo_import_key):
        self.paths = paths or Paths()
        self.run = run
        self.sleep = sleep
        self.import_key = import_key

    # -- state -----------------------------------------------------------------

    @property
    def marker(self) -> Path:
        return self.paths.state_dir / "layered"

    @property
    def state_file(self) -> Path:
        return self.paths.state_dir / "state.json"

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def save_state(self, state: dict) -> None:
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.state_file)

    # -- commands --------------------------------------------------------------

    def check(self, argv: Sequence[str], what: str) -> str:
        result = self.run(argv)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise LayerError(f"{what} failed ({result.returncode}): {detail[-1] if detail else 'no output'}")
        return result.stdout

    def status(self) -> dict:
        try:
            return json.loads(self.check(["rpm-ostree", "status", "--json"], "rpm-ostree status"))
        except ValueError as exc:
            raise LayerError(f"rpm-ostree status is not JSON: {exc}") from None

    def deployment_origin(self, deployment: dict) -> str:
        path = (self.paths.sysroot / "ostree/deploy" / deployment["osname"] / "deploy"
                / f"{deployment['checksum']}.{deployment['serial']}.origin")
        try:
            return path.read_text()
        except OSError as exc:
            raise LayerError(f"cannot read the deployment origin {path}: {exc}") from None

    # -- kit -------------------------------------------------------------------

    def find_source(self, source: str, wait: float) -> Path | None:
        """The block device (or directory) holding the kit; None when absent."""
        if source.startswith("/"):
            path = self.paths.root / source.lstrip("/")
            return path if path.is_dir() else None
        key, _, value = source.partition("=")
        flag = {"UUID": "-U", "LABEL": "-L", "PARTLABEL": None}.get(key, "")
        if flag == "" or not value:
            raise LayerError(f"device-kit.conf source {source!r} is not UUID=, LABEL= or a path")
        deadline = time.monotonic() + wait
        while True:
            if flag:
                result = self.run(["blkid", flag, value])
            else:
                result = self.run(["blkid", "-o", "device", "-t", f"PARTLABEL={value}"])
            device = (result.stdout or "").strip().splitlines()
            if result.returncode == 0 and device:
                return Path(device[0])
            if time.monotonic() >= deadline:
                return None
            self.sleep(2)

    def mount_kit(self, source: Path) -> Path:
        if source.is_dir():
            return source
        mnt = self.paths.mount
        mnt.mkdir(parents=True, exist_ok=True)
        if self.run(["mountpoint", "-q", str(mnt)]).returncode != 0:
            self.check(["mount", "-o", "ro,nodev,nosuid,noexec", str(source), str(mnt)], f"mounting {source}")
        return mnt

    def unmount_kit(self) -> None:
        mnt = self.paths.mount
        if self.run(["mountpoint", "-q", str(mnt)]).returncode == 0:
            self.run(["umount", str(mnt)])

    def verify(self, data: Path) -> None:
        signature = data.with_name(data.name + ".asc")
        if not signature.is_file():
            raise LayerError(f"{data} has no signature {signature.name}")
        if not self.paths.keyring.is_file():
            raise LayerError(f"no release key at {self.paths.keyring}")
        result = self.run(["gpgv", "--keyring", str(self.paths.keyring), str(signature), str(data)])
        if result.returncode != 0:
            raise LayerError(f"{data} is not signed by the Luma OS Release key")

    def load_kit(self, top: Path) -> Kit:
        kit_dir = top / KIT_DIR
        manifest = kit_dir / "kit.json"
        if not manifest.is_file():
            raise FileNotFoundError(manifest)
        self.verify(manifest)
        kit = parse_kit(manifest.read_text())
        kit.path = kit_dir
        return kit

    def stage_repo(self, kit: Kit) -> Path:
        """Copy the kit's repository to /var and check it there (what rpm-ostree reads)."""
        target = self.paths.state_dir / "repo"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(kit.repo, target)
        self.verify(target / "repodata" / "repomd.xml")
        # libdnf treats a file:// baseurl as a local repository: it checks
        # repomd.xml.asc against the keys in <baseurl>/gpgdir and skips the
        # refresh that imports gpgkey=, so the key goes in here (any gpgdir
        # the kit carried is not trusted).
        gpgdir = target / "gpgdir"
        shutil.rmtree(gpgdir, ignore_errors=True)
        if gpgdir.exists() or gpgdir.is_symlink():
            gpgdir.unlink()
        if not self.paths.rpm_key.is_file():
            raise LayerError(f"no release key at {self.paths.rpm_key}")
        gpgdir.mkdir(mode=0o755)
        self.import_key(self.paths.rpm_key, gpgdir)
        self.run(["restorecon", "-R", str(target)])
        return target

    # -- repositories ------------------------------------------------------------

    # Other repositories are renamed in place (dnf and rpm-ostree read only
    # *.repo): /etc and /var are separate mounts on an OSTree system, and a
    # rename never crosses them.
    ASIDE = ".luma-aside"

    def set_repos_aside(self, keep: str) -> None:
        for repo in sorted(self.paths.repos.glob("*.repo")):
            if repo.name != keep:
                os.replace(repo, repo.with_name(repo.name + self.ASIDE))

    def restore_repos(self) -> None:
        for aside in sorted(self.paths.repos.glob("*.repo" + self.ASIDE)):
            target = aside.with_name(aside.name[:-len(self.ASIDE)])
            if target.exists():
                aside.unlink()
            else:
                os.replace(aside, target)

    def write_repo(self, kit: Kit, baseurl: str) -> None:
        path = self.paths.repos / f"{kit.repo_id}.repo"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(repo_file_text(kit, baseurl, "/" + self.paths.rpm_key.relative_to(self.paths.root).as_posix()))
        tmp.chmod(0o644)
        os.replace(tmp, path)

    # -- main --------------------------------------------------------------------

    def apply(self, retry: bool = False, reboot: bool = True, wait: float = 30) -> int:
        self.restore_repos()  # left over from an interrupted run
        if retry:
            self.marker.unlink(missing_ok=True)
            self.state_file.unlink(missing_ok=True)
        if self.marker.exists():
            log("the device layer is in place")
            return 0
        conf_path = self.paths.conf
        if not conf_path.is_file():
            log(f"no {conf_path}: this system has no device kit")
            return 0
        state = self.load_state()
        try:
            conf = parse_conf(conf_path.read_text())
            source = self.find_source(conf["source"], wait)
            if source is None:
                log(f"no filesystem {conf['source']}: no device kit to layer")
                return 0
            top = self.mount_kit(source)
            try:
                kit = self.load_kit(top)
            except FileNotFoundError:
                log(f"{conf['source']} holds no {KIT_DIR}/kit.json: no device kit to layer")
                return 0
            try:
                return self._apply(kit, state, reboot)
            finally:
                self.unmount_kit()
        except LayerError as exc:
            state["last_error"] = str(exc)
            self.save_state(state)
            log(f"error: {exc}")
            return 1

    def _apply(self, kit: Kit, state: dict, reboot: bool) -> int:
        compatible = read_compatible(self.paths.compatible)
        if not set(compatible) & set(kit.compatible):
            raise LayerError(f"the kit is for {kit.device} ({', '.join(kit.compatible)}); this device is "
                             f"{', '.join(compatible) or 'not described by a device tree'}")
        log(f"device kit {kit.device} {kit.version}: install {' '.join(kit.install) or '-'}; "
            f"replace {' '.join(kit.replace) or '-'} from {kit.repo_id}")
        status = self.status()
        deployments = status.get("deployments", [])
        booted = next((d for d in deployments if d.get("booted")), None)
        if booted is None:
            raise LayerError("rpm-ostree reports no booted deployment")
        if layer_in(*parse_origin(self.deployment_origin(booted)), kit):
            self.write_repo(kit, kit.repo_url)
            self.paths.state_dir.mkdir(parents=True, exist_ok=True)
            self.marker.write_text(f"{kit.device} {kit.version} {booted['checksum']}\n")
            state.update(status="layered", deployment=booted["checksum"], last_error=None)
            self.save_state(state)
            log(f"the booted deployment {booted['checksum'][:12]} carries the {kit.device} layer")
            shutil.rmtree(self.paths.state_dir / "repo", ignore_errors=True)
            return 0
        attempts = int(state.get("attempts", 0))
        if attempts >= MAX_ATTEMPTS:
            raise LayerError(f"{attempts} layered deployments were staged and none was booted "
                             "(rolled back or lost); not trying again. "
                             "`luma-device-layer apply --retry` starts over")
        staged = next((d for d in deployments if d.get("staged")), None)
        if staged is not None and set(kit.install) <= set(staged.get("requested-packages", [])):
            # An earlier run (stopped before it restarted) staged the layer.
            state.update(status="staged", staged=staged.get("checksum"), last_error=None)
            self.save_state(state)
            log(f"{staged.get('checksum', '')[:12]} with the {kit.device} layer is already staged")
            return self._restart(reboot)
        if staged is not None:
            raise LayerError("another update is staged; restart first")
        state.update(attempts=attempts + 1, status="layering", base=booted["checksum"], kit=kit.version)
        self.save_state(state)
        repo = self.stage_repo(kit)
        self.write_repo(kit, "file://" + "/" + repo.relative_to(self.paths.root).as_posix())
        self.set_repos_aside(f"{kit.repo_id}.repo")
        try:
            if kit.replace:
                self.check(["rpm-ostree", "override", "replace", "--experimental", "--from",
                            f"repo={kit.repo_id}", *kit.replace, *(f"--install={p}" for p in kit.install)],
                           "rpm-ostree override replace")
            else:
                self.check(["rpm-ostree", "install", "--idempotent", *kit.install], "rpm-ostree install")
        finally:
            self.restore_repos()
            self.write_repo(kit, kit.repo_url)
        pending = next((d for d in self.status().get("deployments", []) if d.get("staged")), None)
        if pending is None or not set(kit.install) <= set(pending.get("requested-packages", [])):
            raise LayerError("rpm-ostree staged no deployment with the device packages")
        state.update(status="staged", staged=pending.get("checksum"), last_error=None)
        self.save_state(state)
        log(f"staged {pending.get('checksum', '')[:12]} with the {kit.device} layer (attempt "
            f"{attempts + 1} of {MAX_ATTEMPTS})")
        return self._restart(reboot)

    def _restart(self, reboot: bool) -> int:
        if reboot:
            log("restarting into it")
            self.unmount_kit()
            self.check(["systemctl", "--no-block", "reboot"], "systemctl reboot")
        return 0

    def report(self) -> int:
        print(json.dumps({"layered": self.marker.exists(), **self.load_state()}, indent=2, sort_keys=True))
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-device-layer", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command")
    apply = sub.add_parser("apply", help="layer the device kit (the first-boot service)")
    apply.add_argument("--retry", action="store_true", help="forget earlier attempts and start over")
    apply.add_argument("--no-reboot", action="store_true", help="stage only; do not restart")
    sub.add_parser("status", help="print what has been done")
    args = parser.parse_args(argv)
    layer = DeviceLayer()
    if args.command == "status":
        return layer.report()
    if os.geteuid() != 0:
        print("luma-device-layer: run as root", file=sys.stderr)
        return 2
    return layer.apply(retry=getattr(args, "retry", False), reboot=not getattr(args, "no_reboot", False))
