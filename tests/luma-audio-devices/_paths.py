# SPDX-License-Identifier: MPL-2.0
"""Make the component importable from the repository or an RPM build tree."""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
for candidate in (HERE.parent, HERE.parents[1] / "src" / "luma-audio-devices"):
    if (candidate / "luma_audio_devices").is_dir():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        COMPONENT = candidate
        break
else:  # pragma: no cover
    raise ImportError("luma_audio_devices is not next to the tests")


def have_gi() -> bool:
    try:
        import gi  # noqa: F401
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True
