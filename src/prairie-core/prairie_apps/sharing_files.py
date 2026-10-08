# SPDX-License-Identifier: Apache-2.0
"""Shared app adapter for exported copies and truthful ShareSheet results."""
from pathlib import Path
import os
import tempfile
from gi.repository import Gdk, Gio, GLib, Gtk
from luma_appkit import ShareSheet, ShareSubject, ShareResult, Toast


def share_calendar_copy(window, anchor, title, content):
    directory = tempfile.TemporaryDirectory(prefix='luma-share-')
    # Keep snapshots available while a receiving app opens its file argument.
    retained = getattr(window, '_share_snapshots', None)
    if retained is None:
        window._share_snapshots = retained = []
    retained.append(directory)
    name = ''.join(c for c in title if c.isalnum() or c in ' -_').strip()[:80] or 'Calendar'
    path = Path(directory.name) / (name + '.ics')
    path.write_text(content, encoding='utf-8')
    os.chmod(path, 0o600)
    file = Gio.File.new_for_path(str(path))
    subject = ShareSubject(title, 'Calendar file', mime_type='text/calendar', icon='calendar')
    def chosen(choice, value):
        if choice == 'copy':
            window.get_clipboard().set(Gdk.FileList.new_from_list([file]))
            return ShareResult(True, 'Copied')
        if choice == 'save':
            dialog = Gtk.FileDialog(title='Save a copy', initial_name=path.name)
            def saved(_dialog, result):
                try:
                    destination = dialog.save_finish(result)
                    file.copy_async(destination, Gio.FileCopyFlags.NONE, GLib.PRIORITY_DEFAULT, None, None, copied)
                except GLib.Error as error:
                    if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                        Toast.show(anchor, error.message, kind='error')
            def copied(_file, result, *_args):
                try:
                    file.copy_finish(result)
                    Toast.show(anchor, 'File saved', kind='saved')
                except GLib.Error as error: Toast.show(anchor, error.message, kind='error')
            dialog.save(window, None, saved)
            return ShareResult(False)
        if choice == 'target':
            target = next((t for t in sheet.targets if t.key == value), None)
            if not target or not target.app_id: return ShareResult(False, 'This app is no longer available')
            from luma_appkit.application_directory import launch
            def accepted(ok, error):
                if getattr(window, '_closed', False): return
                Toast.show(anchor, 'Opened in ' + target.label if ok else error or 'Could not share',
                           kind='sent' if ok else 'error')
            launch(target.app_id + '.desktop', [file],
                   context=window.get_display().get_app_launch_context(), callback=accepted)
            return ShareResult(False)
        return ShareResult(False, 'This action is not available for this copy')
    sheet = ShareSheet.present(anchor, document=subject, copy_actions=('copy', 'save'), on_choice=chosen)
    return sheet
