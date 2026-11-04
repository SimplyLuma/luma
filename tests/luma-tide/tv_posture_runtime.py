# SPDX-License-Identifier: Apache-2.0
"""TV posture switches Tide's presentation live, and leaves the desktop and phone ones as they were.

At every manifest test width the window opens its usual presentation (no TV
stage, the attached deck, the island); turning TV posture on shows the TV
presentation in the same window over the same library and player; turning it
off brings the desktop body back. Needs a GTK display (any backend) and LumaUI.

    PYTHONPATH=src/luma-platform/appkit:src/luma-tide python3 tests/luma-tide/tv_posture_runtime.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.pop('LUMA_POSTURE', None)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lumaui_runtime import pump, test_application  # noqa: E402
from luma_tide.fixture import FixtureLibrary  # noqa: E402
from luma_tide.ui import TideWindow  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WIDTHS = (360, 500, 1024, 1280)


def settle(seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump()
        time.sleep(0.01)


with test_application('org.projectluma.TideTvPostureTest') as (app, _root):
    for width in WIDTHS:
        window = TideWindow(app, FixtureLibrary(REPO / 'tests/fixtures/tide-v70.json'))
        window.set_default_size(width, 720)
        window.present()
        settle()
        assert window.tv is None and not window.tv_posture, width
        assert window.root.get_parent() is window.body, width
        desktop_view = window.view
        window.tv_posture = True
        settle()
        assert window.tv is not None and window.tv.stage.get_parent() is window.body, width
        assert window.root.get_parent() is None and not window.deck.get_visible(), width
        assert window.tv.library is window.library and window.tv.source is window.source, width
        window.tv_posture = False
        settle()
        assert window.tv is None and window.root.get_parent() is window.body, width
        assert window.view == desktop_view, width
        window.close_resources()
        window.destroy()
        settle(0.1)
print('tv posture runtime: PASS at', ', '.join(map(str, WIDTHS)))
