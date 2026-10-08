# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the menu drawer.

Every shared menu is the usual popover on a computer and a bottom drawer at
phone width: full width, a grab handle, rows sized for a thumb (46 px), the
window dimmed behind it (v70 `lMenu`, `#lmenu`, `.ldrawer`). Nothing to do
per app: `menus.Menu` (and so `command_popover`, `attach_context_menu`, the
sidebar foot's filter and the corner pill's ···) presents this drawer itself
when the window it opens in is phone-width. It presents through
`LayerHost.present_modal(..., drawer=True)`, the same drawer the destructive
dialog uses: Esc, a tap outside or a swipe down closes it, Tab stays inside,
arrows move between rows, focus returns to what opened it.

A submenu opens as a second page inside the drawer with a Back row, instead
of a popover beside its row, which has nowhere to go on a phone.

    MenuDrawer.present(button, registry)            # what Menu does at phone width
    MenuDrawer.present_model(button, gio_menu)      # a plain Gio.MenuModel

Rules every part follows: docs/developer/kit/lumaui-principles.md and
behaviour.md. CSS lives in luma-appkit-base.css under `/* LumaUI: Menu drawer */`.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .commands import Command, CommandRegistry  # noqa: E402
from .menus import modifier_text  # noqa: E402
from .structure_adapt import arrow_keys  # noqa: E402
from .structure_layers import LayerHost, ModalHandle  # noqa: E402

__all__ = ["MenuDrawer"]


