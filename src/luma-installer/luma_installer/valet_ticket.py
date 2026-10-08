"""Valet's app-only transaction ticket, composed with LumaUI type and icons.

The two halves and permission controls belong to Valet's v70/v71 surface. Shared
menus, dialogs, toasts, frame, icons and type remain LumaUI parts. CSS below
uses kit tokens and selects Valet's own classes only.

Under 560 px the ticket draws as v71's phone page instead (`valet_phone`): the
same state and the same controls, one column, the decision in the bar.
"""
from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from luma_appkit import AppIcon, BarAction, Card, ContentLitHeader, ProgressLine, apply_type, icons, install_lumaui
from luma_appkit.action_center import make_control
from luma_appkit.lumaui import mobile_form_factor, oklch_rgba
from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.widgets import add_style_builder

from .valet_phone import ValetPhone


_STYLE = """
.valet-main { padding: calc(var(--lumaui-card-padding-top) * 2) calc(var(--lumaui-card-padding-x) * 2); }
.valet-main.home { padding: 0; }
.valet-stub { padding: calc(var(--lumaui-card-padding-top) * 2); padding-right: calc(var(--lumaui-card-padding-top) * 2 - 4px); padding-bottom: calc(var(--lumaui-card-padding-top) * 2 - 4px); }
.valet-hero-glyph { color: @luma_on_media; }
.valet-gone-art { filter: grayscale(1); }
.valet-subtitle { color: @luma_ink_secondary; }
.valet-verified, .valet-signed { color: @luma_good; }
.valet-file { color: @luma_muted; }
.valet-middle, .valet-stub-list { border-spacing: var(--lumaui-card-padding-top); }
.valet-actions { border-spacing: 8px; }
.valet-secondary { min-width: 0; padding: 0 4px; border: 0; box-shadow: none;
  background: transparent; color: @luma_ink_secondary; }
.valet-advanced-button { background: @luma_control_fill; color: @luma_ink_secondary; border-radius: var(--lumaui-ac-tool-radius); }
.valet-progress { background: @luma_well; border-radius: var(--lumaui-field-radius); }
.valet-advanced-panel.in-bar { background: none; box-shadow: none; }
.valet-advanced-panel { background: @luma_menu; border-radius: 0; box-shadow: 0 -1px 0 @luma_hairline, 0 -20px 40px -20px alpha(@luma_media_shadow, .5); }
.valet-home { padding: calc(var(--lumaui-card-padding-top) * 2) calc(var(--lumaui-card-padding-x) * 2); }
.valet-home-icon { background: @luma_chip; border-radius: calc(var(--lumaui-card-padding-x) + var(--lumaui-card-padding-top) / 3); padding: var(--lumaui-card-padding-x); box-shadow: 0 1px 2px @luma_chip_shadow; }
.valet-home-glyph { color: @luma_ink_secondary; }
.valet-home-detail-icon { color: @luma_ink_secondary; }
.valet-home-copy { color: @luma_ink_secondary; }
.valet-ghost { background: @luma_fill; border-radius: var(--lumaui-place-field-radius); }
.luma-treatment-dark .valet-ghost { background: alpha(@luma_on_media, .07); }
.valet-ghost.strong { background: @luma_control_fill; opacity: .7; }
.valet-advanced-top { padding: var(--lumaui-card-padding-top) 20px; }
.valet-advanced-section { padding: var(--lumaui-card-padding-top) 0 calc(var(--lumaui-card-padding-top) + 1px); }
.valet-advanced-section + .valet-advanced-section { box-shadow: inset 0 1px 0 @luma_hairline; }
.valet-advanced-section.valet-advanced-adds { padding-bottom: calc(var(--lumaui-card-padding-top) + 4px); }
.valet-advanced-section:last-child { padding-bottom: 0; }
.valet-advanced-row { padding: calc(var(--lumaui-card-padding-top) / 2) 0; }
.valet-advanced-permission { padding-left: 6px; padding-right: 6px; }
.valet-advanced-fact { box-shadow: inset 0 -1px 0 @luma_hairline; }
.valet-advanced-fact:last-child { box-shadow: none; }
.valet-advanced-icon { color: @luma_ink_secondary; }
.valet-advanced-icon.valet-advanced-muted { color: @luma_muted; }
.valet-advanced-value { color: @luma_ink; }
.valet-advanced-close { color: @luma_ink_secondary; }
.valet-removal-note { color: @luma_muted; }
.valet-note { color: @luma_muted; }
.valet-removed-value { color: @luma_faint; }
.valet-warning { color: @luma_warning_ink; }
.valet-status-removed { color: @luma_danger_ink; }
.valet-stub-total { border-top: 1px solid @luma_hairline; padding-top: var(--lumaui-card-padding-top); }
.valet-stamp { color: @luma_good; box-shadow: inset 0 0 0 1.5px alpha(@luma_good, .55); border-radius: var(--lumaui-field-radius); padding: 4px 10px 4px 8px; margin: 2px 0 16px; }
.valet-stamp-label { color: @luma_good; }
.valet-sha { background: transparent; color: @luma_ink_secondary; }
.valet-sha-value { color: @luma_ink_secondary; }
"""
_style_loaded: set[int] = set()


