# SPDX-License-Identifier: Apache-2.0
"""The save changes sheet, driven with real key presses on a real X display.

Keys go through the X server (xdotool, XTEST), so they travel the same path a
person's do: through the window's own controllers and the application's
accelerators. That is the point of the Ctrl+D check: the application here
binds Ctrl+D three ways, as Layouts does (Duplicate), and none of them may
fire while the sheet is open, while all of them fire again once it is gone.

The sheet is the platform's C implementation (LumaUI.SaveSheet), reached
through the Python kit exactly as a Python application reaches it.

Checked: Enter takes the primary, from the primary and the Name field, while
Enter on Cancel, Don't save or Where answers with that control; Escape
cancels; Ctrl+D is the only key that discards; focus opens on the primary, or
on the Name field with its text selected, and returns where it was when the
sheet closes; Tab and Shift+Tab stay inside the sheet; only the asking
window's content is dimmed and inert; the untitled case writes the file in the
chosen place and reports a bad or taken name inline without leaving the
sheet; several documents are reviewed one window at a time, Cancel stops the
quit halfway, and Discard all quits; more than five documents scroll; a plain
window without the kit's layer gets the same sheet hanging under its header
bar; the destructive red is the measured token.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import time

import gi
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkX11', '4.0')
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('LumaUI', '1')
from gi.repository import Adw, Gdk, GdkX11, Gio, GLib, Graphene, Gtk, LumaUI  # noqa: E402,F401

from luma_appkit import AppWindow, CommandRegistry  # noqa: E402
from luma_appkit import widgets  # noqa: E402
from luma_appkit.save_sheet import SaveChoice, SaveSheet, UnsavedDocument, confirm_save  # noqa: E402

os.environ['LUMA_WINDOW_MEMORY'] = 'app'
assert isinstance(Gdk.Display.get_default(), GdkX11.X11Display), 'run with GDK_BACKEND=x11 under Xvfb'


def settle(predicate=lambda: True, seconds=8.0, what='the sheet'):
    # A key's answer can land a frame or two after the key under software
    # rendering, so every check on the answers waits for them first.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError(f'{what} did not settle')


def pause(seconds=.15):
    end = time.monotonic() + seconds
    settle(lambda: time.monotonic() >= end, seconds + 2)


def focus_window(window):
    xid = window.get_surface().get_xid()
    subprocess.run(['xdotool', 'windowfocus', str(xid)], check=True, timeout=10)
    pause(.2)


def key(window, *names):
    focus_window(window)
    for name in names:
        subprocess.run(['xdotool', 'key', '--clearmodifiers', name], check=True, timeout=10)
        pause(.12)


def focused_in(window, widget):
    focus = window.get_focus()
    return focus is not None and (focus is widget or focus.is_ancestor(widget))


with tempfile.TemporaryDirectory() as root:
    os.environ['XDG_STATE_HOME'] = str(Path(root) / 'state')
    app = Adw.Application(application_id='org.projectluma.SaveSheetTest',
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)

    # The application's own Ctrl+D, bound the three ways an application can.
    heard = {'accelerator': 0, 'bubble': 0, 'capture': 0, 'escape': 0, 'return': 0}

    def count(name):
        heard[name] += 1

    def app_window(title):
        window = AppWindow(application=app, app_id='org.projectluma.SaveSheetTest', title=title,
                           icon_name='application-x-executable', commands=CommandRegistry(()),
                           default_width=900, default_height=640, minimum_width=360, minimum_height=420)
        canvas = Gtk.Button(label='Canvas')
        window.set_body(canvas)
        duplicate = Gio.SimpleAction.new('duplicate', None)
        duplicate.connect('activate', lambda *_: count('accelerator'))
        window.add_action(duplicate)

        def bubble(_c, keyval, _code, state):
            control = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if control and keyval in (Gdk.KEY_d, Gdk.KEY_D):
                count('bubble')
                return True
            if keyval == Gdk.KEY_Escape:
                count('escape')
                return True
            if keyval == Gdk.KEY_Return:
                count('return')
                return True
            return False
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', bubble)
        window.add_controller(keys)
        early = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        early.connect('key-pressed', lambda _c, keyval, _code, state: (
            count('capture') if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_d, Gdk.KEY_D) else None) and False)
        window.add_controller(early)
        window.canvas = canvas
        window.present()
        settle(lambda: window.get_mapped() and window.get_surface() is not None, what=title)
        return window

    app.set_accels_for_action('win.duplicate', ['<Control>d'])
    window = app_window('Mixdown v3')

    # The harness delivers keys: with no sheet, the application's Ctrl+D works.
    # (A handled key stops at the first controller that takes it, so the
    # accelerator hides Ctrl+D from the handlers after it; Escape reaches the
    # application's own handler.)
    window.canvas.grab_focus()
    key(window, 'ctrl+d', 'Escape')
    assert heard['accelerator'] == 1 and heard['escape'] == 1, heard
    before = dict(heard)

    answers = []

    def answered(response):
        answers.append(response)

    def sheet_of(win):
        sheet = win.presented_sheet
        return sheet if isinstance(sheet, SaveSheet) else None

    def open_sheet(win, documents, **kwargs):
        confirm_save(win, documents, answered, **kwargs)
        settle(lambda: sheet_of(win) is not None and sheet_of(win).has_css_class('open'))
        return sheet_of(win)

    def gone(win):
        settle(lambda: win.presented_sheet is None, what='closing')

    named = UnsavedDocument('Mixdown v3', unsaved_seconds=14 * 60, kind='project', window=window)

    def title(sheet): return sheet.get_title_label().get_label()
    def body(sheet): return sheet.get_body_label().get_label()

    # 1. Named document: words, focus, role, and only this window dimmed.
    window.canvas.grab_focus()
    sheet = open_sheet(window, [named])
    primary, cancel, discard = sheet.get_primary_button(), sheet.get_cancel_button(), sheet.get_discard_button()
    assert title(sheet) == 'Save changes to “Mixdown v3”?', title(sheet)
    assert body(sheet) == 'You have 14 minutes of edits that are not saved yet.', body(sheet)
    assert primary.get_label() == 'Save' and cancel.get_label() == 'Cancel'
    assert sheet.get_card().get_accessible_role() == Gtk.AccessibleRole.ALERT_DIALOG
    assert focused_in(window, primary), window.get_focus()
    assert not window.body.get_can_target() and not window.body.get_can_focus()
    assert window.title_bar.get_visible(), 'the title bar stays; the sheet hangs from it'
    assert sheet.get_parent() is window.sheet_layer
    card_width = sheet.get_card().get_width()
    assert 0 < card_width <= min(360, window.sheet_layer.get_width() - 32), (card_width, window.sheet_layer.get_width())

    # Tab and Shift+Tab stay inside, in order, and come round again.
    for expected in (cancel, discard, primary, cancel):
        key(window, 'Tab')
        assert focused_in(window, expected), (expected, window.get_focus())
    key(window, 'shift+Tab', 'shift+Tab')
    assert focused_in(window, discard), window.get_focus()
    key(window, 'shift+Tab', 'shift+Tab')
    assert focused_in(window, primary)
    key(window, 'Return')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.SAVE], answers
    assert sheet_of(window) is sheet, 'Save waits for the application'
    assert not primary.get_sensitive()
    answers[-1].fail('The disk is full.')
    settle(lambda: sheet.get_error_label().get_visible())
    assert sheet.get_error_label().get_label() == 'The disk is full.' and sheet_of(window) is sheet
    key(window, 'Return')
    settle(lambda: len(answers) >= 2, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.SAVE, SaveChoice.SAVE]
    answers[-1].done()
    gone(window)
    assert heard == before, ('keys reached the application behind the sheet', heard, before)
    assert focused_in(window, window.canvas), ('focus returns where it was', window.get_focus())

    # 2. Escape cancels, and nothing behind the sheet hears it.
    answers.clear()
    sheet = open_sheet(window, [named])
    key(window, 'Escape')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.CANCEL], answers
    gone(window)
    assert heard == before, heard
    assert focused_in(window, window.canvas)

    # Enter on a focused Cancel answers Cancel; on Don't save, Don't save.
    answers.clear()
    sheet = open_sheet(window, [named])
    key(window, 'Tab', 'Return')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.CANCEL], answers
    gone(window)
    answers.clear()
    sheet = open_sheet(window, [named])
    key(window, 'Tab', 'Tab', 'Return')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.DISCARD], answers
    gone(window)
    assert heard == before, heard

    # 3. Ctrl+D discards, and only the sheet hears it: the application's
    #    accelerator and both of its key handlers stay silent.
    answers.clear()
    sheet = open_sheet(window, [named])
    key(window, 'ctrl+d')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.DISCARD], answers
    assert answers[0].document is named
    gone(window)
    assert heard == before, ('Ctrl+D reached the application', heard, before)
    # Once the sheet is gone, the application's Ctrl+D is its own again.
    window.canvas.grab_focus()
    key(window, 'ctrl+d')
    assert heard['accelerator'] == before['accelerator'] + 1, heard
    assert window.body.get_can_target() and window.body.get_can_focus()
    before = dict(heard)

    # Other keys never discard: Delete, BackSpace, D alone, Ctrl+Shift+D.
    answers.clear()
    sheet = open_sheet(window, [named])
    key(window, 'Delete', 'BackSpace', 'd', 'ctrl+shift+d', 'alt+d')
    assert not answers, answers
    key(window, 'Escape')
    gone(window)

    # 4. Unknown time: the sheet says so and invents no number.
    answers.clear()
    sheet = open_sheet(window, [UnsavedDocument('Notes.txt', window=window, recovery_copy=True)])
    assert body(sheet) == ('Your changes haven’t been saved. '
                           'A recovery copy is kept until you save or discard.'), body(sheet)
    key(window, 'Escape')
    gone(window)

    # 5. Untitled: named and placed on the sheet, errors inline, file written.
    places = Path(root) / 'places'
    projects, documents = places / 'Projects', places / 'Documents'
    documents.mkdir(parents=True)
    (documents / 'Taken.layouts').write_text('already here')
    untitled = UnsavedDocument('Untitled Project', never_saved=True, extension='.layouts', window=window,
                               places=[('Projects', str(projects)), ('Documents', str(documents))])
    answers.clear()
    sheet = open_sheet(window, [untitled])
    name, where = sheet.get_name_entry(), sheet.get_where()
    error = sheet.get_error_label()
    assert sheet.get_case() == 'untitled'
    assert title(sheet) == 'Save “Untitled Project” before closing?'
    assert body(sheet) == 'It has never been saved. Give it a name and a place.'
    assert focused_in(window, name), window.get_focus()
    bounds = name.get_selection_bounds()
    start, end = bounds[-2:]
    assert (start, end) == (0, len('Untitled Project')), bounds
    labels = [where.get_model().get_string(i) for i in range(where.get_model().get_n_items())]
    assert labels[:2] == ['Projects', 'Documents'] and labels[-1] == 'Other location…', labels
    for expected in (where, primary := sheet.get_primary_button(), sheet.get_cancel_button(),
                     sheet.get_discard_button(), name):
        key(window, 'Tab')
        assert focused_in(window, expected), (expected, window.get_focus())
    # Enter on Where opens the menu instead of saving.
    key(window, 'Tab')
    assert focused_in(window, where)
    key(window, 'Return')
    pause(.4)
    assert not answers and sheet_of(window) is sheet, answers
    key(window, 'Escape')
    pause(.3)
    assert sheet_of(window) is sheet, 'Escape closes the menu first'

    for bad, message in (('bad/name', 'A name can’t contain “/”.'), ('   ', 'Give it a name.'),
                         ('.hidden', 'A name can’t start with a dot.'), ('x' * 300, 'That name is too long.')):
        name.set_text(bad)
        name.grab_focus()
        key(window, 'Return')
        assert not answers, answers
        assert error.get_visible() and error.get_label() == message, (bad, error.get_label())
        assert name.get_parent().has_css_class('error') and sheet_of(window) is sheet
    name.set_text('Taken')
    assert not error.get_visible(), 'typing clears the error'
    where.set_selected(1)
    name.grab_focus()
    key(window, 'Return')
    assert error.get_label() == 'There is already a “Taken.layouts” in Documents.', error.get_label()
    assert not answers and sheet_of(window) is sheet

    name.set_text('Fresh')
    name.grab_focus()
    key(window, 'Return')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.SAVE], answers
    destination = answers[0].destination
    assert destination.get_path() == str(documents / 'Fresh.layouts'), destination.get_path()
    Path(destination.get_path()).write_text('the layout')   # the application writes it
    answers[0].done()
    gone(window)
    assert (documents / 'Fresh.layouts').read_text() == 'the layout'
    assert not (projects / 'Fresh.layouts').exists()
    # The first place is created on demand when it does not exist yet.
    answers.clear()
    sheet = open_sheet(window, [untitled])
    sheet.get_name_entry().set_text('Second')
    key(window, 'Return')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert answers[0].destination.get_path() == str(projects / 'Second.layouts')
    assert projects.is_dir()
    answers[0].done()
    gone(window)
    assert heard == before, heard

    # 6. Several, when quitting: review each window in turn, Cancel stops the
    #    quit halfway, Discard all quits.
    second, third = app_window('Stems'), app_window('Master')
    several = [named,
               UnsavedDocument('Stems', unsaved_seconds=120, kind='project', window=second),
               UnsavedDocument('Master', never_saved=True, kind='project', window=third)]
    answers.clear()
    sheet = open_sheet(window, several, quitting=True)
    assert title(sheet) == 'Save changes to 3 projects before quitting?', title(sheet)
    assert body(sheet) == 'Go through them one at a time, or leave without saving any of them.'
    assert sheet.get_primary_button().get_label() == 'Review changes…'
    assert focused_in(window, sheet.get_primary_button())
    assert second.presented_sheet is None and second.body.get_can_target(), 'other windows stay usable'
    key(window, 'Return')
    settle(lambda: sheet_of(window) is not None and sheet_of(window) is not sheet
           and sheet_of(window).has_css_class('open'))
    assert title(sheet_of(window)) == 'Save changes to “Mixdown v3”?'
    key(window, 'ctrl+d')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [(a.choice, a.document) for a in answers] == [(SaveChoice.DISCARD, named)], answers
    settle(lambda: sheet_of(second) is not None and sheet_of(second).has_css_class('open'))
    assert title(sheet_of(second)) == 'Save changes to “Stems”?'
    key(second, 'Return')
    settle(lambda: len(answers) >= 2, what='the answer')
    assert answers[-1].choice is SaveChoice.SAVE and answers[-1].document is several[1]
    answers[-1].done()
    settle(lambda: sheet_of(third) is not None and sheet_of(third).has_css_class('open'))
    assert sheet_of(third).get_case() == 'untitled' and title(sheet_of(third)) == 'Save “Master” before quitting?'
    key(third, 'Escape')
    settle(lambda: len(answers) >= 3, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.DISCARD, SaveChoice.SAVE, SaveChoice.CANCEL], answers
    gone(third)
    assert SaveChoice.QUIT not in [a.choice for a in answers], 'Cancel stops the quit'

    answers.clear()
    sheet = open_sheet(window, several, quitting=True)
    key(window, 'ctrl+d')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.QUIT] and len(answers[0].documents) == 3, answers
    gone(window)
    assert heard == before, heard

    # More than five documents scroll inside the sheet.
    many = [UnsavedDocument(f'Take {n}', unsaved_seconds=60 * n, kind='project') for n in range(1, 9)]
    answers.clear()
    sheet = open_sheet(window, many, quitting=True)

    def scrollers(widget):
        found = [widget] if isinstance(widget, Gtk.ScrolledWindow) else []
        child = widget.get_first_child()
        while child is not None:
            found += scrollers(child)
            child = child.get_next_sibling()
        return found
    listing = scrollers(sheet.get_card())
    assert len(listing) == 1 and listing[0].get_height() <= 5 * 32 + 12, [w.get_height() for w in listing]
    key(window, 'Escape')
    gone(window)

    # 7. Reduced motion: no animation, the sheet is up and down at once.
    Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
    answers.clear()
    sheet = open_sheet(window, [named])
    key(window, 'Escape')
    assert window.presented_sheet is None, 'no closing animation with reduced motion'
    Gtk.Settings.get_default().set_property('gtk-enable-animations', True)

    # 8. A plain window, as a C application builds it: the same sheet, hung
    #    under its header bar, only its content dimmed, focus given back.
    plain = Adw.ApplicationWindow(application=app, title='Plain')
    plain.set_default_size(700, 500)
    view = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header_command = Gtk.Button(label='Share')
    header.pack_start(header_command)
    view.add_top_bar(header)
    editor = Gtk.TextView()
    view.set_content(editor)
    plain.set_content(view)
    plain.present()
    settle(lambda: plain.get_mapped())
    editor.grab_focus()
    answers.clear()
    fallback = confirm_save(plain, [UnsavedDocument('Plain', unsaved_seconds=300, window=plain)], answered)
    settle(lambda: fallback.has_css_class('open'))
    pause(.3)
    assert LumaUI.window_get_presented_sheet(plain) is fallback
    ok, top = fallback.compute_point(plain, Graphene.Point())
    header_bottom = header.compute_bounds(plain)[1].get_y() + header.get_height()
    assert ok and top.y >= header_bottom - 1, (top.y, header_bottom)
    assert not header_command.get_can_target(), 'the application\'s commands in the title bar wait'
    assert focused_in(plain, fallback.get_primary_button()), plain.get_focus()
    key(plain, 'Escape')
    settle(lambda: len(answers) >= 1, what='the answer')
    assert [a.choice for a in answers] == [SaveChoice.CANCEL], answers
    settle(lambda: LumaUI.window_get_presented_sheet(plain) is None, what='the sheet')
    assert header_command.get_can_target() and focused_in(plain, editor), plain.get_focus()

    # 9. The destructive red is the measured token, on the opaque sheet.
    sheet = open_sheet(window, [named])
    red = sheet.get_discard_button().get_color()
    assert (round(red.red * 255), round(red.green * 255), round(red.blue * 255)) == (0x98, 0x3b, 0x34), red.to_string()
    key(window, 'Escape')
    gone(window)
    kit = Path(widgets.__file__).resolve().parent.parent
    for sheet_name, light_ink in (('luma-appkit-tokens.css', False), ('luma-appkit-dark-tokens.css', True)):
        candidates = [kit / sheet_name, Path('/usr/share/luma-appkit') / sheet_name,
                      Path(os.environ.get('LUMA_APPKIT_BASE_PATH', '/nonexistent')).parent / sheet_name]
        found = next((str(path) for path in candidates if path.is_file()), None)
        assert found, sheet_name
        assert widgets._ink_is_light(found, not light_ink) is light_ink, sheet_name

    # 10. Words: plurals, middle-shortened names, times, never an estimate.
    def words(**facts):
        document = LumaUI.SaveDocument.new(facts.pop('name', 'x'))
        document.set_unsaved_seconds(facts.pop('seconds', -1))
        return LumaUI.SaveSheet.new([document], False)
    assert title(words(name='A' * 20 + 'B' * 30)) == 'Save changes to “AAAAAAAAAAAAAAAAA…BBBBBBBBBBBBBBBBB”?'
    assert body(words(seconds=60)) == 'You have 1 minute of edits that are not saved yet.'
    assert body(words(seconds=3 * 3600)) == 'You have 3 hours of edits that are not saved yet.'
    assert body(words()) == 'Your changes haven’t been saved.'

    for win in (window, second, third, plain):
        win.destroy()
    settle()

print('Save changes sheet (LumaUI): named, untitled and several cases; Enter, Escape, Tab and Ctrl+D; '
      'focus return; inline name errors; plain windows; measured red.')
