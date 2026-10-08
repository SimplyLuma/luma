# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaTextField / HeroTitleField / ParagraphField vs content_field."""


def _field(C, label, purpose=None, placeholder=None, hint=None, value=None):
    field = C.TextField.new(label, purpose)
    if placeholder:
        field.set_placeholder(placeholder)
    if hint:
        field.set_hint(hint)
    if value:
        field.set_text(value)
    return field


def _hero(C, text, editable=True):
    field = C.HeroTitleField.new(text, None)
    field.set_editable_title(editable)
    return field


CASES = [
    ("field", lambda C, Gtk: _field(C, "Server address", "url", "music.example.com"),
     lambda K, Gtk: K.TextField("Server address", placeholder="music.example.com", purpose="url")),
    ("field-hint", lambda C, Gtk: _field(C, "Username", None, hint="The password isn't kept.", value="nick"),
     lambda K, Gtk: K.TextField("Username", value="nick", hint="The password isn't kept.")),
    ("hero", lambda C, Gtk: _hero(C, "Priya Raman"), lambda K, Gtk: K.HeroTitleField("Priya Raman")),
    ("hero-fixed", lambda C, Gtk: _hero(C, "Priya Raman", False),
     lambda K, Gtk: K.HeroTitleField("Priya Raman", editable=False)),
    ("paragraph", lambda C, Gtk: C.ParagraphField.new("", "Add a note", None),
     lambda K, Gtk: K.ParagraphField("", placeholder="Add a note")),
]
