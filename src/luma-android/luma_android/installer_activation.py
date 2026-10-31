# SPDX-License-Identifier: Apache-2.0
"""Activate the graphical installer outside the hardened broker's sandbox."""
from pathlib import Path
import os
import subprocess
import uuid

from .errors import LumaAndroidError


def launch_installer(path: Path) -> None:
    # systemd --user owns the graphical application's context. A direct fork
    # inherits the broker's read-only home, private /tmp and NoNewPrivileges,
    # which prevents normal staging and the authorized package helper. Keep
    # those broker restrictions; do not move package installation into it.
    command = [
        '/usr/bin/systemd-run', '--user', '--quiet', '--collect',
        '--unit=luma-android-installer-' + uuid.uuid4().hex,
        '--property=Type=exec',
    ]
    # The user manager already owns the session bus/runtime environment. Carry
    # only the current display and activation token, never the daemon's entire
    # inherited environment or arbitrary manager properties.
    for name in ('WAYLAND_DISPLAY', 'DISPLAY', 'XDG_ACTIVATION_TOKEN'):
        value = os.environ.get(name)
        if value:
            command.append('--setenv=' + name + '=' + value)
    command.extend(['/usr/bin/luma-android-installer', str(path)])
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise LumaAndroidError('The Android installer could not open. Please try again.') from error
