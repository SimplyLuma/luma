# SPDX-License-Identifier: Apache-2.0

"""Installation, before there is anything to install from.

This is a stub and says so: it writes a record of what it pretends to have
installed and never claims a real Flatpak appeared. What it does faithfully is
*behave* like an installation — it takes time proportional to the download, it
reports real fractions and byte counts, it can be cancelled halfway, it checks
for room first, and it can fail.

A libflatpak implementation replaces this file. It must keep four promises the
window depends on:

1. progress arrives repeatedly, with `transferred_bytes` and `total_bytes`;
2. cancelling stops the work and leaves nothing installed;
3. `free_bytes()` answers for the filesystem apps land on;
4. `remove()` honours `keep_data` rather than deciding for the user.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import time

from .providers import (
    App, FaultInjector, InstallationProvider, InstalledApp, Progress,
    ProviderError, run_async,
)


def state_path() -> pathlib.Path:
    root = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
    return root / "luma-depot/installed.json"


# What the placeholder machine starts with, so Depot is not empty on first run.
# These are the applications the rest of the simulator shows as installed.
_SEED = {
    "io.luma.Reel": ("0.1.0", 240_000_000, "", 0, ""),
    "io.luma.Studio": ("0.1.0", 205_000_000, "", 0, ""),
    "io.luma.Lattice": ("1.1.0", 190_000_000, "1.2.0", 94_000_000,
                        "Opens very large projects faster, and there is a new way to "
                        "compare changes side by side."),
    "io.luma.Bellwether": ("2.2.0", 120_000_000, "2.3.0", 18_000_000,
                           "Alerts can now be grouped by which machine sent them."),
    "io.luma.Keyring": ("5.0.1", 200_000_000, "", 0, ""),
}


class SimulatedInstallationProvider(InstallationProvider):
    def __init__(self, faults: FaultInjector | None = None, *, state_file=None) -> None:
        self.faults = faults or FaultInjector()
        self.path = pathlib.Path(state_file) if state_file is not None else state_path()
        self._records: dict[str, InstalledApp] = {}
        self._load()

    # ── State that survives a restart ────────────────────────────────────

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {app_id: {"version": version, "installed_bytes": size,
                             "update_version": update, "update_bytes": update_size,
                             "update_summary": summary}
                    for app_id, (version, size, update, update_size, summary) in _SEED.items()}
            self._records = {}
            self._write(data)
        self._records = {}
        for app_id, entry in data.items():
            try:
                self._records[app_id] = InstalledApp(
                    app_id=app_id,
                    version=str(entry.get("version", "")),
                    installed_bytes=int(entry.get("installed_bytes", 0)),
                    update_version=str(entry.get("update_version", "")),
                    update_bytes=int(entry.get("update_bytes", 0)),
                    update_summary=str(entry.get("update_summary", "")),
                )
            except (TypeError, ValueError):
                continue

    def _write(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _save(self) -> None:
        self._write({
            record.app_id: {
                "version": record.version,
                "installed_bytes": record.installed_bytes,
                "update_version": record.update_version,
                "update_bytes": record.update_bytes,
                "update_summary": record.update_summary,
            }
            for record in self._records.values()
        })

    # ── The interface ────────────────────────────────────────────────────

    def installed(self, callback, cancellable=None) -> None:
        def work():
            time.sleep(0.15)
            return tuple(sorted(self._records.values(), key=lambda item: item.app_id))

        run_async(work, callback, cancellable)

    def free_bytes(self) -> int:
        if self.faults.enabled("disk-full"):
            return 12_000_000
        try:
            usage = shutil.disk_usage(pathlib.Path.home())
            return int(usage.free)
        except OSError:
            return 0

    def install(self, app: App, on_progress, callback, cancellable=None) -> None:
        total = app.download_bytes or 40_000_000
        if self.free_bytes() < app.installed_bytes:
            def refuse():
                raise ProviderError(
                    "not enough room",
                    hint=f"{app.name} needs {_megabytes(app.installed_bytes)} and there "
                         f"is {_megabytes(self.free_bytes())} free.",
                    recoverable=False,
                )
            run_async(refuse, callback, cancellable)
            return

        def work():
            self._transfer(app.app_id, total, on_progress, cancellable,
                           fail_at=0.62 if self.faults.enabled("install-fail") else None)
            record = InstalledApp(app_id=app.app_id, version=app.version,
                                  installed_bytes=app.installed_bytes or total)
            self._records[app.app_id] = record
            self._save()
            return record

        run_async(work, callback, cancellable)

    def update(self, app_id: str, on_progress, callback, cancellable=None, *, expected_commit="", expected_installed_commit="") -> None:
        record = self._records.get(app_id)
        if record is None or not record.has_update:
            def refuse():
                raise ProviderError("nothing to update",
                                    hint="That application is already up to date.")
            run_async(refuse, callback, cancellable)
            return

        def work():
            self._transfer(app_id, record.update_bytes, on_progress, cancellable,
                           fail_at=0.62 if self.faults.enabled("install-fail") else None)
            updated = InstalledApp(app_id=app_id, version=record.update_version,
                                   installed_bytes=record.installed_bytes)
            self._records[app_id] = updated
            self._save()
            return updated

        run_async(work, callback, cancellable)

    def remove(self, app_id: str, *, keep_data: bool, callback, cancellable=None) -> None:
        def work():
            time.sleep(0.4)
            if app_id not in self._records:
                raise ProviderError("not installed",
                                    hint="That application is not installed.")
            del self._records[app_id]
            self._save()
            # A real provider deletes the app's data directory only when
            # keep_data is false. The stub records the decision rather than
            # silently doing either.
            return {"app_id": app_id, "kept_data": keep_data}

        run_async(work, callback, cancellable)

    # ── The part that behaves like a transfer ────────────────────────────

    def _transfer(self, app_id: str, total: int, on_progress, cancellable,
                  fail_at: float | None) -> None:
        from gi.repository import GLib

        total = max(total, 1)
        chunk = max(total // 40, 1)
        moved = 0
        slow = 3.0 if self.faults.enabled("slow") else 1.0
        while moved < total:
            if cancellable is not None and cancellable.is_cancelled():
                raise ProviderError("cancelled", hint="")
            time.sleep(0.05 * slow)
            moved = min(moved + chunk, total)
            fraction = moved / total
            if fail_at is not None and fraction >= fail_at:
                raise ProviderError(
                    "the transfer failed",
                    hint="The download stopped partway through. Nothing was installed.",
                )
            stage = "Downloading" if fraction < 0.85 else "Installing"
            GLib.idle_add(on_progress, Progress(app_id, fraction, moved, total, stage))


def _megabytes(value: int) -> str:
    return f"{round(value / 1_000_000)} MB"
