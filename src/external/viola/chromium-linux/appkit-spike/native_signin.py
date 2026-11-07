# SPDX-License-Identifier: GPL-3.0-only
"""A site's sign-in (HTTP authentication), grown from the bar.

The engine hands Chromium's login request to the host (engine patch 0004) as
a notice on the permission channel: {id, type: 'login', title, body, host,
proxy}. The bar grows this panel in every layout; Sign in answers
`permission:respond` with the user name and password, Cancel or Esc with
`dismiss`. Nothing here keeps the password once it is sent.
"""

from gi.repository import Gdk, Gtk, Pango
from luma_appkit.bar_panel import PanelField


def is_signin(prompt):
    return isinstance(prompt, dict) and prompt.get('type') == 'login' and isinstance(prompt.get('id'), str)


class SignInPanel(Gtk.Box):
    """Title, one line, user name and password, then Cancel and Sign in."""

    def __init__(self, prompt, on_answer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.DIALOG)
        self.add_css_class('lumaui-panel-confirm')
        self.add_css_class('viola-signin')
        self.prompt_id = prompt['id']
        self.on_answer = on_answer
        self.answered = False
        title = prompt.get('title') or 'Sign in'
        heading = Gtk.Label(label=title, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        heading.add_css_class('lumaui-panel-confirm-title')
        self.append(heading)
        body = prompt.get('body') or ''
        if body:
            line = Gtk.Label(label=body, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            line.add_css_class('lumaui-panel-confirm-body')
            self.append(line)
        self.username = PanelField('user', 'User name', on_submit=lambda _text: self.password.grab_focus())
        self.username.entry.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        self.password = PanelField('lock', 'Password', on_submit=lambda _text: self.submit())
        self.password.entry.set_visibility(False)
        self.password.entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.append(self.username)
        self.append(self.password)
        buttons = Gtk.Box(homogeneous=True)
        buttons.add_css_class('lumaui-panel-confirm-buttons')
        cancel = Gtk.Button(label='Cancel', hexpand=True)
        cancel.add_css_class('lumaui-panel-confirm-cancel')
        cancel.connect('clicked', lambda _b: self.cancel())
        sign_in = Gtk.Button(label='Sign in', hexpand=True)
        sign_in.add_css_class('viola-signin-action')
        sign_in.connect('clicked', lambda _b: self.submit())
        buttons.append(cancel)
        buttons.append(sign_in)
        self.append(buttons)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, body])
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key)
        self.add_controller(keys)

    def _key(self, _controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        return False

    def submit(self):
        if self.answered:
            return
        self.answered = True
        username, password = self.username.text, self.password.text
        self.password.set_text('')
        self.on_answer(self.prompt_id, True, username, password)

    def cancel(self):
        if self.answered:
            return
        self.answered = True
        self.password.set_text('')
        self.on_answer(self.prompt_id, False, '', '')

    def grab_focus(self):
        return self.username.grab_focus()
