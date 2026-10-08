# SPDX-License-Identifier: Apache-2.0
"""A persistent, scrollable editing palette above the action bar (v71 Viewer)."""
from __future__ import annotations
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,Gdk,GLib
from . import lumaui_tokens as tokens
from .action_center import make_control
from .structure_adapt import WidthWatch,window_width


class ToolPalette(Gtk.Box):
    """Bar actions in their own nonmodal palette; narrow rows scroll, never clip tools away."""
    __gtype_name__='LumaUIToolPalette'

    def __init__(self,items=()):
        super().__init__(visible=False,accessible_role=Gtk.AccessibleRole.TOOLBAR)
        self.add_css_class('lumaui-ac-bar');self.add_css_class('lumaui-tool-palette')
        self.row=Gtk.Box()
        self.row.add_css_class('lumaui-tool-palette-row')
        self.buttons=[]
        for item in items:
            button=make_control(item)
            self.row.append(button);self.buttons.append(button)
            focus=Gtk.EventControllerFocus()
            focus.connect("enter",lambda *_:GLib.idle_add(self._reveal_focus))
            button.add_controller(focus)
        self.scroller=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,propagate_natural_width=True,child=self.row)
        self.scroller.set_min_content_width(1)
        self.append(self.scroller)
        self.scroller.get_hadjustment().connect("changed",lambda *_:GLib.idle_add(self._reveal_focus))
        self._width_watch=WidthWatch(self,self._width,threshold=559)

    def _width(self,width):
        (self.add_css_class if 0<width<560 else self.remove_css_class)('phone')
        self.queue_allocate()

    def _reveal_focus(self):
        focused=next((b for b in self.buttons if b.is_focus()),None)
        if focused is None:return False
        valid,bounds=focused.compute_bounds(self.row)
        if valid:
            adjustment=self.scroller.get_hadjustment()
            start,end=bounds.get_x(),bounds.get_x()+bounds.get_width()
            value,page=adjustment.get_value(),adjustment.get_page_size()
            if start<value:adjustment.set_value(start)
            elif end>value+page:adjustment.set_value(end-page)
        return False

    def attach(self,overlay):
        overlay.add_overlay(self)
        overlay.connect('get-child-position',self._position)
        return self

    def set_shown(self,shown):self.set_visible(shown)

    def set_current(self,button):
        for candidate in self.buttons:
            on=candidate is button
            for css in ('on','primary'):
                (candidate.add_css_class if on else candidate.remove_css_class)(css)
            candidate.update_state([Gtk.AccessibleState.PRESSED],
                [Gtk.AccessibleTristate.TRUE if on else Gtk.AccessibleTristate.FALSE])

    def _position(self,overlay,child,allocation):
        if child is not self:return False
        metrics=tokens.BAR['tool_palette']
        phone=0<window_width(self)<560
        side=int(metrics['phone_side'] if phone else metrics['side'])
        available=max(1,overlay.get_width()-side*2)
        natural=self.measure(Gtk.Orientation.HORIZONTAL,-1)[1]
        width=available if phone else min(available,natural)
        height=self.measure(Gtk.Orientation.VERTICAL,width)[1]
        bottom=int(metrics['phone_bottom'] if phone else metrics['bottom'])
        allocation.x=(overlay.get_width()-width)//2
        allocation.y=max(0,overlay.get_height()-bottom-height)
        allocation.width=width;allocation.height=height
        return True
