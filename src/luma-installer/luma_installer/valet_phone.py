"""Valet at phone width: one column, like a phone's install prompt (v71 `vlRenderPhone`).

The app (its icon, name and who made it), its facts and signature in one inset
card, what it may use as switch rows, and Advanced as a row. The decision sits
in the LumaUI action bar at the thumb: Cancel and Install (or Update, Reinstall),
the progress while it works, Done and Open when it has finished. Removing shows
the data choice as rows, what is stored, and what it frees; with nothing opened
the page says which files Valet takes and the bar is Choose a file.

This page is a view of the ticket (`ValetTicket`): it reads the ticket's state
and drives the ticket's own controls, so the install and uninstall flows are the
same objects at every width. Valet owns only its surfaces here (the header, the
facts card, the inset lists); the bar, switch, icons, type, progress line and
toasts are LumaUI parts. CSS is layout and kit tokens only.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango

from luma_appkit import (ActionCenter, AppIcon, BarAction, BarProgress, ScrollView, Switch, ToastHost,
                         apply_type, icons)
from luma_appkit import lumaui_tokens as kit_tokens
from luma_appkit.structure_adapt import WidthWatch
from luma_appkit.widgets import add_style_builder

#: Below this window width Valet draws the phone page (v71 `vlNarrow`: under 560; the kit's phone tier).
PHONE_MAX_WIDTH = kit_tokens.PHONE_MAX_WIDTH

#: v71 `.vlp`: the column sits on the 16 px phone gutter.
GUTTER = 16

#: v71 `.vlp-head` uses the shared 96 px hero icon.
HERO_SIZE = 96

FORMATS_TAKEN = "Open a .flatpakref, .deb, .rpm, AppImage, .apk or .exe from Files or a download, and it opens here."

_STYLE = """
/* v71 .vlp: 56 under the clock, the 16 gutter; the room above the bar is the kit's safe area. */
.valet-phone { padding: 56px 16px 0; }
.valet-phone-head { padding: 12px 0 18px; }
.valet-phone-sub, .valet-phone-status { color: @luma_ink_secondary; }
.valet-phone-mark { color: @luma_good; }
.valet-phone-mark.warn, .valet-phone-sig.warn { color: @luma_warning_ink; }
.valet-phone-status.done { color: @luma_good; }
.valet-phone-status.error { color: @luma_danger_ink; }
.valet-phone-hero.gone { filter: grayscale(1); }
.valet-phone-glyph { color: @luma_on_media; }
.valet-phone-facts, .valet-phone-sig, .valet-phone-list, .valet-phone-home-icon {
  background: @luma_well;
  box-shadow: inset 0 0 0 1px @luma_well_ring, inset 0 1px 2px @luma_well_shade, 0 1px 0 @luma_well_lip; }
/* 2026-10-01: facts over the signature, one inset card drawn as v71 draws it, two halves of one well. */
.valet-phone-card { margin: 18px 0 4px; }
.valet-phone-facts { padding: 12px 4px 10px; border-radius: 16px 16px 0 0; }
.valet-phone-fact { padding: 0 4px; box-shadow: inset -1px 0 0 @luma_hairline; }
.valet-phone-fact:last-child { box-shadow: none; }
.valet-phone-fact-key, .valet-phone-file, .valet-phone-hint, .valet-phone-row-sub, .valet-phone-row-state { color: @luma_muted; }
.valet-phone-sig { padding: 10px 14px 12px; border-radius: 0 0 16px 16px;
  box-shadow: inset 0 1px 0 @luma_hairline, inset 0 0 0 1px @luma_well_ring, 0 1px 0 @luma_well_lip; }
.valet-phone-sig.ok image { color: @luma_good; }
.valet-phone-warning { margin-top: 12px; padding: 12px 14px; border-radius: 16px;
  background: alpha(@luma_warning_ink, .12); color: @luma_warning_ink; }
