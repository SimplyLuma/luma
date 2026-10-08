"""Shared circular dial and call keys; native button activation stays intact."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from . import icons
from .lumaui_tokens import DIAL_KEY as D


class DialKey(Gtk.Button):
    def __init__(self, value='', *, legend='', icon=None, variant='key', phone=False,
                 label=None, on_activate=None):
        if variant not in ('key', 'tone', 'call', 'action'):
            raise ValueError('DialKey variant must be key, tone, call or action')
        super().__init__()
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class('lumaui-dial-key')
        self.add_css_class(variant)
        if phone: self.add_css_class('phone')
        size=D['tone_phone_size' if phone else 'tone_size'] if variant=='tone' else D['phone_size' if phone else 'size']
        if variant in ('call','action'): size=D['size']
        self.set_size_request(size,size)
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
        if icon:
            content.append(icons.image(icon,pixel_size=D['call_icon_size'] if variant in ('call','action') else D['icon_size']))
        else:
            digit=Gtk.Label(label=value);digit.add_css_class('dial-digit');content.append(digit)
        if legend:
            caption=Gtk.Label(label=legend);caption.add_css_class('dial-legend')
            caption.set_margin_top(D['legend_gap']);content.append(caption)
        self.set_child(content)
        self.update_property([Gtk.AccessibleProperty.LABEL],[label or ' '.join(filter(None,(value,legend))) or (icon or '').replace('-', ' ')])
        if on_activate is not None: self.connect('clicked',lambda _:on_activate())
