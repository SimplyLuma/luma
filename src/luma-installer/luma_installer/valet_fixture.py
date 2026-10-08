"""The v70 Valet sample, kept entirely in memory for LUMA_VALET_FIXTURE.

This module has no installer, registry, Flatpak, Android, or filesystem-store
imports. The only file it reads is the explicitly named JSON fixture.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


class ValetFixture:
    def __init__(self, path: str | Path):
        sample = json.loads(Path(path).read_text(encoding="utf-8"))
        self.apps = {app["id"]: app for app in sample["apps"]}
        self.installed = {app["id"]: app for app in sample["installed"]}
        self.initial = deepcopy(sample["initial"])
        self.reset()

    def reset(self) -> None:
        self.view = self.initial["view"]
        self.app_id = self.initial["app"]
        self.remove_id: str | None = None
        self.keep = self.initial["keep"]
        self.sandbox = self.initial["sandbox"]
        self.permissions: dict[tuple[str, int], bool] = {}
        self.progress = 0
        self.step = ""
        self.done = False
        self.removed = False
        self.advanced = False

    @property
    def app(self) -> dict:
        return self.apps[self.app_id]

    @property
    def removal(self) -> dict:
        if self.remove_id is None:
            raise ValueError("No app selected for removal")
        return self.installed[self.remove_id]

    def open_app(self, app_id: str) -> None:
        if app_id not in self.apps:
            raise KeyError(app_id)
        self.view, self.app_id = "install", app_id
        self.advanced = self.done = False
        self.progress = 0
        self.sandbox = True

    def open_removal(self, app_id: str) -> None:
        if app_id not in self.installed:
            raise KeyError(app_id)
        self.view, self.remove_id = "remove", app_id
        self.keep, self.removed = True, False

    def permission_allowed(self, index: int) -> bool:
        return self.permissions.get((self.app_id, index), bool(self.app["perms"][index][2]))

    def toggle_permission(self, index: int) -> bool:
        if self.app["fmt"] not in {"flatpak", "apk", "exe"}:
            raise ValueError("This format has no editable permissions in v70")
        allowed = not self.permission_allowed(index)
        self.permissions[(self.app_id, index)] = allowed
        return allowed

    def removed_bytes_mb(self) -> int:
        return sum(int(size) for _name, size, goes in self.removal["parts"] if goes or not self.keep)

    def finish_removal(self) -> None:
        self.removed = True

