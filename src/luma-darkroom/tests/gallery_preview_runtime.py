# SPDX-License-Identifier: Apache-2.0
"""Gallery access and asynchronous preview regressions, using disposable photos."""
import os
os.environ['GSETTINGS_BACKEND'] = 'memory'
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from workspace_runtime import pump, wait, capture
from gi.repository import Gio
from luma_darkroom.application import DarkroomApplication
from luma_darkroom.window import DarkroomWindow
from luma_darkroom.engine import RasterEngine
from luma_darkroom.model import DocumentStore


def main():
    with tempfile.TemporaryDirectory(prefix='darkroom-gallery-') as directory:
        folder = Path(directory)
        for index in range(30):
            Image.new('RGB', (1400, 900), (40 + index * 4, 70, 100)).save(folder / f'Photo {index:02}.png')
        (folder / 'notes.txt').write_text('Not a photograph')
        (folder / 'nested').mkdir()
        Image.new('RGB', (40, 20)).save(folder / 'nested' / 'excluded.png')
        app = DarkroomApplication(); app.set_flags(Gio.ApplicationFlags.NON_UNIQUE); app.register(None)
        window = DarkroomWindow(app)
        window.store = DocumentStore(folder / 'recovery')
        window.present(); pump()
        assert window.actions['gallery'].get_enabled()
        assert window.actions['open-folder'].get_enabled()
        assert window.filmstrip_pane.get_visible()
        window._browse_folder(folder)
        wait(lambda: len(window._filmstrip_paths) == 30)
        assert window.gallery_revealer.get_reveal_child()
        assert len(window._gallery_pictures) == 24
        window._gallery_page_changed(1)
        assert len(window._gallery_pictures) == 6
        assert window.gallery_previous.get_sensitive()
        assert not window.gallery_next.get_sensitive()
        window._gallery_page_changed(-1)
        photo = folder / 'Photo 00.png'
        window._open_gallery_photo(photo)
        wait(lambda: not window._render_pending and window._edited_preview is not None)
        original = window._edited_preview.tobytes()
        prepared = window._prepared_source
        render = RasterEngine.render
        entered, release, second_entered, second_release = (threading.Event() for _ in range(4))
        calls = []
        def controlled(engine, *args, **kwargs):
            calls.append(engine.document.raw_development[0].value)
            if len(calls) == 1:
                entered.set(); assert release.wait(10)
            elif len(calls) == 2:
                second_entered.set(); assert second_release.wait(10)
            return render(engine, *args, **kwargs)
        with patch.object(RasterEngine, 'render', controlled):
            window.adjustment_exposure.set_value(.25)
            wait(entered.is_set)
            assert not window.render_spinner.get_visible()
            window.adjustment_exposure.set_value(.5)
            window.adjustment_exposure.set_value(.75)
            assert len(calls) == 1, 'More than one preview worker ran'
            release.set()
            wait(second_entered.is_set)
            assert window._edited_preview.tobytes() != original, 'Completed intermediate frame discarded during drag'
            assert calls[:2] == [.25, .75], calls
            second_release.set()
            wait(lambda: not window._render_pending and not window._settle_source)
        assert window._prepared_source is prepared, 'Source decoded during adjustment'
        expected = RasterEngine(window.document).render(max_dimension=1800)
        assert window._edited_preview.tobytes() == expected.image.tobytes()
        window._action_undo()
        assert not window.document.raw_development, 'One drag must undo as one edit'
        window._action_redo()
        wait(lambda: not window._render_pending)
        # Never publish an old photo's frame into a newly selected photo.
        entered.clear(); release.clear()
        def delayed(engine, *args, **kwargs):
            if engine.document.source.uri == photo.as_uri():
                entered.set(); assert release.wait(10)
            return render(engine, *args, **kwargs)
        with patch.object(RasterEngine, 'render', delayed):
            window.adjustment_exposure.set_value(1)
            wait(entered.is_set)
            other = folder / 'Photo 01.png'
            window._open_gallery_photo(other)
            assert window._edited_preview is None
            release.set()
            wait(lambda: not window._render_pending and window._edited_preview is not None)
            assert window._edited_preview.getpixel((0, 0))[:3] == (44, 70, 100)
        window._open_gallery_photo(photo)
        assert window.document.raw_development[0].value == 1
        wait(lambda: not window._render_pending)
        output = Path(os.environ['DARKROOM_WORKSPACE_OUTPUT'])
        for width in (1380, 1024, 500, 360):
            window.set_default_size(width, 880 if width > 1024 else 740); pump(.2)
            window.set_default_size(width, 880 if width > 1024 else 740); pump(.2)
            assert abs(window.get_width()-width) <= 10, (width, window.get_width(), window.filmstrip.measure(0, -1))
            assert window.filmstrip_pane.get_mapped()
            capture(window, output / f'gallery-{width}.png')
            if width < 860:
                window.compact_stack.set_visible_child_name('adjust'); pump()
                assert window.filmstrip_pane.get_mapped()
                window._action_gallery(); pump()
                assert not window.gallery_revealer.get_reveal_child()
                assert window.gallery_toggle.get_mapped()
                window._action_gallery(); pump()
                window._open_gallery_photo(other)
                assert window.compact_stack.get_visible_child_name() == 'image'
                wait(lambda: not window._render_pending)
        # Quit must run close-request, including edits made immediately before it.
        current_id = window.document.id
        window.editor.set_adjustment('exposure', .2)
        window.dirty = True
        app._quit(); pump()
        recovered = [document for _path, document, _metadata in window.store.recoverable()]
        assert any(document.id == current_id and document.raw_development[0].value == .2 for document in recovered)
        assert window._closing
    print('gallery/preview: paging, narrow Edit access, intermediate frames, latest recipe, source reuse, undo and photo-switch race passed')

if __name__ == '__main__': main()
