# SPDX-License-Identifier: Apache-2.0
"""Test doubles for rpm-ostree, the network, power and HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luma_update import minisign  # noqa: E402
from luma_update.config import Paths, Settings  # noqa: E402
from luma_update.errors import TransactionError  # noqa: E402
from luma_update.http import HttpError  # noqa: E402

SECRET = bytes(range(32))
KEY_ID = bytes.fromhex("0123456789abcdef")
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit(n: int) -> str:
    return f"{n:064x}"


def release(version: str, n: int, *, released=NOW - 7 * 86400, start_percentage=1.0, duration=0,
            start=None, **extra) -> dict:
    return {
        "version": version, "commit": commit(n), "released_at": iso(released),
        "rollout": {"start_at": iso(released if start is None else start),
                    "start_percentage": start_percentage, "duration_minutes": duration},
        "paused": False, "deadend": False, "deadend_reason": None, "barrier": False,
        "importance": "normal", "notes_url": f"https://simplyluma.com/releases/{version}",
        "summary": f"Luma {version}", "download_bytes_estimate": 1000, **extra,
    }


def graph_doc(releases, *, channel="stable", arch="x86_64", generated=NOW - 3600) -> dict:
    return {"schema_version": 1, "channel": channel, "arch": arch, "generated_at": iso(generated),
            "releases": releases}


@dataclass
class FakeDeployment:
    checksum: str
    version: str
    origin: str
    booted: bool = False
    staged: bool = False
    base_checksum: str = ""
    id: str = ""
    osname: str = "luma"
    pinned: bool = False
    timestamp: int = 0
    layered: bool = False
    serial: int = 0
    kargs: str = "root=UUID=rig rw rhgb quiet"
    live_replaced: str = ""
    requested_packages: tuple = ()
    requested_local_packages: tuple = ()

    @property
    def base_commit(self) -> str:
        return self.base_checksum or self.checksum


class FakeRpmOstree:
    def __init__(self, booted_version="1.0.0", booted_commit=None, origin="luma:luma/1/x86_64/stable"):
        self.list = [FakeDeployment(booted_commit or commit(1), booted_version, origin, booted=True)]
        self._trees = tempfile.TemporaryDirectory()
        self.kargs_fail_with: Exception | None = None
        self.boot_menu_fail_with: Exception | None = None
        self.boot_menu_calls = []
        self.active = None
        self.calls = []
        self.fail_with: Exception | None = None
        self.versions: dict[str, str] = {}
        self.reloads = 0
        self.during_download = None

    def deployments(self):
        return list(self.list)

    def active_transaction(self):
        return self.active

    def reload(self, config=False):
        self.reloads += 1

    def update_deployment(self, *, revision, refspec, allow_downgrade, progress=None, message=None,
                          cancellable=None, cache_only=False, no_overrides=False, uninstall=(), **_):
        self.calls.append(("update", revision, refspec, allow_downgrade) + (("cache-only",) if cache_only else ())
                          + (("no-overrides",) if no_overrides else ())
                          + (("uninstall",) + tuple(uninstall) if uninstall else ()))
        if self.fail_with and not (cache_only and getattr(self, "cache_only_works", False)):
            raise self.fail_with
        if progress:
            progress(0.5, 100)
            if self.during_download:
                self.during_download()
            progress(0.6, 120)
            if cancellable is not None and cancellable.is_cancelled():
                raise TransactionError("Transaction was cancelled")
            progress(1.0, 200)
        booted = next(d for d in self.list if d.booted)
        origin = refspec or booted.origin
        self.list = [d for d in self.list if not d.staged]
        self.list.insert(0, FakeDeployment(revision, self.versions.get(revision, "?"), origin, staged=True,
                                           kargs=booted.kargs))

    # ── Kernel arguments ────────────────────────────────────────────────

    def tree(self, checksum: str) -> Path:
        path = Path(self._trees.name) / checksum
        path.mkdir(parents=True, exist_ok=True)
        return path

    def declare_kargs(self, checksum: str, name: str, text: str) -> None:
        """Put a /usr/lib/bootc/kargs.d file into a commit's tree."""
        directory = self.tree(checksum) / "usr/lib/bootc/kargs.d"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text)

    def deployment_root(self, deployment):
        return self.tree(deployment.checksum)

    def refresh_boot_menu(self, deployment):
        self.boot_menu_calls.append(deployment.checksum)
        if self.boot_menu_fail_with:
            raise self.boot_menu_fail_with
        return "luma-boot-hidden-menu: added the hidden-menu piece to /boot/grub2/grub.cfg"

    def boot_config(self, *, pending=True):
        if pending:
            target = next((d for d in self.list if d.staged), self.list[0])
        else:
            target = next(d for d in self.list if d.booted)
        return {"options": target.kargs, "staged": target.staged}

    def kernel_args(self, *, existing, append_if_missing=(), delete_if_present=(), message=None):
        self.calls.append(("kargs", tuple(append_if_missing), tuple(delete_if_present)))
        if self.kargs_fail_with:
            raise self.kargs_fail_with
        target = next(d for d in self.list if d.staged)
        assert existing == target.kargs, "rpm-ostree was given stale kernel arguments"
        args = target.kargs.split()
        args += [a for a in append_if_missing if a not in args]
        args = [a for a in args if a not in delete_if_present]
        self.list = [d for d in self.list if not d.staged]
        self.list.insert(0, replace(target, kargs=" ".join(args), serial=target.serial + 1))

    def cleanup_pending(self, message=None):
        self.calls.append(("cleanup",))
        self.list = [d for d in self.list if not d.staged]

    def rollback(self, message=None):
        self.calls.append(("rollback",))
        others = [d for d in self.list if not d.booted and not d.staged]
        booted = [d for d in self.list if d.booted]
        if not others:
            raise RuntimeError("no rollback")
        self.list = [others[0], booted[0]] + others[1:]

    def reboot_into(self, checksum: str) -> None:
        """Simulate a restart that boots ``checksum`` and keeps the others."""
        for item in self.list:
            item.booted = item.checksum == checksum
            item.staged = False
        self.list.sort(key=lambda d: not d.booted)


