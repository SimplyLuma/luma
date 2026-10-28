# SPDX-License-Identifier: Apache-2.0
"""Check native sidebar credentials for fresh and legacy managed previews."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

if os.environ.get('TRY_UI_SOURCE'):
    import sys
    sys.path.insert(0, os.environ['TRY_UI_SOURCE'])
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from luma_tester import gui, image_library
from luma_tester.core import Session
from luma_tester.downloads import host_arch


def main():
    failures = []
    with tempfile.TemporaryDirectory(prefix='try-preview-login-ui-') as directory:
        root = Path(directory)
        catalog = root/'catalog'; catalog.mkdir()
        os.environ['LUMA_TESTER_CATALOG'] = str(catalog)
        path = root/'legacy.json'
        path.write_text(json.dumps(dict(schema=1, id='legacy-preview', arch=host_arch(),
            target='desktop', boot='uefi', media='iso', cloud_init=False, user='luma',
            build='UI fixture', image=dict(url=(root/'image.iso').as_uri(), sha256='a'*64, bytes=1))))
        session = Session(path, root/'state')
        session.directory.mkdir(parents=True)
        (session.directory/'workspace.qcow2').touch()
        (session.directory/'setup.json').write_text(json.dumps(dict(phase='ready', sha256='a'*64)))

        def inspect():
            app = Gtk.Application.get_default()
            if not app or not app.get_active_window(): return True
            try:
                for selection in (None, path):
                    with patch.object(app, 'select_image', return_value=selection):
                        for mode in ('try', 'develop'):
                            app.select_intent(mode); app.refresh()
                            assert all(row.get_visible() for row in app.preview_rows), (selection, mode)
                def texts(widget):
                    found = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
                    child = widget.get_first_child()
                    while child:
                        found.extend(texts(child)); child = child.get_next_sibling()
                    return found
                labels = texts(app.sidebar)
                assert 'luma' in labels and 'TryLuma' in labels
                assert not any('Use this to unlock' in label for label in labels)
                assert len(app.preview_rows) == 3
                assert not app.preview_rows[0].has_css_class('ruled')
                assert app.preview_password.get_focusable()
                app.preview_password.grab_focus()
                assert app.window.get_focus() is app.preview_password
                assert not any(isinstance(child, Gtk.Image) for child in [app.preview_password.get_child()])
                app.preview_password.emit('clicked')
                clipboard = app.window.get_clipboard()
                def copied(source, result):
                    try:
                        assert source.read_text_finish(result) == 'TryLuma'
                    except BaseException as exc:
                        failures.append(exc)
                    app.quit()
                clipboard.read_text_async(None, copied)
                # An arbitrary imported installer must not advertise credentials.
                (session.directory/'setup.json').unlink()
                with patch.object(app, 'select_image', return_value=path):
                    app.refresh()
                    assert not any(row.get_visible() for row in app.preview_rows)
            except BaseException as exc:
                failures.append(exc)
                app.quit()
            return False

        with patch('luma_tester.updates.check', return_value=None), patch.object(image_library, 'refresh', return_value=[]), patch.object(image_library, 'cached', return_value=[]):
            GLib.timeout_add(100, inspect)
            result = gui.launch(root/'state')
        if failures: raise failures[0]
        assert result == 0
        print('Preview login visible in Try and Developer for fresh and installed legacy workspaces: PASS')


if __name__ == '__main__': main()
