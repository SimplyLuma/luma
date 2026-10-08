"""Dispatch optional compatibility reviews without executing package contents."""
from pathlib import Path
import subprocess

from .errors import InstallerError
from .model import kind_for_path


def dispatch_compatibility(path: Path) -> bool:
    kind = kind_for_path(path)
    target = {"android": "/usr/bin/luma-android-installer",
              "windows": "/usr/bin/luma-relay-installer"}.get(kind)
    if target is None:
        return False
    if not Path(target).is_file():
        raise InstallerError(f"The {kind.title()} compatibility service is not installed. "
                             "This package has not been run or installed.")
    try:
        subprocess.Popen([target, str(path)], start_new_session=True, close_fds=True)
    except OSError as error:
        raise InstallerError(f"The {kind.title()} compatibility service could not start: {error}. "
                             "This package has not been run or installed.") from error
    return True
