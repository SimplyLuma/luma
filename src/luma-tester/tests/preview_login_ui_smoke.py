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
if os.environ.get('TRY_KIT_SOURCE'):
    import sys
    sys.path.insert(0, os.environ['TRY_KIT_SOURCE'])
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from luma_tester import gui, image_library
from luma_tester.core import Session
from luma_tester.downloads import host_arch


def main():
    failures = []
    phase = 0
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
            nonlocal phase
            app = Gtk.Application.get_default()
            if not app or not app.get_active_window(): return True
            try:
                if phase == 0:
                    app.window.set_size_request(1200, 900)
                    app.window.set_default_size(1200, 900)
                    for selection in (None, path):
                        with patch.object(app, 'select_image', return_value=selection):
                            for mode in ('try', 'develop'):
                                app.select_intent(mode); app.refresh()
                                assert all(row.get_visible() for row in app.preview_rows), (selection, mode)
                    app.select_intent('try'); app.refresh()
                    app.layout.set_show_sidebar(True)
                    phase = 1
                    return True
                if phase == 1:
                    app.adapt_workspace(app.window, None)
                    app.layout.set_show_sidebar(True)
                    phase = 2
                    return True
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
                assert app.preview_rows[0].has_css_class('divided')
                assert not app.preview_rows[0].has_css_class('ruled')
                def label(widget, text):
                    if isinstance(widget, Gtk.Label) and widget.get_text() == text: return widget
                    child = widget.get_first_child()
                    while child:
                        found = label(child, text)
                        if found is not None: return found
                        child = child.get_next_sibling()
                    return None
                def rect(widget):
                    valid, bounds = widget.compute_bounds(app.sidebar)
                    assert valid
                    return bounds
                user = rect(label(app.preview_rows[1], 'Username'))
                preview = rect(label(app.preview_rows[0], 'Preview login'))
                presets = rect(label(app.preset_heading, 'Presets'))
                empty = rect(label(app.preset_rows[0], 'No presets'))
                assert abs((user.get_y()-preview.get_y()-preview.get_height()) -
                           (empty.get_y()-presets.get_y()-presets.get_height())) <= 1
                username = rect(label(app.preview_rows[1], 'luma'))
                password = rect(app.preview_password.label_widget)
                assert abs((username.get_x()+username.get_width()) -
                           (password.get_x()+password.get_width())) <= 1
                if os.environ.get('DEV_ARTIFACT_DIR'):
                    import json
                    from pathlib import Path
                    Path(os.environ['DEV_ARTIFACT_DIR'], 'sidebar-allocation.json').write_text(json.dumps(dict(
                        preview_to_username=user.get_y()-preview.get_y()-preview.get_height(),
                        presets_to_empty=empty.get_y()-presets.get_y()-presets.get_height(),
                        username_right=username.get_x()+username.get_width(),
                        password_right=password.get_x()+password.get_width())))
                assert app.preview_password.get_focusable()
                app.preview_password.grab_focus()
                assert app.window.get_focus() is app.preview_password, (app.preview_password.get_mapped(), app.preview_password.get_sensitive(), app.window.get_focus())
                assert not any(isinstance(child, Gtk.Image) for child in [app.preview_password.get_child()])
                app.preview_password.emit('clicked')
                clipboard = app.window.get_clipboard()
                def copied(source, result):
                    try:
                        assert source.read_text_finish(result) == 'TryLuma'
                    except BaseException as exc:
                        failures.append(exc)
                    def finish():
                        try:
                            if os.environ.get('DEV_ARTIFACT_DIR'):
                                from gi.repository import Graphene
                                paintable = Gtk.WidgetPaintable.new(app.window)
                                snapshot = Gtk.Snapshot()
                                w, h = app.window.get_width(), app.window.get_height()
                                paintable.snapshot(snapshot, float(w), float(h))
                                node = snapshot.to_node()
                                assert node is not None
                                bounds = Graphene.Rect(); bounds.init(0, 0, w, h)
                                texture = app.window.get_renderer().render_texture(node, bounds)
                                assert texture.save_to_png(os.path.join(os.environ['DEV_ARTIFACT_DIR'], 'sidebar-spacing.png'))
                        except BaseException as exc:
                            failures.append(exc)
                        app.quit()
                        return False
                    GLib.timeout_add(350, finish)
                clipboard.read_text_async(None, copied)
                # An arbitrary imported installer must not advertise credentials.
                (session.directory/'setup.json').unlink()
                with patch.object(app, 'select_image', return_value=path):
                    app.refresh()
                    assert not any(row.get_visible() for row in app.preview_rows)
                app.refresh()
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
