# SPDX-License-Identifier: GPL-3.0-only
"""Present Chromium's selected-page JavaScript dialogs with Luma's toolkit."""
from urllib.parse import urlsplit
import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk


class NativeDialogs:
    def __init__(self, window):
        self.window = window
        self.dialog = None
        self.session = None

    def event(self, event):
        session = event.get('sessionId')
        if session != self.window.page_input.session:
            return False
        method = event.get('method')
        if method == 'Page.javascriptDialogClosed':
            self.dismiss(answer=False)
            return False
        if method != 'Page.javascriptDialogOpening':
            return False
        self.dismiss()
        data = event.get('params') or {}
        kind = data.get('type')
        if kind not in ('alert', 'confirm', 'prompt', 'beforeunload'):
            return False
        source = urlsplit(data.get('url', ''))
        title = 'Viola Settings' if source.scheme == 'chrome' and source.hostname == 'viola' else source.hostname or 'This page'
        if kind == 'beforeunload':
            title = 'Leave this page?'
        dialog = Adw.AlertDialog(heading=title[:200], body=str(data.get('message', ''))[:16000])
        dialog.set_body_use_markup(False)
        dialog.set_heading_use_markup(False)
        if kind != 'alert':
            dialog.add_response('cancel', 'Stay' if kind == 'beforeunload' else 'Cancel')
        dialog.add_response('accept', 'Leave' if kind == 'beforeunload' else 'OK')
        dialog.set_default_response('accept' if kind == 'alert' else 'cancel')
        dialog.set_close_response('accept' if kind == 'alert' else 'cancel')
        entry = None
        if kind == 'prompt':
            entry = Gtk.Entry(text=str(data.get('defaultPrompt',''))[:4096], activates_default=True)
            entry.set_max_length(4096)
            dialog.set_extra_child(entry)
        self.dialog, self.session = dialog, session
        def responded(_dialog, response):
            if self.dialog is not dialog:
                return
            self.dialog = self.session = None
            payload = {'accept':response == 'accept'}
            if entry and payload['accept']:
                payload['promptText'] = entry.get_text()
            # Do not queue behind the browser command that is waiting for this
            # very confirmation. The anonymous pipe already serializes writes.
            self.window.page_input.engine.request('Page.handleJavaScriptDialog',payload,session=session)
        dialog.connect('response',responded)
        dialog.present(self.window)
        return False

    def dismiss(self, answer=True):
        dialog, session = self.dialog, self.session
        self.dialog = self.session = None
        if dialog:
            dialog.close()
        if answer and session:
            self.window.page_input.engine.request('Page.handleJavaScriptDialog',{'accept':False},session=session)

    def sync(self):
        if self.session and self.session != self.window.page_input.session:
            self.dismiss()
