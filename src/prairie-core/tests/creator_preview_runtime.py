#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real GTK creator-review transitions in isolated stores and sessions.

No modem, production Hub or personal data participates. Directory callbacks
are controlled to verify stale responses/errors without claiming delivery.
"""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import shutil
import time
from types import SimpleNamespace
from unittest.mock import patch

# Load the canonical shell contract before GTK/kit initialization. RPM checks
# receive the exact schema as Source3; source runs resolve the same owned file.
_schema_fixture = tempfile.TemporaryDirectory(prefix='creator-appearance-schema-')
_schema = Path(os.environ.get('LUMA_CREATOR_SCHEMA_FILE',
    str(Path(__file__).resolve().parents[3] / 'src/luma-shell-state/org.project_luma.shell-state.gschema.xml')))
assert _schema.is_file(), 'Canonical appearance schema is required for genuine theme tests'
shutil.copyfile(_schema, Path(_schema_fixture.name) / _schema.name)
subprocess.run(['glib-compile-schemas', '--strict', _schema_fixture.name], check=True)
os.environ['GSETTINGS_SCHEMA_DIR'] = _schema_fixture.name
os.environ['GSETTINGS_BACKEND'] = 'memory'

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from luma_appkit import EmptyState, ListEmptyState, add_style_sheet
from prairie_apps import messages, notes, phone
from prairie_apps.clock import ClockWindow
from prairie_apps.calendar_window import CalendarWindow
from prairie_apps.notes_lumaui import NotesLumaWindow
from prairie_apps.photos import PhotosWindow
from prairie_apps.photos_backend import PhotoLibrary


def settle(predicate=lambda: True, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        if predicate():
            # Give the frame clock time to allocate the complete window.
            end = time.monotonic() + .05
            while time.monotonic() < end:
                while context.pending():
                    context.iteration(False)
                time.sleep(.005)
            return
        time.sleep(.005)
    raise AssertionError('The GTK state did not settle')


def screenshot(window, name):
    directory = os.environ.get('LUMA_CREATOR_REVIEW_CAPTURE')
    if not directory:
        return
    from gi.repository import Gsk
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    # A content-changing action can invalidate the paintable before the
    # next GTK frame. Require actual pixels after that allocation/paint.
    until = time.monotonic() + 2
    while node is None and time.monotonic() < until:
        settle(lambda: window.get_mapped())
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
    assert node is not None, 'No actual window pixels were produced'
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(window.get_surface())
    try:
        renderer.render_texture(node, None).save_to_png(str(output / f'{name}.png'))
    finally:
        renderer.unrealize()


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for key, name in (('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                          ('XDG_STATE_HOME', 'state')):
            os.environ[key] = str(root / name)
        os.environ['PRAIRIE_EDS_MODE'] = 'disabled'
        os.environ['TZ'] = 'Europe/Paris'
        core = Path(__file__).resolve().parents[1]
        for app_name in ('NOTES', 'PHOTOS', 'CLOCK', 'PHONE', 'MESSAGES', 'CALENDAR', 'CAMERA', 'TASKS'):
            os.environ.setdefault(f'LUMA_{app_name}_STYLE_PATH', str(core / 'style' / f'{app_name.lower()}.css'))
        app = notes.NotesApplication()
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN)
        assert app.register(None)
        for name in ('photos', 'clock', 'phone', 'calendar'):
            add_style_sheet(os.environ[f'LUMA_{name.upper()}_STYLE_PATH'])
        messages.install_messages_theme()
        style = Adw.StyleManager.get_default()
        appearance = Gio.Settings.new('org.project_luma.shell-state')
        def theme(dark):
            assert appearance.set_string('surface-treatment', 'dark' if dark else 'light')
            try:
                settle(lambda: style.get_dark() is dark)
            except AssertionError:
                from luma_appkit import widgets
                print('THEME', dark, style.get_dark(), style.get_color_scheme(),
                      appearance.get_string('surface-treatment'), widgets.appearance,
                      [(p.get_requested(), p.get_surface(), p.get_has_selection())
                       for p in widgets._appearance_policies.values()], flush=True)
                raise


        # The store is real and private; unrelated semantic publication and
        # physical call providers are not the subject of this UI check.
        with patch.object(NotesLumaWindow, '_create_semantic_publisher', return_value=None), \
             patch.object(NotesLumaWindow, '_watch_store', return_value=None):
            window = NotesLumaWindow(app)
            for dark in (False, True):
                theme(dark)
                for width in (360, 500, 1024, 1440):
                    window.set_default_size(width, 650)
                    window.present()
                    try:
                        settle(lambda: window.get_surface() is not None and window.get_surface().get_width() == width)
                    except AssertionError:
                        screenshot(window, f'notes-width-failure-{width}')
                        raise AssertionError(f'Notes requested {width}px, widget {window.get_width()}px, surface {window.get_surface().get_width()}px')
                    assert window.empty_state.get_visible()
                    assert not window.body_scroll.get_visible()
                    assert window.format_toolbar._state == 'hidden'
                    assert not window.sidebar_toggle.get_visible()
                    if width >= 1024:
                        assert window.empty_state.get_height() > 300
                        screenshot(window, f'notes-empty-{width}-{int(dark)}')
            window.set_default_size(1024, 650)
            settle(lambda: window.get_surface().get_width() == 1024)
            window.create_note()
            settle()
            assert window.body_scroll.get_visible() and not window.empty_state.get_visible()
            assert window.canvas.get_margin_top() == 32
            with patch.object(Gio.DesktopAppInfo, 'new', return_value=None), \
                 patch.object(Gio.AppInfo, 'launch_default_for_uri', return_value=True) as launch:
                window._open_in_write()
                assert launch.call_args.args[0] == 'luma-depot://install/org.projectluma.Write'
            assert window.current is not None and window.store.get_note(window.current.id)
            screenshot(window, 'notes-document-1024')
            window.close()
            settle()

        window = ClockWindow(app)
        cards = window.store.world_clocks()
        assert [(card.label, card.zone) for card in cards] == [('Paris', 'Europe/Paris')]
        window.set_mode('alarm')
        window.present()
        settle(lambda: window.get_width() > 0)
        assert window.alarm_empty.get_visible() and not window.alarm_hero.get_visible()
        assert window.alarm_empty.primary_button.grab_focus()
        screenshot(window, 'clock-alarms-empty')
        window.set_mode('timer')
        settle()
        assert window.timer_time.get_margin_bottom() == 0
        assert window.timer_note.label.get_text() == 'Ready'
        screenshot(window, 'clock-timer-ready')
        window.close()
        settle()

        library = PhotoLibrary(root / 'photos.sqlite')
        with patch.object(PhotosWindow, '_begin_scan', return_value=None), \
             patch.object(PhotosWindow, '_watch_drives', return_value=None):
            window = PhotosWindow(app, library=library)
            window.present()
            settle(lambda: window.state is not None)
            assert window.library_empty.get_visible() and not window.scroll.get_visible()
            assert not window.toggle.get_visible()
            assert window.library_empty.primary_button.grab_focus()
            assert Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon(window.library_empty.icon.get_icon_name())
            screenshot(window, 'photos-empty')
            window.state.query = 'missing'
            window._render_library()
            assert not window.library_empty.get_visible() and window.scroll.get_visible()
            window.close()
            settle()

        with patch.object(phone.PhoneWindow, '_start_live', return_value=False):
            window = phone.PhoneWindow(app)
            window.source = SimpleNamespace(people=(), contacts=(), favourites=(), calls=(), voicemails=())
            window._side()
            state = window.sidebar.list.get_first_child().get_child()
            assert isinstance(state, ListEmptyState) and state.get_text() == 'No calls yet'
            window.present()
            settle()
            screenshot(window, 'phone-sidebar-empty')
            window.close()
            settle()

        from prairie_apps import camera
        from prairie_apps.camera_backend import CameraDevice
        camera._install_camera_theme()
        device = CameraDevice('test-only', 'Integrated Camera: Integrated Camera',
                              'external', desktop_uvc=True)
        with patch.object(camera, 'list_camera_devices', return_value=(device,)), \
             patch.object(camera.CameraWindow, '_start_camera'):
            window = camera.CameraWindow(app)
            window.present()
            settle(lambda: hasattr(window, '_surface') and window.get_mapped())
            # Only hardware acquisition is replaced. GTK controls, responsive
            # parenting and source labels are the production implementation.
            window._picture.set_paintable(Gdk.MemoryTexture.new(512, 512,
                Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(bytes((30, 130, 190)) * 512 * 512), 512 * 3))
            window._status.set_visible(False)
            assert window._source_label.get_text() == 'Built-in Camera'
            assert window._source_picker.has_css_class('lumaui-text-button')
            assert window._source_picker.has_css_class('raised')
            assert window._surface.bar.has_css_class('lumaui-ac-bar')
            assert not window._surface.overlay.has_css_class('lumaui-media')
            for dark in (False, True):
                theme(dark)
                settle()
                screenshot(window, f'camera-desktop-{int(dark)}')
                window._source_picker.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
                settle()
                screenshot(window, f'camera-source-hover-{int(dark)}')
                window._source_picker.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            window.set_default_size(360, 800)
            settle(lambda: window._surface.phone)
            assert not window._surface.bar.get_visible()
            assert window._capture_button.get_parent() is window._surface.bottom_row
            screenshot(window, 'camera-phone')
            window.set_default_size(1180, 740)
            settle(lambda: not window._surface.phone)
            assert window._surface.bar.get_visible()
            assert window._capture_button.get_parent() is window._surface.fixed
            assert not window._surface.overlay.has_css_class('lumaui-media')
            screenshot(window, 'camera-desktop-return')
            window.close()
            settle()

        window = CalendarWindow(app)
        window.set_default_size(1180, 740)
        window.present()
        settle(lambda: window.loaded and window.get_surface().get_width() == 1180)
        assert window.heading.has_css_class('raised')
        assert window.heading_island.__class__ is Gtk.Box
        assert window.pane.sheet.get_margin_start() == 0
        screenshot(window, 'calendar-raised-heading')
        window.date_menu()
        settle()
        screenshot(window, 'calendar-date-picker')
        window.close()
        settle()

        from prairie_apps import tasks
        tasks_fixture = root / 'tasks.json'
        tasks_fixture.write_text(json.dumps({'today': '2026-10-05', 'tasks': [],
            'people': {'me': {'n': 'You', 'u': 'you'}},
            'lists': [{'id': 'personal', 'n': 'Personal', 'ppl': [], 'secs': [], 'h': 200}]}))
        add_style_sheet(os.environ['LUMA_TASKS_STYLE_PATH'])
        with patch.dict(os.environ, {'LUMA_TASKS_FIXTURE': str(tasks_fixture)}):
            # A compact cold launch reaches details before any wide render.
            for width in (360, 500):
                compact = tasks.TasksWindow(app)
                compact.set_default_size(width, 800)
                compact.present()
                settle(lambda: bool(compact.data['lists']) and compact.get_mapped())
                compact.draft = 'Compact launch today'
                compact._add_task()
                settle(lambda: len(compact.data['tasks']) == 1 and not compact.busy)
                compact._pick(compact.data['tasks'][0]['id'])
                settle(lambda: compact.phone_panel is not None and compact.phone_panel.get_mapped())
                assert next(w for w in descendants(compact) if w.get_name() == 'tk-comment').get_mapped()
                compact.close()
                settle()
            window = tasks.TasksWindow(app)
            window.set_default_size(1180, 740)
            window.present()
            settle(lambda: bool(window.data['lists']) and window.get_mapped())
            assert window.empty_state.get_visible() and not window.scroll.get_visible()
            assert not window.sidebar_toggle.get_visible()
            assert window.add_field.widget.text_widget.get_mapped(), 'new-task entry is unavailable on launch'
            screenshot(window, 'tasks-empty')
            window.draft = 'take out trash tomorrow at3:00p.m.'
            window._add_task()
            settle(lambda: len(window.data['tasks']) == 1 and not window.busy)
            task, = window.data['tasks']
            assert (task['t'], task['due'], task['time']) == ('take out trash', 1, '3:00 PM')
            assert window.details.sheet.get_margin_start() == 0
            window._save_time(task['id'], '16:30')
            settle(lambda: window.data['tasks'][0]['time'] == '4:30 PM' and not window.busy)
            # Real pointer blur, no Enter or explicit save call: leave Time,
            # then hide/reopen details and read the store's canonical value.
            from native_input import outside_click
            clock_entry = next(w for w in descendants(window) if w.get_name() == 'tk-time').entry
            assert clock_entry.grab_focus()
            clock_entry.set_text('18:45')
            target = next(w for w in descendants(window) if w.get_name() == 'tk-title')
            ok, bounds = target.compute_bounds(window)
            assert ok and target.get_mapped()
            outside_click(window, int(bounds.get_x()+bounds.get_width()/2), int(bounds.get_y()+bounds.get_height()/2))
            settle(lambda: not window.busy and window.data['tasks'][0]['time'] == '6:45 PM')
            window._details_closed()
            window._pick(task['id'])
            settle()
            assert next(w for w in descendants(window) if w.get_name() == 'tk-time').entry.get_text() == '6:45 PM'
            assert window.comment_footer.get_parent() is not None
            ancestor = window.comment_footer
            while ancestor is not None and ancestor is not window.details.body:
                ancestor = ancestor.get_parent()
            assert ancestor is window.details.body, 'Comment composer must belong to the details section'
            screenshot(window, 'tasks-detail-time')
            for width in (360, 500, 1024, 1440):
                window.set_default_size(width, 800)
                settle(lambda: window.get_surface().get_width() == width)
                window._edit(task['id'], {'repeat': ''})
                settle(lambda: not window.busy and not window.data['tasks'][0].get('repeat'))
                repeat = next(w for w in descendants(window) if w.get_name() == 'tk-repeat')
                assert repeat.get_mapped()
                repeat.emit('clicked')
                settle(lambda: window._repeat_menu is not None and window._repeat_menu.get_mapped())
                weekly = next(w for w in descendants(window._repeat_menu) if isinstance(w, Gtk.Button)
                              and any(isinstance(n, Gtk.Label) and n.get_text() == 'Every week' for n in descendants(w)))
                assert weekly.activate(), 'Repeat menu action could not be activated'
                settle(lambda: not window.busy and window.data['tasks'][0].get('repeat') == 'FREQ=WEEKLY')
                imported_rule = 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;COUNT=12'
                window._edit(task['id'], {'repeat': imported_rule})
                settle(lambda: not window.busy and window.data['tasks'][0].get('repeat') == imported_rule)
                repeat = next(w for w in descendants(window) if w.get_name() == 'tk-repeat')
                window._repeat_custom(repeat)
                settle(lambda: window._repeat_menu.get_mapped())
                unchanged_save = next(w for w in descendants(window._repeat_menu) if w.get_name() == 'tk-repeat-save')
                unchanged_save.emit('clicked')
                settle()
                assert window.data['tasks'][0]['repeat'] == imported_rule, 'Unchanged custom Save altered recurrence'
                repeat = next(w for w in descendants(window) if w.get_name() == 'tk-repeat')
                window._repeat_custom(repeat)
                settle(lambda: window._repeat_menu.get_mapped())
                interval = next(w for w in descendants(window._repeat_menu) if w.get_name() == 'tk-repeat-interval')
                interval.entry.set_text('0')
                save = next(w for w in descendants(window._repeat_menu) if w.get_name() == 'tk-repeat-save')
                save.emit('clicked')
                settle()
                assert window.data['tasks'][0]['repeat'] == imported_rule
                assert window._repeat_menu.get_mapped()
                interval.entry.set_text('3')
                save.emit('clicked')
                settle(lambda: not window.busy and window.data['tasks'][0].get('repeat') == imported_rule + ';INTERVAL=3')
                screenshot(window, f'tasks-repeat-{width}')
            # The actual UI must issue Never even for an imported dates-only
            # schedule whose visible RRULE string is empty.
            window.source.data['tasks'][0].update(repeat='', recurring=True)
            window._reload()
            settle(lambda: not window.busy and window.data['tasks'][0].get('recurring'))
            repeat = next(w for w in descendants(window) if w.get_name() == 'tk-repeat')
            repeat.emit('clicked')
            settle(lambda: window._repeat_menu.get_mapped())
            never = next(w for w in descendants(window._repeat_menu) if isinstance(w, Gtk.Button)
                         and any(isinstance(n, Gtk.Label) and n.get_text() == 'Never' for n in descendants(w)))
            assert never.activate()
            settle(lambda: not window.busy and not window.data['tasks'][0].get('recurring'))
            # An actual blur arriving during a pending write must not erase
            # the explicit queued close. Release the worker only after that
            # event has reached GTK, then require saved Time and closed window.
            import threading
            release = threading.Event()
            original_edit = window.source.edit
            calls = []
            def held_edit(key, changes):
                calls.append(dict(changes))
                if len(calls) == 1:
                    assert release.wait(5), 'Private worker gate was not released'
                return original_edit(key, changes)
            with patch.object(window.source, 'edit', side_effect=held_edit):
                clock_entry = next(w for w in descendants(window) if w.get_name() == 'tk-time').entry
                assert clock_entry.grab_focus()
                clock_entry.set_text('19:20')
                window._edit(task['id'], {'flag': True})
                settle(lambda: window.busy and bool(calls))
                window.close()
                target = next(w for w in descendants(window) if w.get_name() == 'tk-title')
                ok, bounds = target.compute_bounds(window)
                assert ok
                outside_click(window, int(bounds.get_x()+bounds.get_width()/2), int(bounds.get_y()+bounds.get_height()/2))
                settle()
                release.set()
                settle(lambda: window.closed and not window.get_mapped(), timeout=8)
                assert window.source.data['tasks'][0]['time'] == '7:20 PM'
                assert any(change.get('time') == '19:20' for change in calls)
            print('PASS Tasks imported recurrence constraints/Never plus actual Time blur/reopen/busy close and widths')
            settle()

        from prairie_apps import voice_memos
        add_style_sheet(os.environ.get('LUMA_MEMO_STYLE_PATH', str(core / 'style/memo.css')))
        with patch.dict(os.environ, {'XDG_MUSIC_DIR': str(root / 'music')}), \
             patch.object(voice_memos.audio, 'inspect_recording_capability') as inspect_microphone:
            window = voice_memos.VoiceMemosWindow(app)
            window.present()
            settle(lambda: window.content_stack.get_visible_child_name() == 'empty')
            assert window.empty.heading.get_text() == 'No recordings yet'
            window._new_recording()
            settle()
            assert window._draft and not window.recording and window.session is None
            assert window.empty.heading.get_text() == 'New recording'
            inspect_microphone.assert_not_called()
            screenshot(window, 'memos-new-draft')
            window._cancel_draft()
            assert not window._draft and not window.recording
            inspect_microphone.assert_not_called()
            # Continue through the actual UI/session/encoder using a private
            # synthetic source. This opens no physical microphone.
            from audio_continuation import tone
            window.root.mkdir(parents=True, exist_ok=True)
            original = window.root / 'Meeting.ogg'
            tone(original, 440)
            original_bytes = original.read_bytes()
            window._reload_real()
            settle(lambda: window.current_id == str(original) and len(window.memos) == 1)
            window._continue_recording()
            assert window._draft and window._continuing == original and not window.recording
            inspect_microphone.assert_not_called()
            inspect_microphone.return_value = SimpleNamespace(available=True, source='Synthetic test')
            real_start = voice_memos.audio.start_recording
            def synthetic_start(root, **kwargs):
                return real_start(root, source='audiotestsrc', **kwargs)
            with patch.object(voice_memos.audio, 'start_recording', side_effect=synthetic_start) as opened:
                window._record()
                settle(lambda: window.recording and not window._busy)
                settle()
                time.sleep(.2)
                window._record()
                settle(lambda: not window.recording and not window._busy and len(window.memos) == 3)
                opened.assert_called_once()
            assert original.read_bytes() == original_bytes
            continued = Path(window.current_id)
            assert continued.stem == 'Meeting continued' and continued.is_file()
            assert voice_memos.audio.load_peaks(continued, window.root)[1] > .35
            screenshot(window, 'memos-continued-audio')
            window.close()
            settle()

        fixture = root / 'messages.json'
        fixture.write_text(json.dumps({'people': {}, 'conversations': []}))
        with patch.dict(os.environ, {'LUMA_MESSAGES_FIXTURE': str(fixture)}):
            window = messages.MessagesWindow(app)
            window.present()
            settle()
            # Resolve the actual menu models through the installed icon theme.
            # Incoming/read states reach both context menu variations.
            icon_theme = Gtk.IconTheme.get_for_display(window.get_display())
            service_for_icons = SimpleNamespace(phone_numbers=True,
                store=SimpleNamespace(has_incoming=lambda _: True))
            registries = [window.commands]
            registries.extend(window.thread_commands(messages.ThreadRecord('+12025550123', 'Private', '', 0, unread),
                              service_for_icons) for unread in (0, 1))
            for registry in registries:
                for group in registry.visible_groups(menu=True):
                    for command in group.commands:
                        assert command.icon, f'Missing menu icon: {command.id}'
                        assert icon_theme.has_icon(messages.icons.resolve(command.icon)), command.icon
            assert isinstance(window.list_empty, EmptyState)
            assert window.list_empty.heading.get_text() == 'No conversations yet'
            assert window.empty_state.heading.get_text() == 'No messages yet'
            screenshot(window, 'messages-two-empty-states')
            pending = []
            service = SimpleNamespace()
            with patch.object(window, '_luma_service', return_value=service), \
                 patch.object(window, 'luma_call', side_effect=lambda *args: pending.append(args)):
                window.recipient_search.set_text('alice')
                window._reload_recipients()
                assert pending[-1][1:3] == ('luma.people', {'handle': 'alice'})
                first = pending[-1][-1]
                window.recipient_search.set_text('bob')
                window._reload_recipients()
                first({'account': 'test-alice', 'display_name': 'Alice'}, None)
                assert not getattr(window.recipient_list.get_row_at_index(0), 'luma_handle', None)
                pending[-1][-1]({'account': 'test-bob', 'display_name': 'Bob'}, None)
                assert window.recipient_list.get_row_at_index(0).luma_handle == 'bob'
                window.recipient_search.set_text('missing')
                window._reload_recipients()
                pending[-1][-1](None, SimpleNamespace(message='No member found'))
                assert window.recipient_list.get_row_at_index(0).get_child().get_text() == 'No member found'
            window.close()
            settle()
    print('PASS: Notes widths/themes/create/Depot handoff; setup Clock/alarms/timer; Photos library vs search; Phone empty; Camera shared bar/themes/phone/return/source; Calendar date control/picker; Tasks empty/default entry/date/time/comments; Memos private empty/New draft without microphone; Messages two states/plain username/stale response/error. No real Hub delivery or hardware capture claimed.')


if __name__ == '__main__':
    main()
