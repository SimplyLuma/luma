# SPDX-License-Identifier: Apache-2.0
"""Empty recent-file store must leave document chrome and totals quiet."""
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk
from luma_appkit import EmptyState, ListEmptyState, install_appkit
from luma_viewer.application import ViewerWindow


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


app = Adw.Application(application_id='org.projectluma.ViewerEmptyTest',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
install_appkit()
commands = []
with patch.object(ViewerWindow, '_choose_file', lambda *_: commands.append('open')), patch('luma_viewer.composition.Recents') as recents:
    recents.return_value.entries=[]
    recents.return_value.grouped.return_value=[]
    window = ViewerWindow(app)
    assert window.facts is None
    assert window.corner_slot.get_child() is None
    assert window.status_path.get_text() == window.status_detail.get_text() == ''
    assert window.files_count.get_text() == ''
    states = [w for w in descendants(window) if isinstance(w, EmptyState)]
    assert {w.heading.get_text() for w in states} == {'Nothing open'}
    main = next(w for w in states if w.heading.get_text() == 'Nothing open')
    main.primary_button.emit('clicked')
    assert commands == ['open']
    assert any(isinstance(w, ListEmptyState) and 'No recent files' in w.get_text()
               for w in descendants(window))
    window.destroy()
print('PASS: Viewer empty recents, compact information pane, quiet chrome/counts and existing Open command')