/* v71 h6 margin 22 4 8 meets the list's 10: the margins collapse to 10. */
.valet-phone-heading { margin: 22px 4px 0; color: @luma_ink_secondary; }
.valet-phone-list { border-radius: 20px; margin-top: 10px; }
/* GTK's min-height is the content's: v71's 58 less the 8 + 8 padding. */
.valet-phone-row { min-height: 42px; padding: 8px 14px; border-radius: 0; background: none;
  box-shadow: inset 0 -1px 0 @luma_hairline; }
.valet-phone-row:last-child { box-shadow: none; }
.valet-phone-row > image, .valet-phone-row > box > image { color: @luma_ink_secondary; }
.valet-phone-row.stays .valet-phone-row-state { color: @luma_good; }
.valet-phone-row.gone { opacity: .45; }
.valet-phone-total { margin: 12px 4px 0; }
.valet-phone-home-icon { border-radius: 24px; min-width: 72px; min-height: 72px; color: @luma_ink_secondary; }
.valet-phone-home-copy { color: @luma_muted; }
"""
_style_loaded: set[int] = set()


def install_phone_style() -> None:
    display = Gdk.Display.get_default()
    if display is None or id(display) in _style_loaded:
        return
    add_style_builder(lambda _appearance: _STYLE)
    _style_loaded.add(id(display))


def _label(text: str, role: str, *, weight: int | None = None, xalign: float = 0, wrap: bool = False,
           css: str | None = None, justify: Gtk.Justification | None = None) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if wrap:
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if justify is not None:
        label.set_justify(justify)
    if css:
        label.add_css_class(css)
    return apply_type(label, role, weight=weight)


def _clear(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


class ValetPhone(Gtk.Box):
    """The page and its bar. `render()` redraws from the ticket; the ticket schedules it."""

    def __init__(self, ticket) -> None:
        install_phone_style()
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.set_name("vl-phone")
        self.ticket = ticket
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.column.add_css_class("valet-phone")
        self.column.set_name("vl-phone-page")
        self.scroller = ScrollView(self.column)
        self.host = ToastHost(self.scroller)
        self.append(self.host)
        self.bar = ActionCenter().attach(self.host)
        self.bar.set_name("vl-phbar")
        self.progress = BarProgress(name="Installation progress")
        self.choose_anchor: Gtk.Widget | None = None
        self._home: Gtk.Widget | None = None
        self._width_watch = WidthWatch(self, lambda _width: self._place_home())
        self._page_key: object = None
        self._bar_key: object = None

    # ── drawing ─────────────────────────────────────────────────────────────

    def render(self) -> None:
        """Redraw what changed: the page, the bar, or only the progress in it."""
        t = self.ticket
        root = self.get_root()
        self.column.set_margin_top(getattr(root, "status_inset", 0))
        page_key = t.phone_page_key()
        if page_key != self._page_key:
            self._page_key = page_key
            self._draw_page()
        bar_key = t.phone_bar_key()
        if bar_key != self._bar_key:
            self._bar_key = bar_key
            self._draw_bar()
        self.progress.set_fraction(t.progress_line.fraction)
        self.progress.set_label(t.progress_text.get_label())

    def hide_bar(self) -> None:
        self.bar.hide_bar()
        self._bar_key = None

    def _draw_bar(self) -> None:
        t = self.ticket
        self.choose_anchor = None
        if t.home.get_visible():
            choose = BarAction("folder-open", "Choose a file", primary=True, keep_label=True, fill=True,
                               on_activate=lambda: _press(t.choose_button))
            self.bar.show_bar([choose], fill=True)
            self.choose_anchor = self.bar.bar_row.get_first_child()
            return
        primary, secondary = t.primary, t.secondary
        controls: list[object] = []
        if not t.progress_bar.get_visible() and secondary.get_visible() and secondary.get_label() != t.primary_label.get_label():
            controls.append(BarAction("", secondary.get_label(), fill=True, sensitive=secondary.get_sensitive(),
                                      on_activate=lambda: _press(secondary)))
        if t.progress_bar.get_visible():
            controls.append(self.progress)
        elif primary.get_visible():
            danger = primary.has_css_class("danger")
            controls.append(BarAction("", t.phone_action(), primary=True, danger=danger, fill=True,
                                      sensitive=primary.get_sensitive(), on_activate=lambda: _press(primary)))
        if controls:
            self.bar.show_bar(controls, fill=True)
            if t.progress_bar.get_visible():
                self.progress.widget.set_name("vl-phone-progress")
        else:
            self.bar.hide_bar()

    def _draw_page(self) -> None:
        t = self.ticket
        _clear(self.column)
        home = t.home.get_visible()
        (self.column.add_css_class if home else self.column.remove_css_class)("home")
        if home:
            self._draw_home()
            return
        self._draw_head()
        if t.removing and t.keep_choices.get_visible():
            self._draw_keep_choice()
        facts = t.phone_facts()
        if any(len(fact) > 2 for fact in facts):
            self._draw_stored(facts)
        elif facts:
            self._draw_facts_card(facts)
        warning = t.phone_warning()
        if warning:
            box = Gtk.Box(spacing=8)
            box.set_name("vl-phone-warning")
            box.add_css_class("valet-phone-warning")
            box.append(icons.image("triangle-alert", pixel_size=15))
            words = _label(warning, "body", wrap=True)
            words.set_hexpand(True)
            box.append(words)
            self.column.append(box)
        if t.phone_has_permissions():
            self._draw_permissions()
        if t.advanced.get_visible():
            self._draw_advanced_row()

    def _draw_home(self) -> None:
        home = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.FILL)
        home.set_name("vl-phone-home")
        home.add_css_class("valet-phone-home")
        self._home = home
        tile = Gtk.Box(halign=Gtk.Align.CENTER)
        tile.add_css_class("valet-phone-home-icon")
        glyph = icons.image("download", pixel_size=24)
        glyph.set_hexpand(True)
        glyph.set_halign(Gtk.Align.CENTER)
        tile.append(glyph)
        home.append(tile)
        title = _label("Install an app", "title-1", weight=700, xalign=0.5)
        title.set_margin_top(8)
        home.append(title)
        copy = _label(FORMATS_TAKEN, "body", xalign=0.5, wrap=True, css="valet-phone-home-copy",
                      justify=Gtk.Justification.CENTER)
        copy_clamp = Adw.Clamp(maximum_size=300, tightening_threshold=300, child=copy)
        home.append(copy_clamp)
        self.column.append(home)
        self._place_home()

    def _place_home(self) -> None:
        """v71 `.vlp-home { padding-top: 30% }`: 30% of the column's width (GTK has no % padding)."""
        if self._home is None or self._home.get_parent() is None:
            return
        width = self.column.get_width()
        if width <= 0:
            root = self.get_root()
            width = (root.get_width() - 2 * GUTTER) if root is not None else 0
        self._home.set_margin_top(round(.3 * max(0, width)))

    def _hero(self) -> Gtk.Widget:
        t = self.ticket
        name, app_id, picture = t._icon_source
        art = Gtk.Overlay(halign=Gtk.Align.CENTER)
        art.set_name("vl-phone-hero")
        art.add_css_class("valet-phone-hero")
        icon = AppIcon(app_id=app_id, picture=picture, name=name, size=HERO_SIZE)
        art.set_child(icon)
        if t.hero_glyph.get_visible():
            glyph = Gtk.Image(icon_name=t.hero_glyph.get_icon_name(), pixel_size=round(HERO_SIZE * .46),
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            glyph.add_css_class("valet-phone-glyph")
            art.add_overlay(glyph)
        if t._gone:
            art.add_css_class("gone")
            art.set_opacity(.45)
        elif t.phase == "done" and not t.removing:
            # v71 `.vlhero.done`: the app has parked itself in Apps; its ticket icon steps back.
            art.set_opacity(.35)
        return art

    def _draw_head(self) -> None:
        t = self.ticket
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.set_name("vl-phone-head")
        head.add_css_class("valet-phone-head")
        head.append(self._hero())
        name = _label(t.name.get_label(), "page-title", weight=750, xalign=0.5)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_margin_top(8)
        head.append(name)
        description, publisher, verified = t.subtitle
        line = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        line.append(_label(description + (" ·" if publisher else ""), "lead", weight=400, css="valet-phone-sub"))
        if publisher:
            mark = icons.image("shield-check" if verified else "triangle-alert", pixel_size=15)
            mark.add_css_class("valet-phone-mark")
            if not verified:
                mark.add_css_class("warn")
            line.append(mark)
            line.append(_label(publisher, "lead", weight=400, css="valet-phone-sub"))
        head.append(line)
        status, done, error = t.phone_status()
        if status:
            row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            row.set_name("vl-phone-status")
            if done:
                row.append(icons.image("check", pixel_size=15))
            text = _label(status, "body", weight=600 if done else None, xalign=0.5, wrap=True,
                          justify=Gtk.Justification.CENTER)
            row.append(text)
            for widget in (row, text):
                widget.add_css_class("valet-phone-status")
                if done:
                    widget.add_css_class("done")
                if error:
                    widget.add_css_class("error")
            head.append(row)
        self.column.append(head)

    def _draw_facts_card(self, facts) -> None:
        t = self.ticket
        strip_facts = [fact for fact in facts if fact[0] != "Signed by"][:4]
        signer = next((fact[1] for fact in facts if fact[0] == "Signed by"), "")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.set_name("vl-phone-facts")
        card.add_css_class("valet-phone-card")
        strip = Gtk.Box(homogeneous=True)
        strip.set_name("vl-phone-strip")
        strip.add_css_class("valet-phone-facts")
        for key, value in strip_facts:
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            cell.add_css_class("valet-phone-fact")
            value_label = _label(value, "list-title", weight=700, xalign=0.5)
            value_label.set_ellipsize(Pango.EllipsizeMode.END)
            value_label.set_max_width_chars(1)
            value_label.set_tooltip_text(value)
            cell.append(value_label)
            cell.append(_label(key, "caption", weight=400, xalign=0.5, css="valet-phone-fact-key"))
            strip.append(cell)
        card.append(strip)
        signed = signer not in {"", "Not signed", "Not declared"}
        sig = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sig.set_name("vl-phone-signature")
        sig.add_css_class("valet-phone-sig")
        sig.add_css_class("ok" if signed else "warn")
        who = Gtk.Box(spacing=8)
        who.append(icons.image("shield-check" if signed else "triangle-alert", pixel_size=16))
        who.append(_label(f"Signed by {signer}" if signed else "Not signed", "body", weight=600))
        sig.append(who)
        file_name = t.file_label.get_text()
        if file_name:
            file_label = _label(file_name, "mono-small", css="valet-phone-file")
            file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            file_label.set_margin_start(24)
            sig.append(file_label)
        card.append(sig)
        self.column.append(card)

    def _heading(self, text: str, hint: str = "") -> None:
        heading = Gtk.Box(spacing=8)
        heading.add_css_class("valet-phone-heading")
        heading.append(_label(text, "body", weight=650))
        if hint:
            heading.append(_label(hint, "small", css="valet-phone-hint"))
        self.column.append(heading)

    def _list(self, name: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_name(name)
        box.add_css_class("valet-phone-list")
        box.set_overflow(Gtk.Overflow.HIDDEN)
        self.column.append(box)
        return box

    @staticmethod
    def _row_words(title: str, subtitle: str = "") -> Gtk.Box:
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        words.append(_label(title, "list-title", weight=600))
        if subtitle:
            sub = _label(subtitle, "row-subtitle", css="valet-phone-row-sub", wrap=True)
            words.append(sub)
        return words

    def _draw_permissions(self) -> None:
        t = self.ticket
        rows = t.phone_permissions()
        editable = any(row["editable"] for row in rows)
        self._heading("What it can use", "Tap to allow or block" if editable else "")
        listing = self._list("vl-phone-permissions")
        for row in rows:
            control = row["control"]
            allowed = control.get_active()
            line = Gtk.Box(spacing=12)
            line.append(icons.image(row["icon"], pixel_size=18))
            line.append(self._row_words(row["label"], row["detail"]))
            if row["editable"]:
                # v71: the whole row is the switch (`button.vlp-row` with its `.cfsw`); the kit's
                # switch draws the state and the row takes the tap, so there is one control.
                item = Gtk.Button(accessible_role=Gtk.AccessibleRole.SWITCH)
                switch = Switch(active=allowed)
                switch.set_halign(Gtk.Align.END)
                switch.set_can_target(False)
                switch.set_can_focus(False)
                switch.update_state([Gtk.AccessibleState.HIDDEN], [True])
                line.append(switch)
                item.set_child(line)
                item.update_property([Gtk.AccessibleProperty.LABEL], [row["label"]])
                item.update_state([Gtk.AccessibleState.CHECKED], [allowed])

                def toggled(_button, c=control, s=switch):
                    c.set_active(not c.get_active())
                    s.set_active(c.get_active())
                    _button.update_state([Gtk.AccessibleState.CHECKED], [c.get_active()])

                item.connect("clicked", toggled)
            else:
                item = Gtk.Box(accessible_role=Gtk.AccessibleRole.GROUP)
                item.append(line)
                line.set_hexpand(True)
                if row["state"]:
                    line.append(_label("Allowed" if allowed else "Not allowed", "meta", css="valet-phone-row-state"))
            item.add_css_class("valet-phone-row")
            listing.append(item)

    def _draw_advanced_row(self) -> None:
        t = self.ticket
        listing = self._list("vl-phone-advanced")
        row = Gtk.Button(sensitive=t.advanced.get_sensitive())
        row.add_css_class("valet-phone-row")
        row.set_name("vl-phone-advanced-row")
        line = Gtk.Box(spacing=12)
        line.append(icons.image("list", pixel_size=18))
        line.append(self._row_words("Advanced", "How it runs, network, package"))
        line.append(icons.image("chevron-right", pixel_size=18))
        row.set_child(line)
        row.update_property([Gtk.AccessibleProperty.LABEL], ["Advanced: how it runs, network, package"])
        row.connect("clicked", lambda _b: t.advanced.set_active(not t.advanced.get_active()))
        listing.append(row)

    def _draw_keep_choice(self) -> None:
        t = self.ticket
        listing = self._list("vl-phone-keep")
        keep = t.keep.get_active()
        for button, chosen, title, subtitle in (
                (t.keep, keep, "Keep settings and data", "Reinstalling picks up where you left off"),
                (t.remove_everything, not keep, "Remove everything", "Settings, caches and saved data go too")):
            # Keep is the source of truth: only its being unticked means the data goes.
            row = Gtk.Button(accessible_role=Gtk.AccessibleRole.RADIO, sensitive=t.keep.get_sensitive())
            row.add_css_class("valet-phone-row")
            line = Gtk.Box(spacing=12)
            line.append(icons.image("circle-check" if chosen else "circle", pixel_size=18))
            line.append(self._row_words(title, subtitle))
            row.set_child(line)
            row.update_property([Gtk.AccessibleProperty.LABEL], [title])
            row.update_state([Gtk.AccessibleState.CHECKED], [chosen])
            row.connect("clicked", lambda _b, target=button: target.set_active(True))
            listing.append(row)

    def _draw_stored(self, facts) -> None:
        t = self.ticket
        self._heading(t.phone_stored_heading)
        listing = self._list("vl-phone-stored")
        for key, value, status in facts:
            line = Gtk.Box(spacing=12)
            line.add_css_class("valet-phone-row")
            line.add_css_class({"removed": "goes", "kept": "stays", "gone": "gone"}.get(status, "stays"))
            line.append(self._row_words(key))
            shown = value if status in {"gone", "retained"} else f"{value} · {status}"
            line.append(_label(shown, "meta", css="valet-phone-row-state"))
            listing.append(line)
        if t.stub_total.get_visible():
            total = Gtk.Box()
            total.set_name("vl-phone-total")
            total.add_css_class("valet-phone-total")
            total.append(_label(t.stub_total_label.get_label(), "lead", weight=400))
            value = _label(t.stub_total_value.get_label(), "lead", weight=700, xalign=1)
            value.set_hexpand(True)
            total.append(value)
            self.column.append(total)


def _press(button: Gtk.Button) -> None:
    """Act through the ticket's own control, exactly as a click on it would."""
    if button.get_sensitive() and button.get_visible():
        button.emit("clicked")


__all__ = ["ValetPhone", "PHONE_MAX_WIDTH"]
