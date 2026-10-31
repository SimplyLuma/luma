# SPDX-License-Identifier: Apache-2.0
"""One-way requests to present a native panel; no settings mutation over Binder."""
from __future__ import annotations

import time
import os
import uuid

from .engine import _validate_package_name
from .errors import LumaAndroidError

INTERFACE = "org.projectluma.android.IAppSettings"
SERVICE = "lumaappsettings"


def launch_command(package, environment=None):
    _validate_package_name(package)
    environment = os.environ if environment is None else environment
    # Leave the session transport's read-only home and other service sandbox
    # restrictions intact. The ordinary user manager owns this native window.
    command = ["/usr/bin/systemd-run", "--user", "--quiet", "--collect",
               "--unit=luma-android-app-settings-" + uuid.uuid4().hex,
               "--property=Type=exec"]
    for name in ("WAYLAND_DISPLAY", "DISPLAY"):
        if environment.get(name):
            command.append("--setenv=" + name + "=" + environment[name])
    return command + ["/usr/bin/luma-android-settings", "--app", package]


class AppSettingsRequests:
    def __init__(self, present, *, clock=time.monotonic):
        self.present, self.clock = present, clock
        self.last_request = None

    def receive(self, request, code):
        # Credentials originate in the Binder driver. This ABI is for the
        # current system-UID task HAL, not ordinary Android app callers.
        if request.sender_euid() != 1000:
            return -1
        if code != 1:
            return -38
        package = request.init_reader().read_string16()
        if not isinstance(package, str) or len(package) > 255:
            return -22
        try:
            _validate_package_name(package)
        except LumaAndroidError:
            return -22
        now = self.clock()
        if self.last_request is not None and now - self.last_request < 1:
            return -11
        self.last_request = now
        self.present(package)
        return 0


def export(manager, glib):
    def present(package):
        def launch():
            from gi.repository import Gio
            # A fixed executable and argv; the request cannot name a command,
            # URI, file, preference value or privileged operation.
            try:
                process = Gio.Subprocess.new(launch_command(package),
                                   Gio.SubprocessFlags.NONE)
                def finished(child, result):
                    try:
                        child.wait_check_finish(result)
                    except glib.Error:
                        pass
                process.wait_check_async(None, finished)
            except glib.Error:
                pass
            return glib.SOURCE_REMOVE
        glib.idle_add(launch)

    requests = AppSettingsRequests(present)
    local = None

    def handle(request, code, _flags):
        reply = local.new_reply()
        try:
            status = requests.receive(request, code)
        except Exception:
            status = -22
        return reply, status

    local = manager.new_local_object(INTERFACE, handle)
    return local, requests
