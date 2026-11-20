# SPDX-License-Identifier: GPL-3.0-only
"""Files dropped on the page reach it as a drag of those files.

GTK receives the drop (from Filer, through the document portal inside the
Flatpak); the page gets Chromium's own dragEnter, dragOver and drop with the
file paths, at the pointer's page position. A page's drop zone or upload
field sees an ordinary file drop. Only local files are passed; a drag the
page refuses changes nothing. chrome://extensions does not install a dropped
.crx this way: Chromium takes that path from its own window's drop.
"""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk

MAX_FILES = 64
COPY = 1


def local_paths(files):
    paths = []
    for item in files[:MAX_FILES]:
        path = item.get_path() if item is not None else None
        if path:
            paths.append(path)
    return paths


def drag_events(point, paths):
    """The page's drag: enter and over at the drop point, then the drop."""
    data = {'items': [], 'files': list(paths), 'dragOperationsMask': COPY}
    return [('Input.dispatchDragEvent', {'type': kind, **point, 'data': data})
            for kind in ('dragEnter', 'dragOver', 'drop')]


class PageDrop:
    def __init__(self, page_input):
        self.page_input = page_input
        self.target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        self.target.connect('drop', self._drop)
        page_input.page.add_controller(self.target)

    def _drop(self, _target, value, x, y):
        paths = local_paths(list(value.get_files()) if value is not None else [])
        point = self.page_input.point(x, y)
        if not paths or point is None:
            return False
        sent = False
        for method, params in drag_events(point, paths):
            sent = self.page_input.dispatch(method, params) or sent
        return sent
