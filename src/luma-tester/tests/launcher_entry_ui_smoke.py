# SPDX-License-Identifier: Apache-2.0
"""Start through the GUI entry without constructing a Gtk widget beforehand."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

if os.environ.get('TRY_UI_SOURCE'):
    sys.path.insert(0, os.environ['TRY_UI_SOURCE'])
os.environ['GSETTINGS_BACKEND'] = 'memory'
from luma_tester import gui, image_library
from luma_tester.core import TesterError
from gi.repository import Gdk, GLib, Gtk


def main():
    failures = []
    seen = []
    def finish():
        app = Gtk.Application.get_default()
        try:
            assert app and app.get_active_window()
            assert Gdk.Display.get_default() is not None
            seen.append(True)
        except BaseException as exc:
            failures.append(exc)
        finally:
            if app:app.quit()
        return False
    with tempfile.TemporaryDirectory(prefix='try-entry-ui-') as name, \
            patch('luma_tester.updates.check', return_value=None), \
            patch.object(image_library, 'refresh', return_value=[]):
        GLib.timeout_add(1500, finish)
        result = gui.launch(Path(name)/'state')
    if failures:raise failures[0]
    assert seen and result == 0
    with patch.object(Gtk, 'init_check', return_value=False):
        try:gui.launch()
        except TesterError as exc:assert 'desktop session' in str(exc)
        else:raise AssertionError('Missing display was not handled explicitly')
    print('Plain launcher entry initializes the display; missing display is reported: PASS')


if __name__ == '__main__':main()
