# SPDX-License-Identifier: Apache-2.0
"""Transient paragraph layout for native document editors.

The caller supplies a block classifier; the helper owns spacing and indentation
without introducing persisted document tags or moving the user's selection.
"""
from gi.repository import GLib, Gtk, Pango
from .lumaui_tokens import DOCUMENT_TEXT as D
from .document_fonts import ensure_document_fonts


class DocumentBlockLayout:
    def __init__(self, view: Gtk.TextView, classify):
        ensure_document_fonts()
        self.view, self.buffer, self.classify = view, view.get_buffer(), classify
        self.tags = {}
        self.pending = False
        self.applying = False
        self.buffer.connect('changed', self._queue)
        self.buffer.connect_after('apply-tag', self._queue)
        self.buffer.connect_after('remove-tag', self._queue)
        # An empty trailing paragraph has no character range to tag. Its native
        # default must match a normal paragraph before the first letter is typed.
        # Otherwise the caret jumps by paragraph_after on that first insertion.
        view.set_pixels_above_lines(D['paragraph_after'])
        view.set_pixels_below_lines(0)
        self._queue()

    def _queue(self, *_args):
        if not self.pending and not self.applying:
            self.pending = True
            GLib.idle_add(self.refresh)

    def refresh(self):
        self.pending = False
        self.applying = True
        try:
            start, end = self.buffer.get_bounds()
            for tag in self.tags.values():
                self.buffer.remove_tag(tag, start, end)
            previous_after = 0
            previous_kind = None
            count = self.buffer.get_line_count()
            self.view.set_pixels_above_lines(D['paragraph_after'] if count > 1 else 0)
            for line in range(count):
                first = self.buffer.get_iter_at_line(line)[1]
                last = first.copy()
                if not last.forward_line(): last = self.buffer.get_end_iter()
                if first.is_end(): break
                kind, level = self.classify(line)
                listed = kind in ('checklist', 'bulleted', 'numbered')
                before = D['heading_before'] if kind == 'heading' else D['quote_before'] if kind == 'quote' else D['list_gap'] if listed else 0
                after = D['heading_after'] if kind == 'heading' else D['quote_after'] if kind == 'quote' else D['list_gap'] if listed else D['paragraph_after']
                if previous_kind in ('checklist', 'bulleted', 'numbered') and not listed:
                    previous_after = D['paragraph_after']
                above = max(previous_after, before)
                indent = ((D['check_indent'] if kind == 'checklist' else D['list_indent']) * (level + 1)) if listed else D['quote_indent'] if kind == 'quote' else 0
                key = (above, indent, kind)
                if key not in self.tags:
                    self.tags[key] = self.buffer.create_tag(None, pixels_above_lines=above,
                                                          pixels_below_lines=0, left_margin=indent)
                    if kind == "quote":
                        font = Pango.FontDescription()
                        font.set_family(D["quote_family"])
                        font.set_absolute_size(D["quote_size"] * Pango.SCALE)
                        font.set_weight(D["quote_weight"])
                        font.set_style(Pango.Style.ITALIC)
                        # CSS optical sizing follows CSS pixels; Pango defaults to points.
                        font.set_variations(f"opsz={D['quote_size']}")
                        self.tags[key].set_property("font-desc", font)
                        # GtkTextTag line-height suppresses paragraph spacing.
                        # Keep the shared TextView line height and explicit block gaps.
                        self.tags[key].set_property("pixels-above-lines", above + D["quote_padding_y"])
                        self.tags[key].set_property("pixels-below-lines", D["quote_padding_y"])
                tag = self.tags[key]
                tag.set_priority(self.buffer.get_tag_table().get_size()-1)
                self.buffer.apply_tag(tag, first, last)
                previous_after, previous_kind = after, kind
            self.view.set_bottom_margin(previous_after)
        finally:
            self.applying = False
        return GLib.SOURCE_REMOVE


def snapshot_document_quote(snapshot, owner, x, y, height, *, hue=None):
    """Paint a quote's rule; the editor supplies its actual multiline bounds."""
    from gi.repository import Gdk
    from . import lumaui, media_style as style
    colour = style.colour(owner, "luma_accent")
    if hue is not None:
        colour = Gdk.RGBA()
        colour.parse(lumaui.oklch_rgba(D["quote_rule_lightness_ratio"],
                                     D["quote_rule_chroma_ratio"], hue))
    snapshot.append_color(colour, style.rect(x, y, D["quote_rule_width"], height))