class MenuDrawer(Gtk.Box):
    """A menu as a bottom drawer. Make one with `present()` or `present_model()`."""

    __gtype_name__ = "LumaUIMenuDrawer"

    def __init__(self, *, title: str | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.MENU)
        self.add_css_class("lumaui-menu-drawer")
        handle = Gtk.Box(halign=Gtk.Align.CENTER)
        handle.add_css_class("lumaui-drawer-handle")
        self.append(handle)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=lumaui.duration("morph"), vhomogeneous=False,
                               interpolate_size=True)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           propagate_natural_height=True, child=self.pages)
        self.append(self.scroller)
        self.handle: ModalHandle | None = None
        self.on_closed: Callable[[], None] | None = None
        self.title = title
        self._depth = 0
        if title:
            self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    # ── public API ────────────────────────────────────────────────────────

    @classmethod
    def present(cls, where: Gtk.Widget, registry: CommandRegistry, *, title: str | None = None,
                on_closed: Callable[[], None] | None = None) -> "MenuDrawer":
        """Show `registry`'s commands as a drawer over the window `where` is in."""
        drawer = cls(title=title)
        drawer._registry = registry
        drawer.on_closed = on_closed
        drawer._push(drawer._registry_page(registry, title), root=True)
        drawer._show(where)
        return drawer

    @classmethod
    def present_model(cls, where: Gtk.Widget, model: Gio.MenuModel, *, title: str | None = None,
                      on_closed: Callable[[], None] | None = None) -> "MenuDrawer":
        """Show a Gio.MenuModel as a drawer; its actions are activated on `where`."""
        drawer = cls(title=title)
        drawer._where = where
        drawer.on_closed = on_closed
        drawer._push(drawer._model_page(model, title), root=True)
        drawer._show(where)
        return drawer

    @classmethod
    def present_items(cls, where: Gtk.Widget, rows, *, title: str | None = None,
                      on_closed: Callable[[], None] | None = None) -> "MenuDrawer":
        """Show plain menu rows as a drawer: F3's `MenuItem`s (label, icon or gicon,
        note, selected, on_activate), a heading string, or None for a separator.
        `FloatingMenu` (Open in, the action center's mode menu) presents this way
        at phone width, so every menu in LumaUI has one drawer."""
        drawer = cls(title=title)
        drawer.on_closed = on_closed
        page = drawer._page(title, back=False)
        for row in rows:
            if row is None:
                page.append(cls._separator())
            elif isinstance(row, str):
                page.append(cls._heading(row))
            elif hasattr(row, "menu_widget"):  # rows family (MN1): RichMenuItem, MenuSection
                widget = row.menu_widget(drawer.close)
                widget.add_css_class("drawer")
                page.append(widget)
            else:
                button = cls._row(row.label, icon=getattr(row, "icon", None), gicon=getattr(row, "gicon", None),
                                  note=getattr(row, "note", None), selected=bool(getattr(row, "selected", False)))
                callback = getattr(row, "on_activate", None)
                button.connect("clicked", lambda _b, cb=callback: drawer._choose(cb or (lambda: None)))
                page.append(button)
        drawer._push(page, root=True)
        drawer._show(where)
        return drawer

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self._closed()

    # ── pages ─────────────────────────────────────────────────────────────

    def _page(self, title: str | None, back: bool) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("lumaui-menu-page")
        if back:
            row = self._row(title or "Back", icon="chevron-left", css="back")
            row.connect("clicked", lambda _b: self._pop())
            page.append(row)
        elif title:
            page.append(self._heading(title))
        arrow_keys(page, orientation=Gtk.Orientation.VERTICAL)
        return page

    def _registry_page(self, registry: CommandRegistry, title: str | None) -> Gtk.Box:
        page = self._page(title, back=False)
        first = True
        for group in registry.visible_groups(menu=True):
            if not first:
                page.append(self._separator())
            first = False
            if group.label:
                page.append(self._heading(group.label))
            for command in group.commands:
                page.append(self._command_row(registry, command))
        return page

    def _submenu_page(self, registry: CommandRegistry, command: Command) -> Gtk.Box:
        page = self._page(command.label, back=True)
        for child in command.children:
            if child.visible():
                page.append(self._command_row(registry, child))
        return page

    def _command_row(self, registry: CommandRegistry, command: Command) -> Gtk.Button:
        children = tuple(c for c in command.children if c.visible())
        row = self._row(command.label, icon=command.icon, description=command.description,
                        shortcut=command.shortcut, submenu=bool(children), count=command.count,
                        checked=bool(command.checked()) if command.checked is not None else None,
                        danger=command.destructive)
        row.set_sensitive(bool(command.enabled()))
        if children:
            row.connect("clicked", lambda _b: self._push(self._submenu_page(registry, command)))
        else:
            row.connect("clicked", lambda _b: self._choose(lambda: registry.invoke(command.id)))
        return row

    def _model_page(self, model: Gio.MenuModel, title: str | None, *, back: bool = False) -> Gtk.Box:
        page = self._page(title, back=back)
        self._fill_from_model(page, model, top=True)
        return page

    def _fill_from_model(self, page: Gtk.Box, model: Gio.MenuModel, *, top: bool) -> None:
        wrote = False
        for index in range(model.get_n_items()):
            section = model.get_item_link(index, Gio.MENU_LINK_SECTION)
            label = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_LABEL, GLib.VariantType("s"))
            text = label.get_string() if label is not None else ""
            if section is not None:
                if wrote:
                    page.append(self._separator())
                if text:
                    page.append(self._heading(text))
                self._fill_from_model(page, section, top=False)
                wrote = True
                continue
            submenu = model.get_item_link(index, Gio.MENU_LINK_SUBMENU)
            action = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION, GLib.VariantType("s"))
            target = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_TARGET, None)
            row = self._row(text.replace("_", ""), submenu=submenu is not None)
            if submenu is not None:
                row.connect("clicked", lambda _b, m=submenu, t=text: self._push(self._model_page(m, t.replace("_", ""), back=True)))
            elif action is not None:
                name = action.get_string()
                row.connect("clicked", lambda _b, n=name, v=target: self._choose(lambda: self._where.activate_action(n, v)))
            page.append(row)
            wrote = True

    def _push(self, page: Gtk.Box, *, root: bool = False) -> None:
        self._depth = 0 if root else self._depth + 1
        name = f"page-{self._depth}"
        old = self.pages.get_child_by_name(name)
        if old is not None:
            self.pages.remove(old)
        self.pages.add_named(page, name)
        self.pages.set_visible_child(page)
        if not root:
            GLib.idle_add(lambda: (page.child_focus(Gtk.DirectionType.TAB_FORWARD), False)[1])

    def _pop(self) -> None:
        if self._depth == 0:
            return
        current = self.pages.get_visible_child()
        self._depth -= 1
        previous = self.pages.get_child_by_name(f"page-{self._depth}")
        self.pages.set_visible_child(previous)
        GLib.timeout_add(max(1, self.pages.get_transition_duration()), lambda: (self.pages.remove(current), False)[1])
        GLib.idle_add(lambda: (previous.child_focus(Gtk.DirectionType.TAB_FORWARD), False)[1])

    # ── rows ──────────────────────────────────────────────────────────────

    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          accessible_role=Gtk.AccessibleRole.HEADING)
        label.add_css_class("lumaui-menu-heading")
        return label

    @staticmethod
    def _separator() -> Gtk.Box:
        rule = Gtk.Box(accessible_role=Gtk.AccessibleRole.SEPARATOR)
        rule.add_css_class("lumaui-menu-separator")
        return rule

    @staticmethod
    def _row(label: str, *, icon: str | None = None, description: str = "", shortcut: tuple[str, ...] = (),
             submenu: bool = False, checked: bool | None = None, danger: bool = False, css: str = "",
             count: int | None = None, gicon: object | None = None, note: str | None = None,
             selected: bool = False) -> Gtk.Button:
        role = Gtk.AccessibleRole.MENU_ITEM_CHECKBOX if checked is not None else Gtk.AccessibleRole.MENU_ITEM
        row = Gtk.Button(accessible_role=role)
        row.add_css_class("lumaui-menu-row")
        if css:
            row.add_css_class(css)
        if danger:
            row.add_css_class("danger")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("lumaui-menu-line")
        if selected:
            row.add_css_class("on")
        if gicon is not None:
            glyph = Gtk.Image.new_from_gicon(gicon)
            glyph.add_css_class("lumaui-app-icon")
            line.append(glyph)
        elif icon:
            glyph = icons.image(icon)
            glyph.add_css_class("lumaui-menu-icon")
            line.append(glyph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["lumaui-menu-label"]))
        if description:
            text.append(Gtk.Label(label=description, xalign=0, wrap=True, css_classes=["lumaui-menu-description"]))
        line.append(text)
        if note:
            line.append(Gtk.Label(label=note, css_classes=["lumaui-menu-note"]))
        if count is not None:
            from .content_badges import CountBadge
            line.append(CountBadge(count))
        if checked is not None:
            mark = icons.image("check")
            mark.add_css_class("lumaui-menu-check")
            mark.set_opacity(1 if checked else 0)
            line.append(mark)
            row.update_state([Gtk.AccessibleState.CHECKED], [GObject.Value(GObject.TYPE_INT, int(checked))])
        if submenu:
            chevron = icons.image("chevron-right")
            chevron.add_css_class("lumaui-menu-chevron")
            line.append(chevron)
        row.set_child(line)
        spoken = label + (f", {description}" if description else "") + (f", {note}" if note else "")
        row.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        if shortcut:
            # A phone has no keyboard shortcuts to show; the name still carries it.
            row.update_property([Gtk.AccessibleProperty.KEY_SHORTCUTS], ["+".join(modifier_text(k) for k in shortcut)])
        return row

    # ── presenting ────────────────────────────────────────────────────────

    @staticmethod
    def _above_bar(host: Gtk.Widget, bar: Gtk.Widget) -> int:
        """The card's bottom margin that leaves v70's 8 between it and the bar's top edge: measured
        from where the bar really is (its border box), so a phone's own bar inset counts (Monitor,
        26 Sep: the card sat on the bar, gap 0)."""
        gap = tokens.ACTION_CENTER["bar_gap"] * 2
        pill = getattr(bar, "bar", None)  # the ActionCenter fills its island; its bar is the pill
        ok, bounds = (pill if isinstance(pill, Gtk.Widget) else bar).compute_bounds(host)
        if ok and host.get_height() > 0:
            return max(0, round(host.get_height() - bounds.get_y()) + gap)
        return bar.get_height() + tokens.ACTION_CENTER["bar_bottom"] + gap

    def _show(self, where: Gtk.Widget) -> None:
        host = LayerHost.window_host(where)
        height = host.get_height()
        if height > 0:
            share = tokens.DRAWER["max_height_pct"] / 100
            self.scroller.set_max_content_height(max(120, int(height * share) - 40))
        self.handle = host.present_modal(self, on_cancel=self._closed, drawer=True)
        # A menu from a bar item opens above its bar, which stays in view (v70 .crpop.ldrawer over #mn-bar).
        bar = where
        while bar is not None and not bar.has_css_class("lumaui-action-center"):
            bar = bar.get_parent()
        card = getattr(self.handle, "card", None)
        if bar is not None and card is not None:
            card.set_margin_bottom(self._above_bar(host, bar))
        # Opened from inside a media context (a viewfinder), the drawer keeps its palette.
        if lumaui.in_media_context(where):
            self.add_css_class("lumaui-media")
            card = getattr(self.handle, "card", None)
            if card is not None:
                card.add_css_class("lumaui-media")

    def _choose(self, run: Callable[[], object]) -> None:
        """Close, then run: a command may rebuild the widget the menu opened from."""
        if self.handle is not None:
            self.handle.close()
        self._closed()
        GLib.idle_add(lambda: (run(), False)[1])

    def _closed(self) -> None:
        callback, self.on_closed = self.on_closed, None
        if callback is not None:
            callback()
