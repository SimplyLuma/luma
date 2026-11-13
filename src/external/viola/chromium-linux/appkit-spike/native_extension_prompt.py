# SPDX-License-Identifier: GPL-3.0-only
"""An extension's install, permission or added notice, grown from the bar.

The engine hands Chromium's extension prompts to the host (engine patch 0006)
as notices on the permission channel. {type: 'extension'} is the install,
re-enable or permission-increase prompt: {id, title, body, details, accept,
cancel}; the accept button answers `permission:respond` with `allow`, Cancel
or Esc with `dismiss`. {type: 'extension-installed'} has no accept label: its
one button (Done) answers `dismiss`.
"""

from gi.repository import Gdk, Gtk, Pango

TYPES = ('extension', 'extension-installed')
MAX_DETAILS = 24


def is_extension_prompt(prompt):
    return isinstance(prompt, dict) and prompt.get('type') in TYPES and isinstance(prompt.get('id'), str)


def _label(text, css):
    label = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
    label.add_css_class(css)
    return label


class ExtensionPromptPanel(Gtk.Box):
    """Title, the heading line, one line per permission, then Cancel and the accept action."""

    def __init__(self, prompt, on_answer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.DIALOG)
        self.add_css_class('lumaui-panel-confirm')
        self.add_css_class('viola-signin')
        self.prompt_id = prompt['id']
        self.on_answer = on_answer
        self.answered = False
        title = str(prompt.get('title') or 'Extension')
        self.append(_label(title, 'lumaui-panel-confirm-title'))
        body = str(prompt.get('body') or '')
        if body:
            self.append(_label(body, 'lumaui-panel-confirm-body'))
        details = prompt.get('details') if isinstance(prompt.get('details'), list) else []
        self.details = [str(line) for line in details[:MAX_DETAILS] if line]
        for line in self.details:
            self.append(_label(line, 'lumaui-panel-confirm-body'))
        buttons = Gtk.Box(homogeneous=True)
        buttons.add_css_class('lumaui-panel-confirm-buttons')
        accept_label = str(prompt.get('accept') or '')
        self.cancel_button = Gtk.Button(label=str(prompt.get('cancel') or ('Cancel' if accept_label else 'Done')),
                                        hexpand=True)
        self.cancel_button.add_css_class('lumaui-panel-confirm-cancel')
        self.cancel_button.connect('clicked', lambda _b: self.cancel())
        buttons.append(self.cancel_button)
        self.accept_button = None
        if accept_label:
            self.accept_button = Gtk.Button(label=accept_label, hexpand=True)
            self.accept_button.add_css_class('viola-signin-action')
            self.accept_button.connect('clicked', lambda _b: self.accept())
            buttons.append(self.accept_button)
        self.append(buttons)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                             [title, '\n'.join([body] + self.details).strip()])
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key)
        self.add_controller(keys)

    def _key(self, _controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        return False

    def accept(self):
        if self.answered or self.accept_button is None:
            return
        self.answered = True
        self.on_answer(self.prompt_id, True)

    def cancel(self):
        if self.answered:
            return
        self.answered = True
        self.on_answer(self.prompt_id, False)

    def grab_focus(self):
        # Cancel takes the focus so Enter never accepts an install by accident.
        return self.cancel_button.grab_focus()