def install_valet_style() -> None:
    install_lumaui()
    display = Gdk.Display.get_default()
    if display is None or id(display) in _style_loaded:
        return
    add_style_builder(lambda _appearance: _STYLE)
    _style_loaded.add(id(display))


def _label(text: str, role: str, *, wrap: bool = False, xalign: float = 0,
           weight: int | None = None) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if wrap:
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return apply_type(label, role, weight=weight)


def _fixture_picture(start: str, end: str) -> Gdk.Texture | None:
    """Render v70's sample icon recipe as in-memory artwork for AppIcon."""
    colours = []
    for value in (start, end):
        match = re.fullmatch(r"oklch\(([.\d]+) ([.\d]+) ([.\d]+)\)", value)
        if match is None:
            return None
        rgb = oklch_rgba(*(float(part) for part in match.groups()))
        colours.append(rgb.replace("rgba(", "rgb(").rsplit(",", 1)[0] + ")")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="0 0 72 72">'
           f'<defs><linearGradient id="tile" x1="33%" y1="3%" x2="67%" y2="97%">'
           f'<stop stop-color="{colours[0]}"/><stop offset="1" stop-color="{colours[1]}"/>'
           f'</linearGradient></defs><rect width="72" height="72" rx="20" fill="url(#tile)"/></svg>')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.write(svg.encode("utf-8"))
    loader.close()
    return Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())


