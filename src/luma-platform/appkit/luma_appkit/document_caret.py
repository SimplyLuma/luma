# SPDX-License-Identifier: Apache-2.0
"""A remote document caret, painted without entering the editable text stream."""
from gi.repository import Gdk, Graphene, Pango
from . import lumaui, media_style as style
from .lumaui_tokens import DOCUMENT_CARET as D


def snapshot_document_caret(snapshot, owner, x, y, *, name, hue, text_size=15, label_bounds=None):
    """Paint a peer position; optional (left, right) bounds constrain only its label."""
    def colour(lightness):
        value = Gdk.RGBA()
        value.parse(lumaui.oklch_rgba(lightness, D['chroma_ratio'], hue))
        return value
    height = text_size * D['height_ratio']
    caret = style.rect(x, y, D['width'], height)
    snapshot.push_rounded_clip(style.rounded(caret, D['radius']))
    snapshot.append_color(colour(D['ink_lightness_ratio']), caret)
    snapshot.pop()
    layout = owner.create_pango_layout(name)
    font = Pango.FontDescription.from_string('Figtree')
    font.set_absolute_size(D['label_size'] * Pango.SCALE)
    font.set_weight(Pango.Weight.SEMIBOLD)
    font.set_variations(f"wght={D['label_weight']}")
    layout.set_font_description(font)
    attributes = Pango.AttrList()
    attributes.insert(Pango.attr_letter_spacing_new(round(D["label_tracking"] * Pango.SCALE)))
    layout.set_attributes(attributes)
    width, label_height = layout.get_pixel_size()
    px, py = D['label_padding_x'], D['label_padding_y']
    label_x = x
    if label_bounds is not None:
        left, right = label_bounds
        available = max(0, right - left)
        px = min(px, available / 2)
        if width + 2 * px > available:
            layout.set_width(max(0, int((available - 2 * px) * Pango.SCALE)))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            width, label_height = layout.get_pixel_size()
        label_x = max(left, min(x, right - width - 2 * px))
    bounds = style.rect(label_x, y-label_height-2*py, width+2*px, label_height+2*py)
    snapshot.push_rounded_clip(style.rounded(bounds, D['label_radius'], D['label_radius'], D['label_radius'], 0))
    snapshot.append_color(colour(D['label_lightness_ratio']), bounds)
    snapshot.pop()
    snapshot.save()
    snapshot.translate(Graphene.Point().init(label_x+px, y-label_height-py))
    snapshot.append_layout(layout, style.colour(owner, 'luma_well_on_ink'))
    snapshot.restore()
