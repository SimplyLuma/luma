# SPDX-License-Identifier: MPL-2.0
"""Photo workspace regression checks, run against the installed GTK/AppKit."""
import os
import tempfile
import time
from pathlib import Path

# Theme changes in this test must never modify the desktop settings.
os.environ["GSETTINGS_BACKEND"] = "memory"

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gio, GLib, Graphene, Gtk
from PIL import Image
from luma_darkroom.application import DarkroomApplication
from luma_darkroom.window import DarkroomWindow
from luma_darkroom.model import DocumentStore


def pump(seconds=.2):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending(): context.iteration(False)
        time.sleep(.005)


def wait(predicate):
    end = time.monotonic() + 20
    while not predicate():
        assert time.monotonic() < end, 'Preview timed out'
        pump(.05)


def capture(window, path):
    pump(.2)
    # A texture upload can invalidate allocation between GTK frame clocks.
    # Wait for the next complete snapshot instead of sampling that gap.
    for _ in range(20):
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(window).snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        if node is not None:
            window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, window.get_width(), window.get_height())).save_to_png(str(path))
            return
        pump(.05)
    raise AssertionError("Window never produced a drawable frame")



def main():
    output = Path(os.environ['DARKROOM_WORKSPACE_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='darkroom-workspace-') as directory:
        folder = Path(directory)
        photo = folder / 'Coastal light.png'
        other = folder / 'Second photo.png'
        # A generated fixture, never the user's photo library.
        image = Image.new('RGB', (1400, 900))
        image.putdata([(int(30+x/8), int(60+y/7), int(120+x/15)) for y in range(900) for x in range(1400)])
        image.save(photo)
        Image.new('RGB', (900, 1400), (120, 170, 80)).save(other)
        app = DarkroomApplication()
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        window = DarkroomWindow(app)
        window.store = DocumentStore(folder / 'recovery')
        window.set_default_size(1380, 880)
        window.present()
        pump()
        capture(window, output / 'empty.png')
        window.set_default_size(360, 740)
        pump(.3)
        window.set_default_size(360, 740)
        pump(.3)
        assert window.get_width() <= 360, window.get_width()
        capture(window, output / "empty-360.png")
        window.set_default_size(1380, 880)
        pump(.3)
        window.open_path(photo)
        wait(lambda: not window._render_pending and window._edited_preview is not None)
        for scheme, name in ((Adw.ColorScheme.FORCE_LIGHT, 'light'), (Adw.ColorScheme.FORCE_DARK, 'dark')):
            Gio.Settings.new("org.project_luma.shell-state").set_string("surface-treatment", name)
            pump(.2)
            assert Adw.StyleManager.get_default().get_dark() == (name == "dark")
            capture(window, output / f'{name}-wide.png')
        window.adjustment_exposure.set_value(.75)
        assert window.editor.can_undo
        window._action_undo()
        assert not window.document.raw_development
        window._action_redo()
        assert window.document.raw_development[0].value == .75
        window._apply_look("Warm", {"temperature": 18, "vibrance": 12})
        assert any(a.kind == "temperature" and a.value == 18 for a in window.document.raw_development)
        window._action_undo()
        assert not any(a.kind == "temperature" for a in window.document.raw_development)
        window._action_before_after()
        capture(window, output / 'compare.png')
        assert window.canvas_stack.get_visible_child_name() == 'compare'
        window._action_before_after()
        window._page_buttons['crop'].set_active(True)
        wait(lambda: not window._render_pending)
        capture(window, output / 'crop.png')
        window._page_buttons['retouch'].set_active(True)
        assert window.document.workspace.active_tool == "heal"
        window._select_tool("clone")
        assert window.document.workspace.active_tool == "clone"
        assert window._retouch_buttons["clone"].get_active()
        capture(window, output / "retouch.png")
        window._page_buttons['history'].set_active(True)
        capture(window, output / "history.png")
        window._page_buttons['adjust'].set_active(True)
        window._set_zoom(1)
        assert window.edited_picture.get_size_request().width == 1400
        window._fit_image()
        assert window.edited_picture.get_size_request().width == -1
        for width in (1024, 500, 420, 360):
            window.set_default_size(width, 740)
            pump(.3)
            window.set_default_size(width, 740)
            pump(.3)
            assert abs(window.get_width() - width) <= 10, (width, window.get_width())
            capture(window, output / f'width-{width}.png')
            if width < 860:
                window.compact_stack.set_visible_child_name('adjust')
                capture(window, output / f'edit-{width}.png')
                window.compact_stack.set_visible_child_name('image')
        # A failed new open keeps the current photo and edits; no modal dead end.
        broken = folder / 'broken.raf'
        broken.write_bytes(b'not a camera image')
        before = window.document
        window.open_path(broken)
        wait(lambda: not window._render_pending)
        assert window.document is before
        assert window.open_notice.get_revealed()
        assert broken not in window._filmstrip_paths
        window._open_notice_action()
        assert not window.open_notice.get_revealed()
        assert window.document.raw_development[0].value == .75
        # Switching photos must not silently discard an unsaved recipe or undo.
        window.open_path(other)
        window.open_path(photo)
        assert window.document.raw_development[0].value == .75
        assert window.editor.can_undo
        window.dirty = False
        wait(lambda: not window._render_pending)
        window._sessions.clear()
        window.close()
        pump()
    print('workspace: responsive widths, appearance, comparison, zoom, undo, photo switching passed')


if __name__ == '__main__': main()
