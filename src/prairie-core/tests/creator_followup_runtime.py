#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual active Messages view, shared forms and native input on private stores."""
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from creator_preview_runtime import settle, screenshot
from gi.repository import Adw, Gdk, Gio, Gtk
from luma_appkit import Card, EmptyState, install_appkit, install_lumaui
from prairie_apps.messages_port import FixtureMessagesWindow
from prairie_apps.messages_dialog import MessagesDialog
from prairie_apps.messages import install_messages_theme


from native_input import outside_click


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for key in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME'):
            os.environ[key] = str(root / key)
        fixture = root / 'messages.json'
        fixture.write_text(json.dumps({'people': {}, 'conversations': []}))
        os.environ['LUMA_MESSAGES_FIXTURE'] = str(fixture)
        app = Adw.Application(application_id='org.projectluma.CreatorFollowup', flags=Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None)
        install_appkit(); install_lumaui()
        install_messages_theme()
        count = 0
        for dark in (False, True):
            Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment', 'dark' if dark else 'light')
            settle(lambda: Adw.StyleManager.get_default().get_dark() is dark)
            for width in (360, 500, 1024, 1440):
                window = FixtureMessagesWindow(app)
                window.set_default_size(width, 760)
                window.present()
                settle(lambda: window.get_surface().get_width() == width)
                view = window.surface
                assert view.current is None
                assert isinstance(view.empty, EmptyState) and view.empty.get_mapped()
                assert not view.header.get_visible() and not view.scroller.get_visible()
                assert view.center.state == 'hidden', view.center.state
                assert view.empty.primary_button.get_label() == 'Link Google Messages'
                assert view.empty.secondary_button.get_label() == 'Choose a Luma username'
                screenshot(window, f'messages-active-empty-{width}-{int(dark)}')
                dialog = MessagesDialog(title='Accounts')
                navigation = Adw.NavigationView()
                first = Adw.NavigationPage(title='Accounts', tag='accounts', child=Gtk.Label(label='Private test accounts'))
                second = Adw.NavigationPage(title='Google Messages', tag='network', child=Gtk.Entry(placeholder_text='Private test field'))
                navigation.add(first)
                dialog.set_child(navigation)
                closed = []
                dialog.connect('closed', lambda *_: closed.append(True))
                dialog.present(window)
                settle(lambda: dialog.get_mapped())
                assert isinstance(dialog, Card) and dialog.has_css_class('lumaui-card')
                assert dialog.get_width() <= window.get_width(), (width, dialog.get_width())
                navigation.push(second); settle()
                assert dialog.heading.title.get_text() == 'Google Messages'
                assert dialog.back_button.get_visible()
                dialog.back_button.emit('clicked'); settle()
                assert navigation.get_visible_page() is first
                assert not dialog.back_button.get_visible()
                settled = time.monotonic() + .35
                settle(lambda: time.monotonic() >= settled)
                screenshot(window, f'messages-account-card-{width}-{int(dark)}')
                dialog.close_button.emit('clicked'); settle(lambda: not dialog.get_mapped())
                assert closed == [True]
                if width >= 1024:
                    view.new_message(); settle(lambda: view._new_pop.get_mapped())
                    pop = view._new_pop
                    assert pop.get_autohide()
                    view.new_message(); settle(lambda: not pop.get_mapped())
                    assert view._new_pop is None
                    view.new_message(); settle(lambda: view._new_pop.get_mapped())
                    pop = view._new_pop
                    outside_click(window, window.get_width() - 50, 300)
                    settle(lambda: not pop.get_mapped())
                window.close(); settle()
                count += 1
        # The actual GIO launch path receives real URIs/files, even when no
        # luma-depot MIME default is configured in this private session. The
        # receivers record wire arguments; they do not simulate installation.
        from prairie_apps.notes_lumaui import NotesLumaWindow
        from luma_appkit import add_style_sheet
        core = Path(__file__).resolve().parents[1]
        add_style_sheet(os.environ.get('LUMA_NOTES_STYLE_PATH', str(core / 'style/notes.css')))
        applications = Path(os.environ['XDG_DATA_HOME']) / 'applications'
        applications.mkdir(parents=True)
        recorder = root / 'record-launch.py'
        recorder.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
        marker = root / 'depot-wire.json'
        (applications / 'org.projectluma.Depot.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Private Depot wire receiver\n'
            f'Exec=python3 {recorder} {marker} %U\nTerminal=false\n')
        try:
            absent = Gio.DesktopAppInfo.new('org.projectluma.Write.desktop')
        except TypeError:
            absent = None
        assert absent is None
        with patch.object(NotesLumaWindow, '_create_semantic_publisher', return_value=None), \
             patch.object(NotesLumaWindow, '_watch_store', return_value=None):
            notes = NotesLumaWindow(app)
            notes.set_default_size(1024, 760); notes.present(); settle()
            notes.create_note(); settle()
            saved = notes.store.get_note(notes.current.id)
            row = notes.note_rows[notes.current.id]
            assert row.depth == 0 and not row._expandable and not row.twisty.get_visible()
            success, mark = row.mark_button.compute_bounds(row)
            assert success and mark.get_x() < 35, mark.get_x()
            notes._open_in_write()
            settle(marker.exists)
            assert json.loads(marker.read_text()) == ['luma-depot://install/org.projectluma.Write']
            assert notes.store.get_note(saved.id) == saved
            marker = root / 'write-wire.json'
            (applications / 'org.projectluma.Write.desktop').write_text(
                '[Desktop Entry]\nType=Application\nName=Private Write wire receiver\n'
                f'Exec=python3 {recorder} {marker} %F\nTerminal=false\n')
            # GIO's desktop-directory monitor invalidates its cache on the
            # main loop. Wait for that real update before testing the newly
            # installed receiver; an immediate lookup races the monitor.
            def write_receiver_available():
                try:
                    return Gio.DesktopAppInfo.new('org.projectluma.Write.desktop') is not None
                except TypeError:
                    return False
            settle(write_receiver_available)
            notes._open_in_write(); settle(marker.exists)
            paths = json.loads(marker.read_text())
            assert len(paths) == 1 and Path(paths[0]).is_file()
            assert notes.store.get_note(saved.id) == saved
            screenshot(notes, 'notes-root-leaf-and-write-wire')
            notes.close(); settle()
        assert fixture.read_text() == json.dumps({'people': {}, 'conversations': []})
        print(f'PASS {count} active Messages widths/themes, shared modal navigation/close, native outside-click/toggle; real Notes GIO absent/present receivers and root-leaf geometry. Depot detail rendering/network linking/delivery are separate gates.')


if __name__ == '__main__':
    main()
