# SPDX-License-Identifier: Apache-2.0
"""Messages forms composed from the kit's modal, card and navigation controls."""
from gi.repository import Adw, GObject, Gtk
from luma_appkit import Card, IconOnlyButton, LayerHost, PageHeader


class MessagesDialog(Card):
    __gsignals__ = {'closed': (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, *, title, content_width=440, content_height=640, follows_content_size=False):
        super().__init__()
        self._title, self._width, self._height = title, content_width, content_height
        self._handle = None
        self._finished = False
        self.card = self
        self.card.set_name('messages-dialog-card')
        line = Gtk.Box(spacing=8)
        self.back_button = IconOnlyButton('chevron-left', 'Back', on_click=self._back)
        self.back_button.set_visible(False)
        line.append(self.back_button)
        self.heading = PageHeader(title=title)
        self.heading.set_hexpand(True)
        line.append(self.heading)
        self.close_button = IconOnlyButton('x', 'Close', on_click=self.close)
        line.append(self.close_button)
        self.card.append(line)
        self.content = Adw.Bin(vexpand=True, hexpand=True)
        self.card.append(self.content)
        self._view = None

    def set_child(self, child):
        self.content.set_child(child)
        if isinstance(child, Adw.NavigationView):
            self._view = child
            child.connect('notify::visible-page', lambda *_: self._navigation_changed())
            self._navigation_changed()

    def get_child(self):
        return self.content.get_child()

    def _navigation_changed(self):
        if self._view is None:
            return
        page = self._view.get_visible_page()
        self.heading.set_title(page.get_title() if page else self._title)
        self.back_button.set_visible(bool(page and self._view.get_previous_page(page)))

    def _back(self):
        if self._view is not None:
            self._view.pop()

    def present(self, parent):
        root = parent.get_root() or parent
        host = LayerHost.install(root)
        if host.modal is not None:
            host.modal.cancel()
        self.card.set_size_request(min(self._width, max(280, root.get_width() - 48)),
                                   min(self._height, max(280, root.get_height() - 112)))
        self._handle = host.present_modal(self.card, on_cancel=self.close,
                                         initial_focus=self.close_button)
        self._navigation_changed()

    def close(self):
        if self._finished:
            return
        self._finished = True
        if self._handle is not None:
            self._handle.close()
        self.emit('closed')