@dataclass
class Network:
    available: bool = True
    metered: bool | None = False
    connectivity: int = 4


@dataclass
class Power:
    available: bool = True
    on_battery: bool = False
    percentage: float | None = 100.0


class FakeProbes:
    def __init__(self):
        self.net = Network()
        self.pwr = Power()

    def network(self):
        return self.net

    def power(self):
        return self.pwr


class FakeHttp:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.posts: list[tuple[str, str, dict | None, dict]] = []
        self.post_status = 202
        self.offline = False
        self.responses: dict[str, tuple[int, dict]] = {}
        self.gets: list[str] = []

    def get(self, url, max_bytes, headers=None):
        self.gets.append(url)
        if self.offline:
            raise HttpError("offline")
        if url not in self.files:
            raise HttpError(f"{url} answered HTTP 404", 404)
        return self.files[url]

    def request_json(self, method, url, payload, headers=None, max_bytes=65536):
        if self.offline:
            raise HttpError("offline")
        self.posts.append((method, url, payload, dict(headers or {})))
        if url in self.responses:
            return self.responses[url]
        return self.post_status, {}


class Rig:
    """A temporary root with a signed graph published over FakeHttp."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = Paths(self.root)
        self.settings = Settings()
        self.http = FakeHttp()
        self.probes = FakeProbes()
        self.backend = FakeRpmOstree()
        self.now = NOW
        keydir = self.root / "usr/lib/luma-update/graph-keys.d"
        keydir.mkdir(parents=True)
        public, _ = minisign.sign_for_tests(SECRET, KEY_ID, b"", "x")
        (keydir / "luma-update-graph.pub").write_text(public)
        self.paths.wariness_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.wariness_file.write_text("0.5\n")

    def publish(self, doc: dict, channel="stable", secret=SECRET, key_id=KEY_ID) -> None:
        data = json.dumps(doc).encode()
        _, signature = minisign.sign_for_tests(secret, key_id, data, f"timestamp:{int(self.now)}\tfile:{channel}.json")
        url = self.settings.graph_url.format(channel=channel)
        self.http.files[url] = data
        self.http.files[url + ".minisig"] = signature.encode()
        for item in doc["releases"]:
            self.backend.versions[item["commit"]] = item["version"]

    def engine(self, **kwargs):
        from luma_update.engine import Engine
        return Engine(self.paths, self.settings, self.backend, self.probes, self.http,
                      clock=lambda: self.now, arch="x86_64", boot_id=lambda: kwargs.pop("boot", "boot-1"),
                      greenboot_installed=kwargs.pop("greenboot", True))

    def close(self):
        self.tmp.cleanup()
