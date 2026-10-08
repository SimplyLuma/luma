# SPDX-License-Identifier: Apache-2.0
"""Cold startup from the installed payload, never a source-tree module path."""
import os
from pathlib import Path
import tempfile
import time

expected = Path(os.environ['TIDE_INSTALLED_ROOT']).resolve()
with tempfile.TemporaryDirectory() as temp:
    for kind in ('CONFIG', 'DATA', 'CACHE', 'STATE'):
        path = Path(temp) / kind.lower()
        path.mkdir()
        os.environ[f'XDG_{kind}_HOME'] = str(path)
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import GLib
    import luma_tide
    assert Path(luma_tide.__file__).resolve().is_relative_to(expected), luma_tide.__file__
    from luma_tide.application import TideApplication
    from luma_tide.ui_phone import NowPlayingPhone, PhoneBar
    app = TideApplication()
    started = time.monotonic()
    assert app.register(None)
    app.activate()
    beat = []
    GLib.idle_add(lambda: beat.append(True) and False)
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if app.window is not None and app.window.get_mapped() and beat:
            break
        time.sleep(.01)
    assert app.window is not None and app.window.get_mapped(), 'installed Tide produced no mapped window'
    assert beat, 'GTK main loop did not respond after startup'
    print(f'PASS installed Tide startup, mapped window and heartbeat in {time.monotonic()-started:.3f}s')
    app.window.close()
    app.quit()