class ValetTicket(Gtk.Overlay):
    """One ticket for install, update, remove, progress and completion."""

    def __init__(self) -> None:
        install_valet_style()
        super().__init__(hexpand=True, vexpand=True)
        self.set_name("vl-ticket")
        self.phase = "ready"
        self.removing = False
        self.is_phone = False
        self.subtitle: tuple[str, str, bool] = ("", "", False)
        self._facts: list[tuple] = []
        self._choices: list[dict] = []
        self._phone_text: str | None = None
        self._phone_action: str | None = None
        self._phone_queued = 0
        #: Removing on a phone: the heading over what is stored (v71 "Stored on this phone").
        self.phone_stored_heading = "Stored on this phone" if mobile_form_factor() else "Stored on this computer"
        self.layouts = Gtk.Stack(hhomogeneous=False, vhomogeneous=False,
                                 transition_type=Gtk.StackTransitionType.NONE)
        self.set_child(self.layouts)
        self.panels = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.panels.add_css_class("valet-ticket")
        # The ticket's own minimum (the blank ticket is 680 wide) must not hold the window
        # open: under 560 the phone page takes over, so the ticket never asks for its width.
        ticket_room = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                         vscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                         propagate_natural_width=True, propagate_natural_height=True,
                                         has_frame=False, child=self.panels)
        self.layouts.add_named(ticket_room, "ticket")

        self.main = Gtk.Overlay(hexpand=True, vexpand=True)
        self.main.set_name("vl-main")
        self.main.add_css_class("valet-panel")
        self.main_card = Card(self.main, padded=False, recessed=True)
        # The rounded shared Card owns the clipping boundary. The inner
        # Overlay has no rounded CSS box and only clips rectangularly.
        self.main_card.set_overflow(Gtk.Overflow.HIDDEN)
        self.main_card.set_hexpand(True)
        self.main_card.set_vexpand(True)
        self.panels.append(self.main_card)
        self.main.set_overflow(Gtk.Overflow.HIDDEN)
        self.main.set_child(Gtk.Box())
        self.lit = ContentLitHeader(name="Valet")
        self.lit.set_can_target(False)
        self.main.add_overlay(self.lit)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.content.add_css_class("valet-main")
        self.main.add_overlay(self.content)
        self.stub_slot = Gtk.Overlay(vexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.stub_slot.set_size_request(236, -1)
        self.stub = Card(padded=False, recessed=True)
        self.stub.set_hexpand(True)
        self.stub.set_vexpand(True)
        self.stub.set_name("vl-stub")
        self.stub.add_css_class("valet-panel")
        self.stub.add_css_class("valet-stub")
        self.stub_slot.set_child(self.stub)
        self.panels.append(self.stub_slot)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.hero_header = header
        header.add_css_class("valet-hero")
        header.set_name("vl-header")
        self.content.append(header)
        self.hero_art = Gtk.Overlay(valign=Gtk.Align.START)
        self.hero_art.set_size_request(72, 72)
        self.hero_art.set_name("vl-hero-wrap")
        header.append(self.hero_art)
        self.icon = AppIcon(name="Application", size=72)
        self.icon.set_name("vl-hero")
        self.icon.set_halign(Gtk.Align.CENTER)
        self.icon.set_valign(Gtk.Align.CENTER)
        self._icon_source = ("Application", None, None)
        self._gone = False
        self._art_hue = None
        self.hero_art.set_child(self.icon)
        self.hero_glyph = icons.image("image", pixel_size=33)
        self.hero_glyph.add_css_class("valet-hero-glyph")
        self.hero_glyph.set_halign(Gtk.Align.CENTER)
        self.hero_glyph.set_valign(Gtk.Align.CENTER)
        self.hero_glyph.set_visible(False)
        self.hero_art.add_overlay(self.hero_glyph)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        header.append(words)
        self.name = _label("Reading application…", "title-1")
        self.name.set_ellipsize(Pango.EllipsizeMode.END)
        words.append(self.name)
        subtitle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        words.append(subtitle)
        self.sub = _label("", "body")
        self.sub.add_css_class("valet-subtitle")
        self.sub.set_ellipsize(Pango.EllipsizeMode.END)
        subtitle.append(self.sub)
        self.verified_icon = icons.image("shield-check", pixel_size=14)
        self.verified_icon.add_css_class("valet-verified")
        subtitle.append(self.verified_icon)
        self.publisher = _label("", "body")
        self.publisher.add_css_class("valet-subtitle")
        subtitle.append(self.publisher)

        self.middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        self.middle.add_css_class("valet-middle")
        self.middle.set_margin_top(20)
        self.content.append(self.middle)
        self.notice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.notice_icon = icons.image("check", pixel_size=16)
        self.notice_icon.set_visible(False)
        self.notice.append(self.notice_icon)
        self.phase_label = _label("", "body", wrap=True)
        self.notice.append(self.phase_label)
        self.middle.append(self.notice)
        permission_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.permissions_heading = _label("Permissions", "label")
        permission_title.append(self.permissions_heading)
        self.permissions_hint = _label("Tap to allow or block", "caption")
        permission_title.append(self.permissions_hint)
        self.middle.append(permission_title)
        self.permissions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.permissions.set_name("vl-permissions")
        self.middle.append(self.permissions)
        self.keep_choices = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # TODO(kit-request valet-02-permission-choice.md): DT4 ChoiceChips.
        self.keep = Gtk.ToggleButton(label="Keep settings and data", active=True)
        self.remove_everything = Gtk.ToggleButton(label="Remove everything")
        self.remove_everything.set_group(self.keep)
        self.keep_choices.append(self.keep)
        self.keep_choices.append(self.remove_everything)
        self.middle.append(self.keep_choices)
        self.keep_choices.set_visible(False)

        self.spacer = Gtk.Box(vexpand=True)
        self.content.append(self.spacer)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                               halign=Gtk.Align.START)
        self.actions.add_css_class("valet-actions")
        self.content.append(self.actions)
        self.primary = make_control(BarAction(icon="check", label="Install", primary=True))
        primary_line = self.primary.get_child()
        primary_line.get_first_child().set_visible(False)
        self.primary_label = primary_line.get_last_child()
        self.primary.set_size_request(84, 42)
        self.actions.append(self.primary)
        self.secondary = Gtk.Button(label="Cancel")
        self.secondary.add_css_class("valet-secondary")
        self.secondary.set_size_request(66, 38)
        self.actions.append(self.secondary)
        self.progress_bar = Gtk.Overlay()
        self.progress_bar.add_css_class("valet-progress")
        self.progress_bar.set_name("vl-progress")
        self.progress_bar.set_size_request(260, 42)
        self.progress_line = ProgressLine(0, size="hero", label="Installation progress")
        self.progress_bar.set_child(self.progress_line)
        self.progress_text = _label("", "body", xalign=0.5, weight=600)
        self.progress_bar.add_overlay(self.progress_text)
        self.actions.append(self.progress_bar)
        self.progress_bar.set_visible(False)

        self.home = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                            hexpand=True, vexpand=True)
        self.home.set_name("vl-home")
        self.home.add_css_class("valet-home")
        self.home.set_visible(False)
        home_icon = Gtk.Box(halign=Gtk.Align.START)
        home_icon.add_css_class("valet-home-icon")
        home_glyph = icons.image("download", pixel_size=24)
        home_glyph.add_css_class("valet-home-glyph")
        home_icon.append(home_glyph)
        self.home.append(home_icon)
        home_form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.home_form = home_form
        home_form.append(_label("Drop an app here", "title-2"))
        home_copy = _label("Choose an app package, or drop one anywhere on this card.",
                           "body", wrap=True)
        home_copy.add_css_class("valet-home-copy")
        home_form.append(home_copy)
        self.choose_button = make_control(BarAction(icon="download", label="Choose a file…", primary=True))
        choose_line = self.choose_button.get_child()
        choose_line.set_halign(Gtk.Align.CENTER)
        self.choose_button.set_size_request(-1, 38)
        self.choose_button.set_hexpand(False)
        self.choose_button.set_halign(Gtk.Align.START)
        home_form.append(self.choose_button)
        self.home.append(home_form)
        self.content.prepend(self.home)

        self.stamp = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                             halign=Gtk.Align.START)
        self.stamp.add_css_class("valet-stamp")
        self.stamp.append(icons.image("check", pixel_size=13))
        self.stamp_label = _label("", "label", weight=650)
        self.stamp_label.add_css_class("valet-stamp-label")
        self.stamp.append(self.stamp_label)
        self.stamp.set_visible(False)
        self.stub.append(self.stamp)
        self.file_label = _label("", "body")
        self.file_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.file_label.set_max_width_chars(20)
        self.file_label.add_css_class("valet-file")
        self.stub.append(self.file_label)
        self.stub_list = Gtk.Grid(column_spacing=6, row_spacing=2, vexpand=True,
                                  valign=Gtk.Align.START)
        self.stub_list.add_css_class("valet-stub-list")
        self.stub.append(self.stub_list)
        self.stub_total = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.stub_total.add_css_class("valet-stub-total")
        self.stub_total_label = _label("Frees", "small", weight=400)
        self.stub_total.append(self.stub_total_label)
        self.stub_total_value = _label("", "title-2", xalign=1, weight=700)
        self.stub_total_value.set_hexpand(True)
        self.stub_total.append(self.stub_total_value)
        self.stub_total.set_visible(False)
        self.stub.append(self.stub_total)
        self.stub_ghost = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, vexpand=True)
        self.stub_ghost.set_visible(False)
        self._ghost_bars = []
        for _ in range(5):
            pair = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for strong in (False, True):
                line = Gtk.Box(halign=Gtk.Align.START)
                line.add_css_class("valet-ghost")
                if strong:
                    line.add_css_class("strong")
                pair.append(line)
                self._ghost_bars.append((line, strong))
            self.stub_ghost.append(pair)
        self.stub.append(self.stub_ghost)
        self.home_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.home_detail.set_name("vl-home-detail")
        self.home_detail.set_visible(False)
        detail_icon = icons.image("shield-check", pixel_size=22)
        detail_icon.set_halign(Gtk.Align.START)
        detail_icon.add_css_class("valet-home-detail-icon")
        self.home_detail.append(detail_icon)
        self.home_detail.append(_label("Review before installing", "title-2", wrap=True))
        detail_copy = _label("After you choose an app, its details and permissions appear here.",
                             "body", wrap=True)
        detail_copy.add_css_class("valet-home-copy")
        self.home_detail.append(detail_copy)
        self.stub.append(self.home_detail)
        self.advanced = apply_type(Gtk.ToggleButton(label="Advanced"), "small", weight=500)
        self.advanced.add_css_class("valet-advanced-button")
        self.stub.append(self.advanced)

        self.advanced_panel = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                           transition_duration=260,
                                           halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        self.advanced_panel.set_name("vl-advanced")
        # The closed revealer still fills the overlay for animation. It must
        # not intercept clicks intended for Uninstall or Cancel underneath.
        self.advanced_panel.set_can_target(False)
        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        surface.add_css_class("valet-advanced-panel")
        self.advanced_panel.set_child(surface)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top.add_css_class("valet-advanced-top")
        top.append(_label("Advanced", "title-2", weight=700))
        self.advanced_file = _label("", "small")
        self.advanced_file.set_margin_start(10)
        self.advanced_file.set_ellipsize(Pango.EllipsizeMode.END)
        self.advanced_file.set_hexpand(True)
        top.append(self.advanced_file)
        close = Gtk.Button(icon_name="lumaui-x-symbolic", halign=Gtk.Align.END, hexpand=True)
        close.add_css_class("valet-advanced-close")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda _b: self.advanced.set_active(False))
        top.append(close)
        surface.append(top)
        self.advanced_contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.advanced_contents.set_size_request(520, -1)
        self.advanced_contents.set_halign(Gtk.Align.CENTER)
        advanced_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        advanced_wrap.append(self.advanced_contents)
        scroll = Gtk.ScrolledWindow(vexpand=True, child=advanced_wrap)
        # Grown in the phone bar it is as tall as it needs, up to v71's 520 less its 52 header.
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(468)
        surface.append(scroll)
        self.advanced_surface = surface
        # The closed revealer still fills the overlay for its animation; it must
        # not take clicks meant for the ticket or the phone bar underneath.
        self.advanced_panel.set_can_target(False)
        self.add_overlay(self.advanced_panel)
        self.advanced.connect("toggled", self._advanced_toggled)

        self.phone = ValetPhone(self)
        self.layouts.add_named(self.phone, "ticket-phone")
        self.layouts.set_visible_child_name("ticket")
        self._watch_phone_state()
        self.set_icon("application-x-executable")
        self.set_phase("ready", "Install")
        # v71: a window that crosses 560 px redraws in the other shape, either way.
        self._width_watch = WidthWatch(self, on_tier=lambda tier: self.set_phone(tier == "phone"))

    def _advanced_toggled(self, button: Gtk.ToggleButton) -> None:
        opened = button.get_active()
        bar = self.phone.bar
        if self.is_phone and opened and bar.state in ("bar", "double"):
            # v71: on a phone Advanced grows the bar, its row held below; folding it unticks Advanced.
            self.advanced_panel.set_child(None)
            self.advanced_surface.add_css_class("in-bar")  # the bar's glass is its surface (v71 .vlp-adv)
            if bar.grown != "advanced":
                bar.grow("advanced", self.advanced_surface,
                         on_fold=lambda: self.advanced.set_active(False))
            return
        if bar.grown == "advanced":
            bar.fold_panel()
        self.advanced_surface.remove_css_class("in-bar")
        if self.advanced_surface.get_parent() is None:
            self.advanced_panel.set_child(self.advanced_surface)
        # In a window Advanced covers the ticket (v70 `.vlt .vladv`).
        self.advanced_panel.set_can_target(opened)
        self.advanced_panel.set_reveal_child(opened)

    # ── phone ─────────────────────────────────────────────────────────────

    def set_phone(self, phone: bool) -> None:
        """Draw as the phone page (under 560 px) or as the ticket; the state is the same."""
        if phone == self.is_phone and self.layouts.get_visible_child_name() == ("ticket-phone" if phone else "ticket"):
            return
        opened = self.advanced.get_active()
        if opened:
            self.advanced.set_active(False)
        self.is_phone = phone
        self.layouts.set_visible_child_name("ticket-phone" if phone else "ticket")
        if phone:
            self.phone.render()
        else:
            self.phone.hide_bar()
        self._update_layout()
        if opened:
            self.advanced.set_active(True)

    def _queue_phone(self, *_args) -> None:
        if self.is_phone and not self._phone_queued:
            self._phone_queued = GLib.idle_add(self._render_phone)

    def _render_phone(self) -> bool:
        self._phone_queued = 0
        if self.is_phone:
            self.phone.render()
        return GLib.SOURCE_REMOVE

    def _watch_phone_state(self) -> None:
        """The phone page follows the ticket's controls, whoever changes them."""
        for widget, names in ((self.primary, ("sensitive", "visible")), (self.primary_label, ("label",)),
                              (self.secondary, ("sensitive", "visible", "label")),
                              (self.phase_label, ("label",)), (self.progress_bar, ("visible",)),
                              (self.progress_text, ("label",)), (self.advanced, ("sensitive", "visible")),
                              (self.keep, ("active", "sensitive")), (self.keep_choices, ("visible",)),
                              (self.stamp, ("visible",)), (self.stamp_label, ("label",)),
                              (self.name, ("label",)), (self.file_label, ("label",)),
                              (self.stub_total, ("visible",)), (self.stub_total_value, ("label",)),
                              (self.home, ("visible",))):
            for name in names:
                widget.connect(f"notify::{name}", self._queue_phone)
        self.phase_label.connect("notify::css-classes", self._queue_phone)

    def choose_anchor(self) -> Gtk.Widget:
        """What a menu for Choose a file rises from: the bar's button on a phone."""
        return (self.phone.choose_anchor if self.is_phone and self.phone.choose_anchor is not None
                else self.choose_button)

    def phone_page_key(self) -> tuple:
        """Everything the phone page shows, so it redraws only when that changes."""
        return (self.home.get_visible(), self.removing, self.phase, self._icon_source[:2], id(self._icon_source[2]),
                self._gone, self.hero_glyph.get_visible() and self.hero_glyph.get_icon_name(),
                self.name.get_label(), self.subtitle, self.phone_status(), tuple(map(tuple, self._facts)),
                self.file_label.get_text(), self.phone_warning(), self.phone_stored_heading,
                tuple((row["label"], row["icon"], row["detail"], row["editable"], row["state"],
                       row["control"].get_active()) for row in self._choices),
                self.keep_choices.get_visible(), self.keep.get_active(), self.keep.get_sensitive(),
                self.stub_total.get_visible(), self.stub_total_label.get_label(), self.stub_total_value.get_label(),
                self.advanced.get_visible(), self.advanced.get_sensitive())

    def phone_bar_key(self) -> tuple:
        return (self.home.get_visible(), self.primary.get_visible(), self.primary.get_sensitive(),
                self.phone_action(), self.primary.has_css_class("danger"),
                self.secondary.get_visible(), self.secondary.get_sensitive(), self.secondary.get_label(),
                self.progress_bar.get_visible())

    def phone_action(self) -> str:
        return self._phone_action or self.primary_label.get_label()

    def phone_facts(self) -> list[tuple]:
        return list(self._facts)

    def phone_has_permissions(self) -> bool:
        return bool(self._choices)

    def phone_permissions(self) -> list[dict]:
        return list(self._choices)

    def _notice_is_warning(self) -> bool:
        return "won’t" in self.phase_label.get_label()

    def phone_warning(self) -> str:
        """A compatibility warning: on a phone it is its own note under the facts (v71 `.vtwarn`)."""
        return self.phase_label.get_label() if self._notice_is_warning() and self.phase != "done" else ""

    def phone_status(self) -> tuple[str, bool, bool]:
        """(the line under the name, whether it is the finished check, whether it is an error)."""
        text = self.phase_label.get_label() if self._phone_text is None else self._phone_text
        if self._notice_is_warning() and self._phone_text is None:
            text = ""
        done = self.phase == "done"
        if done and self.stamp.get_visible():
            stamp = self.stamp_label.get_label()
            text = f"{stamp}. {text}" if text else stamp
        return text, done, self.phase_label.has_css_class("error")

    def _replace_icon(self, name: str, *, app_id: str | None = None,
                      picture: Gdk.Paintable | None = None) -> None:
        self._icon_source = (name, app_id, picture)
        replacement = AppIcon(app_id=app_id, picture=picture, name=name,
                              size=64 if self._gone else 72)
        if self._gone:
            replacement.set_size_request(66, 66)
        replacement.set_name("vl-hero")
        replacement.set_halign(Gtk.Align.CENTER)
        replacement.set_valign(Gtk.Align.CENTER)
        self.hero_art.set_child(replacement)
        self.icon = replacement
        self._queue_phone()

    def set_gone(self, gone: bool) -> None:
        if gone == self._gone:
            return
        self._gone = gone
        self.hero_art.set_opacity(.45 if gone else 1.0)
        if gone:
            self.hero_art.add_css_class("valet-gone-art")
        else:
            self.hero_art.remove_css_class("valet-gone-art")
        self.hero_glyph.set_pixel_size(30 if gone else 33)
        name, app_id, picture = self._icon_source
        self._replace_icon(name, app_id=app_id, picture=picture)

    def set_icon(self, icon: str | Gio.Icon | None) -> None:
        self.set_gone(False)
        name = self.name.get_label()
        self._art_hue = None
        self.hero_glyph.set_visible(False)
        if isinstance(icon, str) and icon.startswith("/"):
            try:
                picture = Gdk.Texture.new_from_file(Gio.File.new_for_path(icon))
            except GLib.Error:
                picture = None
            self._replace_icon(name, picture=picture)
        else:
            self._replace_icon(name, app_id=icon if isinstance(icon, str) else None)

    def set_fixture_icon(self, app: dict, *, gone: bool = False) -> None:
        self.set_gone(gone)
        self._replace_icon(app["n"], picture=_fixture_picture(app["a"], app["b"]))
        match = re.fullmatch(r"oklch\([.\d]+ [.\d]+ ([.\d]+)\)", app["a"])
        self._art_hue = float(match.group(1)) if match else None
        self.hero_glyph.set_from_icon_name(icons.icon_name(app["g"]))
        self.hero_glyph.set_visible(True)

    def set_lit_name(self, name: str) -> None:
        self.lit.set_source(name=name, hue=self._art_hue)

    def set_subtitle(self, description: str, publisher: str = "", *, verified: bool = False) -> None:
        self.subtitle = (description, publisher, verified)
        self._queue_phone()
        self.sub.set_label(description + (' ·' if publisher else ''))
        self.publisher.set_label(publisher)
        self.verified_icon.set_visible(bool(publisher) and verified)

    def set_home(self, home: bool) -> None:
        self.home.set_visible(home)
        if home:
            self.main.remove_css_class("valet-panel")
            self.content.add_css_class("home")
            self.stub.add_css_class("home")
        else:
            self.main.add_css_class("valet-panel")
            self.content.remove_css_class("home")
            self.stub.remove_css_class("home")
        self.lit.set_visible(not home)
        self.hero_header.set_visible(not home)
        self.middle.set_visible(not home)
        self.spacer.set_visible(not home)
        self.actions.set_visible(not home)
        self.file_label.set_visible(not home)
        self.stub_list.set_visible(not home)
        self.stub_ghost.set_visible(False)
        self.home_detail.set_visible(home)
        self.advanced.set_visible(not home)
        self._update_layout()

    def set_facts(self, facts: list[tuple[str, str]], *, columns: int = 2) -> None:
        while (child := self.stub_list.get_first_child()) is not None:
            self.stub_list.remove(child)
        self._fact_columns = columns
        self._facts = [tuple(fact) for fact in facts]
        self._queue_phone()
        self.stub_list.set_column_homogeneous(columns > 1)
        self.stub_list.set_row_spacing(2)
        for index, fact in enumerate(facts):
            key, value, *status = fact
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            row.append(_label(key, "caption", weight=400))
            value_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            item = _label(value, "body", weight=600)
            if status and status[0] in {"kept", "retained"}:
                item.add_css_class("valet-removal-note")
            elif status and status[0] == "gone":
                item.add_css_class("valet-removed-value")
            item.set_selectable(True)
            item.set_ellipsize(Pango.EllipsizeMode.END)
            item.set_max_width_chars(14 if columns > 1 and key != "Signed by" else 24)
            if key == "Signed by" and value not in {"Not signed", "Not declared", ""}:
                signed_icon = icons.image("shield-check", pixel_size=13)
                signed_icon.add_css_class("valet-signed")
                value_line.append(signed_icon)
            value_line.append(item)
            if status and status[0] in {"kept", "removed"}:
                status_label = _label(status[0], "caption", xalign=1, weight=600)
                if status[0] == "removed":
                    status_label.add_css_class("valet-status-removed")
                status_label.set_hexpand(True)
                value_line.append(status_label)
            row.append(value_line)
            self.stub_list.attach(row, index % columns, index // columns,
                                  columns if key == "Signed by" and columns > 1 else 1, 1)
        self._update_layout()

    def set_total(self, label: str, value: str) -> None:
        self.stub_total_label.set_label(label)
        self.stub_total_value.set_label(value)
        self.stub_total.set_visible(True)

    def set_stamp(self, value: str | None) -> None:
        self.stamp_label.set_label(value or "")
        self.stamp.set_visible(bool(value))
        self.file_label.set_visible(not bool(value) and not self.home.get_visible())
        self._update_layout()

    def set_permissions(self, choices: list[tuple]) -> None:
        """(label, Lucide icon, allowed, editable, on_change[, reason[, says_state]]) per choice.

        `reason` is the line a phone row shows under the name (v71 "Syncs presets");
        `says_state` False keeps a fixed row quiet instead of saying Allowed (the sandbox).
        """
        while (child := self.permissions.get_first_child()) is not None:
            self.permissions.remove(child)
        self._choices = []
        self.permissions_heading.set_visible(bool(choices))
        self.permissions_hint.set_visible(bool(choices) and any(choice[3] for choice in choices))
        self._has_permissions = bool(choices)

        def control(label, icon, allowed, editable):
            button = Gtk.ToggleButton(active=allowed, sensitive=editable)
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            line.append(icons.image(icon, pixel_size=14))
            line.append(_label(label, "body"))
            button.set_child(line)
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [label + (": allowed" if allowed else ": not allowed")])
            button.set_tooltip_text(label + (": allowed" if allowed else ": not allowed"))
            return button

        for label, icon, allowed, editable, on_change, *more in choices:
            reason = more[0] if more else ""
            says_state = more[1] if len(more) > 1 else True
            desktop = control(label, icon, allowed, editable)

            def changed(widget, callback=on_change, name=label):
                state = name + (": allowed" if widget.get_active() else ": not allowed")
                widget.set_tooltip_text(state)
                widget.update_property([Gtk.AccessibleProperty.LABEL], [state])
                if callback is not None:
                    callback(widget.get_active())

            desktop.connect("toggled", changed)
            # The phone row drives this same control, so a choice is made in one place.
            self.permissions.append(desktop)
            self._choices.append({"label": label, "icon": icon, "detail": reason, "editable": editable,
                                  "state": says_state, "control": desktop})
        self._queue_phone()
        self._update_layout()

    def _update_layout(self) -> None:
        """The ticket's own measures (v70); the phone page is laid out by `valet_phone`."""
        if not hasattr(self, "advanced_contents") or not hasattr(self, "phone"):
            return
        home = self.home.get_visible()
        self.panels.set_halign(Gtk.Align.FILL if not home else Gtk.Align.CENTER)
        self.panels.set_valign(Gtk.Align.FILL)
        self.panels.set_vexpand(True)
        self.panels.set_hexpand(not home)
        self.stub_ghost.set_margin_top(10)
        for bar, strong in self._ghost_bars:
            bar.set_size_request(138 if strong else 84, 12 if strong else 8)
        self.stub_list.set_margin_end(4)
        self.stub_total.set_margin_end(8)
        columns = getattr(self, "_fact_columns", 2)
        self.stub_list.set_column_spacing(6)
        self.stub_list.set_margin_top(0 if columns > 1 and self.stamp.get_visible() else 10)
        self.home_form.set_spacing(14)
        self.permissions.set_visible(getattr(self, "_has_permissions", False))
        self.main.set_vexpand(not home)
        self.stub_slot.set_vexpand(not home)
        self.stub_slot.set_hexpand(False)  # the stub keeps its 236; the app half takes the rest
        self.stub.set_vexpand(not home)
        self.main.set_size_request(436 if home else -1, 268 if home else -1)
        self.stub_slot.set_size_request(236, 268 if home else -1)
        self.stub.set_size_request(-1, 268 if home else -1)
        self.file_label.set_max_width_chars(20)
        # Advanced reads at 520 in a window; on a phone it takes the width less the 20 px inset.
        self.advanced_contents.set_size_request(-1 if self.is_phone else 520, -1)
        self.advanced_contents.set_halign(Gtk.Align.FILL if self.is_phone else Gtk.Align.CENTER)
        self.advanced_contents.set_margin_start(20 if self.is_phone else 0)
        self.advanced_contents.set_margin_end(20 if self.is_phone else 0)

    def set_phase(self, phase: str, action: str, text: str = "", fraction: float | None = 0.0,
                  *, removing: bool = False, cancellable: bool = False, phone_text: str | None = None,
                  phone_action: str | None = None) -> None:
        """`phone_text` and `phone_action`, when given, are what the phone page says instead of
        `text` under the name and `action` in the bar (v71's sample: "Remove", not "Remove Harbor")."""
        self.phase = phase
        self.removing = removing
        self._phone_text = phone_text
        self._phone_action = phone_action
        self.primary_label.set_label(action)
        self.primary.update_property([Gtk.AccessibleProperty.LABEL], [action])
        self.primary.set_sensitive(phase != "working")
        self.primary.set_visible(phase != "working")
        self.primary.remove_css_class("danger")
        if removing and phase == "ready":
            self.primary.add_css_class("danger")
        self.phase_label.set_label(text)
        apply_type(self.phase_label, "title-2" if phase == "done" else "body",
                   weight=600 if phase == "done" else None)
        self.notice.set_visible(bool(text))
        glyph = "check" if phase == "done" else "triangle-alert" if "won’t" in text else None
        if glyph == "check":
            self.notice_icon.add_css_class("valet-verified")
        else:
            self.notice_icon.remove_css_class("valet-verified")
        if glyph == "triangle-alert":
            self.phase_label.add_css_class("valet-warning")
            self.notice_icon.add_css_class("valet-warning")
        else:
            self.phase_label.remove_css_class("valet-warning")
            self.notice_icon.remove_css_class("valet-warning")
        self.notice_icon.set_visible(glyph is not None)
        if glyph:
            self.notice_icon.set_from_icon_name(f"lumaui-{glyph}-symbolic")
        self.keep_choices.set_visible(removing and phase == "ready")
        self.middle.set_margin_top(8 if removing and phase == "ready" else 20)
        (self.phase_label.add_css_class if phase == "ready" and text and glyph != "triangle-alert"
         else self.phase_label.remove_css_class)("valet-note")
        after = (self.keep_choices if removing and phase == "ready" else
                 self.permissions if "won’t" in text else None)
        self.middle.reorder_child_after(self.notice, after)
        self.secondary.set_visible(phase in {"ready", "done"} or (phase == "working" and cancellable))
        self.secondary.set_label("Cancel" if phase != "done" else "Done")
        self.advanced.set_sensitive(phase == "ready")
        self.progress_bar.set_visible(phase == "working")
        if not removing:
            self.stub_total.set_visible(False)
        self.set_fraction(fraction)
        self._update_layout()
        self._queue_phone()

    def set_fraction(self, fraction: float | None) -> None:
        self.progress_line.set_fraction(0 if fraction is None else fraction)
        self._queue_phone()

    def set_progress_label(self, label: str) -> None:
        self.progress_text.set_label(label)
        self.progress_line.set_label(label)
