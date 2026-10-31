# SPDX-License-Identifier: Apache-2.0
"""Application-scoped input compatibility, including older runtime images."""
from __future__ import annotations

import fnmatch

from .engine import _validate_package_name
from .errors import LumaAndroidError, RuntimeUnavailableError

LEGACY_TOUCH = "persist.waydroid.fake_touch"
TOUCH_PREFIX = "persist.luma.app.touch."
CAPABILITY = "ro.luma.per_app_touch"


class AppPreferences:
    def __init__(self, engine, package: str):
        _validate_package_name(package)
        self.engine, self.package = engine, package

    def _installed(self):
        if self.package not in self.engine.live_launchable_packages():
            raise LumaAndroidError("This application is no longer installed.")

    def _get(self, name):
        return self.engine._run("prop", "get", name, timeout=10).stdout.strip()

    def touch(self) -> bool:
        self._installed()
        if self._get(CAPABILITY) == "1":
            override = self._get(TOUCH_PREFIX + self.package)
            if override in ("true", "false"):
                return override == "true"
        return any(fnmatch.fnmatchcase(self.package, pattern)
                   for pattern in self._get(LEGACY_TOUCH).split(",") if pattern)

    def set_touch(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("Touch compatibility must be enabled or disabled.")
        with self.engine.lifecycle_lock:
            self._installed()
            if self._get(CAPABILITY) == "1":
                key, value = TOUCH_PREFIX + self.package, str(enabled).lower()
            else:
                # Retain every unrelated rule. Never turn off a wildcard for
                # other applications to implement one application's override.
                key = LEGACY_TOUCH
                rules = [item for item in self._get(key).split(",") if item]
                if not enabled and any(item != self.package and
                        fnmatch.fnmatchcase(self.package, item) for item in rules):
                    raise LumaAndroidError(
                        "A shared compatibility rule enables touch for this app. "
                        "Update the Android runtime to override it individually.")
                rules = [item for item in rules if item != self.package]
                if enabled:
                    rules.append(self.package)
                value = ",".join(rules)
                if len(value.encode()) > 91:
                    raise LumaAndroidError(
                        "This older Android runtime has reached its compatibility "
                        "limit. Update it to save more app preferences.")
            if self._get(key) != value:
                self.engine._run("prop", "set", key, value, timeout=10)
            if self._get(key) != value:
                raise RuntimeUnavailableError("The application preference was not saved.")
