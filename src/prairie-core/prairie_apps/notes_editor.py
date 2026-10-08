# SPDX-License-Identifier: Apache-2.0
"""The single Notes formatting toolbar, shared by library and file windows."""
from gi.repository import Gtk
from luma_appkit import ConnectedButtonGroup, IconButton, SaveStatus, Toolbar


class NotesFormattingToolbar(Toolbar):
    def __init__(self, context, on_format, on_link):
        super().__init__()
        self.add_css_class("notes-format-toolbar")
        self.format_buttons: dict[str, Gtk.Button] = {}
        text_group = ConnectedButtonGroup(tint="violet")
        structure_group = ConnectedButtonGroup(tint="amber")
        for style, icon, label, group in (
            ("bold", "luma-format-bold-symbolic", "Bold", text_group),
            ("italic", "luma-format-italic-symbolic", "Italic", text_group),
            ("underline", "luma-format-underline-symbolic", "Underline", text_group),
            ("heading", "luma-format-heading-symbolic", "Heading", structure_group),
            ("quote", "luma-format-quote-symbolic", "Quote", structure_group),
            ("bulleted", "luma-format-list-symbolic", "Bulleted list", structure_group),
            ("numbered", "luma-format-list-ordered-symbolic", "Numbered list", structure_group),
        ):
            button = IconButton(icon, label, context=context, quiet=True)
            button.connect("clicked", lambda _button, value=style: on_format(value))
            group.append(button)
            self.format_buttons[style] = button
        self.append(text_group)
        self.append(structure_group)
        link_group = ConnectedButtonGroup(tint="blue")
        link = IconButton("luma-format-link-symbolic", "Insert link", context=context, quiet=True)
        link.connect("clicked", on_link)
        link_group.append(link)
        self.append(link_group)
        self.save_status = SaveStatus()
        self.save_status.add_css_class("notes-save-status")
        self.save_status.set_hexpand(True)
        self.save_status.set_halign(Gtk.Align.END)
        self.append(self.save_status)
