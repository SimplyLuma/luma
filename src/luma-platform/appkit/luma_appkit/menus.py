"""The menu: the toolkit's own, built from the command model.

Every menu in Luma is a `GtkPopoverMenu`. LumaUI's toolkit sheet
(lumaui-toolkit.css, ADR-052) gives it the context-menu contract — container,
rows, headings, shortcuts, the destructive row — so a text view's
cut-and-paste menu, a menu button, the identity menu and an application's
right-click all look the same without any of them knowing about this module. What this module adds is the bridge from the kit's command
model: a `CommandRegistry` becomes a `GMenuModel` whose actions live on the
menu (or on the widget that owns it), so shortcuts, semantics and menus keep
driving one command from one place.

A `Menu` draws every command row itself (`_custom_row`): the label, the
shortcut as keycaps, the check at the trailing edge, a destructive command, a
choice that explains itself on a second line. GTK's own model rows do the
first three only with Luma's GTK patch; the kit's rows do them on any GTK.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402
from .commands import Command, CommandRegistry  # noqa: E402

# v70 .crpop: rows at least 210 wide (lumaui-toolkit.css popover.menu > contents), 6 px padding.
MENU_MIN_ROW_WIDTH = 210
MENU_PADDING = 6

VARIANTS = {"app": "luma-menu-app", "compact": "luma-menu-compact",
            "desktop": "luma-menu-desktop",
            "dock": "luma-menu-dock", "submenu": "luma-menu-submenu"}

_MODIFIERS = {
    "ctrl": "Ctrl", "control": "Ctrl", "cmd": "Ctrl", "command": "Ctrl", "⌘": "Ctrl", "⌃": "Ctrl",
    "shift": "Shift", "⇧": "Shift",
    "alt": "Alt", "option": "Alt", "opt": "Alt", "⌥": "Alt",
    "super": "Super", "win": "Super", "meta": "Super",
    "⌫": "Backspace", "backspace": "Backspace", "⌦": "Delete", "delete": "Delete", "del": "Delete",
    "↩": "Return", "enter": "Return", "return": "Return", "⎋": "Escape", "esc": "Escape", "escape": "Escape",
    "⇥": "Tab", "tab": "Tab", "space": "Space", "␣": "Space",
}

_ACCEL_MODIFIERS = {"Ctrl": "<Control>", "Shift": "<Shift>", "Alt": "<Alt>", "Super": "<Super>"}


def modifier_text(key: str) -> str:
    """The key as this keyboard names it: ⌘ and cmd read Ctrl, ⌫ reads Backspace."""
    return _MODIFIERS.get(key.strip().lower(), _MODIFIERS.get(key.strip(), key.strip()))


def accelerator(shortcut: tuple[str, ...]) -> str:
    """GTK's accelerator string for a shortcut tuple: ("Ctrl", "Q") → "<Control>q"."""
    parts = [modifier_text(part) for part in shortcut if part.strip()]
    if not parts:
        return ""
    modifiers = "".join(_ACCEL_MODIFIERS[p] for p in parts[:-1] if p in _ACCEL_MODIFIERS)
    key = parts[-1]
    if len(key) == 1:
        key = key.lower()
    return modifiers + key


def point_rectangle(x: float, y: float) -> Gdk.Rectangle:
    """A 1x1 rectangle at a point, for `Gtk.Popover.set_pointing_to`.

    `Gdk.Rectangle(x, y, 1, 1)` and `Gdk.Rectangle(x=..)` look right and are
    silently a rectangle at 0,0: PyGObject ignores arguments to boxed types.
    """
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y, rectangle.width, rectangle.height = int(x), int(y), 1, 1
    return rectangle


# A right-click opens the menu on press, under the pointer; the release that
# follows landed on the first item and chose it. Activations this soon after
# the menu appears are that release, not a choice.
RELEASE_GRACE_SECONDS = 0.3


def _action_name(command_id: str) -> str:
    return command_id.replace(".", "-")


def command_actions(registry: CommandRegistry, *, on_invoked: Callable[[], None] | None = None,
                    before_invoke: Callable[[Callable[[], None]], None] | None = None) -> Gio.SimpleActionGroup:
    """One action per command, checked ones stateful, disabled ones disabled.

    `before_invoke`, when given, receives the invocation and decides when to
    run it; a menu uses it to close and let go of its parent first.
    """
    group = Gio.SimpleActionGroup()
    for command in _all_commands(registry):
        name = _action_name(command.id)
        if command.checked is not None:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(bool(command.checked())))
        else:
            action = Gio.SimpleAction.new(name, None)
        action.set_enabled(bool(command.enabled()))

        def activate(_action, _parameter, cid=command.id):
            def run():
                if registry.invoke(cid) and on_invoked is not None:
                    on_invoked()
            if before_invoke is not None:
                before_invoke(run)
            else:
                run()

        action.connect("activate", activate)
        group.add_action(action)
    return group


def _all_commands(registry: CommandRegistry):
    def walk(commands):
        for command in commands:
            if command.visible():
                yield command
                yield from walk(command.children)
    for group in registry.visible_groups():
        yield from walk(group.commands)


def command_menu(registry: CommandRegistry, *, prefix: str = "menu",
                 custom: dict[str, Command] | None = None,
                 decorations: dict | None = None,
                 own_rows: bool = False) -> Gio.Menu:
    """The registry as a `GMenuModel` whose actions are `<prefix>.<id>`.

    `custom` collects the commands that need a custom row (destructive, or
    described); the caller places those with `GtkPopoverMenu.add_child`. When
    no dictionary is given they become ordinary rows — right for a text view's
    extra menu, where there is no popover to hand a widget to. With `own_rows`
    every command that is not a submenu gets a custom row, so the kit draws
    the whole menu (keycaps, the trailing check) whatever GTK the system has.
    """
    def item_for(command):
        item = Gio.MenuItem.new(command.label, f"{prefix}.{_action_name(command.id)}")
        if command.icon:
            item.set_icon(Gio.ThemedIcon.new(command.icon))
        if command.shortcut:
            item.set_attribute_value("accel", GLib.Variant.new_string(accelerator(command.shortcut)))
        children = tuple(c for c in command.children if c.visible())
        if command.children:
            submenu = Gio.Menu()
            if decorations is not None:
                key = "luma-header:" + command.id
                header = Gio.MenuItem.new(None, None)
                header.set_attribute_value("custom", GLib.Variant.new_string(key))
                decorations[key] = ("header", command.label, len(children))
                submenu.append_item(header)
            for child in children:
                submenu.append_item(item_for(child))
            item.set_submenu(submenu)
        elif custom is not None and (own_rows or command.destructive or command.description
                                     or command.count is not None):
            custom[command.id] = command
            item.set_attribute_value("custom", GLib.Variant.new_string(command.id))
        return item

    menu = Gio.Menu()
    for group in registry.visible_groups(menu=True):
        if (decorations is not None and group.quick_actions
                and 2 <= len(group.commands) <= 4
                and all(not c.destructive and not c.children for c in group.commands)):
            key = "luma-actions:" + str(len(decorations))
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value("custom", GLib.Variant.new_string(key))
            decorations[key] = ("actions", group.label, group.commands)
            menu.append_item(item)
            continue
        section = Gio.Menu()
        for command in group.commands:
            section.append_item(item_for(command))
        if section.get_n_items():
            menu.append_section(group.label or None, section)
    return menu


class Menu(Gtk.PopoverMenu):
    """A popover menu built from a registry, styled by the toolkit.

    Submenus open beside their row rather than sliding within the same page,
    which is what the contract draws. The variant sets the base width the
    toolkit's stylesheet gives the container.
    """

    def __init__(self, registry: CommandRegistry, *, variant: str = "app",
                 keep_parent: bool = False, phone_placement: str = "drawer") -> None:
        if phone_placement not in ("drawer", "popover"):
            raise ValueError("phone_placement must be drawer or popover")
        self.registry = registry
        self.phone_placement = phone_placement
        # A menu built per click lets go of its parent after a choice; one that
        # belongs to a menu button (the identity) stays where it is.
        self._keep_parent = keep_parent
        self._checked_rows = []
        self.variant = variant if variant in VARIANTS else "app"
        self._custom: dict[str, Command] = {}
        decorations = {}
        # Every row is the kit's own (ADR-052): GTK's model rows put the check
        # first and print a shortcut as one label unless GTK itself is patched.
        model = command_menu(registry, custom=self._custom, decorations=decorations, own_rows=True)
        super().__init__(menu_model=model, flags=Gtk.PopoverMenuFlags.NESTED)
        self._actions = command_actions(registry, before_invoke=self._close_then)
        # Below the point it opens from, whichever way a caller pops it up.
        self.set_position(Gtk.PositionType.BOTTOM)
        self._action_commands = tuple(_all_commands(registry))
        self.insert_action_group("menu", self._actions)
        self.connect("show", self._refresh_state)
        self.connect("show", self._remember_shown)
        self.connect("show", self._shown_as_popover)
        self._shown_at = 0.0
        self.set_has_arrow(False)
        # GTK's own scroller inside a popover menu asks for its scrollbar's
        # minimum height, so a one-row menu grew empty space under its row
        # (Nick: uneven padding under "Delete contact"). v70's menus are as
        # tall as their rows; a menu taller than the screen still scrolls (wheel,
        # keys), without a bar of its own.
        for scroller in (w for w in _descendants(self) if isinstance(w, Gtk.ScrolledWindow)):
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.add_css_class("luma-menu-popover")
        self.add_css_class(VARIANTS[self.variant])
        if any(kind == "actions" for kind, _label, _value in decorations.values()):
            self.add_css_class("luma-menu-with-actions")
        # Each nested GtkPopoverMenu owns its own custom slots. add_child on
        # the root alone silently loses all described submenu choices.
        popovers = [self] + [w for w in _descendants(self) if isinstance(w, Gtk.PopoverMenu)]
        for popover in popovers[1:]:
            popover.add_css_class(VARIANTS["submenu"])
            popover.connect("show", self._position_submenu)
        for command_id, command in self._custom.items():
            self._place_custom(popovers, self._custom_row(command), command_id)
        for key, (kind, label, value) in decorations.items():
            child = self._action_strip(label, value) if kind == "actions" else self._submenu_heading(label, value)
            self._place_custom(popovers, child, key)

    def _remember_shown(self, _popover) -> None:
        self._shown_at = GLib.get_monotonic_time() / 1e6

    def set_pointing_to(self, rectangle) -> None:
        self._pointing_empty = rectangle is None or (rectangle.width == 0 and rectangle.height == 0
                                                     and rectangle.x == 0 and rectangle.y == 0)
        Gtk.PopoverMenu.set_pointing_to(self, rectangle)

    def popup(self) -> None:
        # Ordinary menus use a phone drawer. Identity menus may remain anchored
        # to the title row, which stays visible on a phone.
        if self._present_as_drawer():
            return
        # Callers that built their rectangle with Gdk.Rectangle(x, y, 1, 1) handed
        # over 0,0: open where the pointer is instead of the parent's corner.
        if getattr(self, "_pointing_empty", False):
            point = self._pointer_in_parent()
            if point is not None:
                Gtk.PopoverMenu.set_pointing_to(self, point_rectangle(*point))
                self.set_position(Gtk.PositionType.BOTTOM)
        Gtk.PopoverMenu.popup(self)

    def popup_below(self, anchor: Gtk.Widget) -> None:
        """Open under `anchor` the way v70's corner menus do (.crpop): the menu's
        left edge 4 px left of the anchor's, 8 px below it, kept 8 px inside the
        window. The menu must already be parented to `anchor`."""
        from .structure_adapt import is_phone
        root = anchor.get_root()
        ok, bounds = anchor.compute_bounds(root) if root is not None else (False, None)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 0, anchor.get_width(), anchor.get_height()
        self.set_pointing_to(rect)
        if ok and not is_phone(anchor):
            # GTK centres the menu's card on the anchor; shift it so its left edge
            # lands where v70 puts it. The card is its rows plus 6 px each side.
            child = self.get_child()
            natural = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1] if child is not None else 0
            width = max(natural, MENU_MIN_ROW_WIDTH) + 2 * MENU_PADDING
            left = min(bounds.get_x() - 4, root.get_width() - width - 8)
            shift = left + width / 2 - (bounds.get_x() + bounds.get_width() / 2)
            self.set_offset(int(round(shift)), 8)
        self.set_position(Gtk.PositionType.BOTTOM)
        self.popup()

    def _present_as_drawer(self) -> bool:
        if self.phone_placement == "popover":
            return False
        parent = self.get_parent()
        if parent is None:
            return False
        from .structure_adapt import is_phone
        if not is_phone(parent):
            return False
        from .structure_drawer import MenuDrawer
        MenuDrawer.present(parent, self.registry, on_closed=lambda: self.emit("closed"))
        return True

    def _shown_as_popover(self, _popover) -> None:
        # A GtkMenuButton pops its popover up from C, past popup(): swap it for
        # the drawer before the popover is ever drawn.
        if not getattr(self, "_swapping", False) and self._present_as_drawer():
            self._swapping = True
            self.popdown()
            self._swapping = False

    def _pointer_in_parent(self):
        parent = self.get_parent()
        native = parent.get_native() if parent else None
        surface = native.get_surface() if native else None
        seat = self.get_display().get_default_seat() if surface else None
        pointer = seat.get_pointer() if seat else None
        if pointer is None:
            return None
        found, surface_x, surface_y, _mask = surface.get_device_position(pointer)
        if not found:
            return None
        offset_x, offset_y = native.get_surface_transform()
        ok, point = native.compute_point(parent, Graphene.Point().init(surface_x - offset_x, surface_y - offset_y))
        return (point.x, point.y) if ok else None

    def _close_then(self, invoke: Callable[[], None]) -> None:
        """Close, let go of the parent, then run the command.

        A command often rebuilds the widget the menu was opened from -- Notes
        clears its sidebar with `while child := list.get_first_child()`. With
        the menu still parented there, that loop met a child a list cannot
        remove and never ended; the app stopped responding.
        """
        if GLib.get_monotonic_time() / 1e6 - self._shown_at < RELEASE_GRACE_SECONDS:
            return
        self.popdown()

        def later() -> bool:
            if self.get_parent() is not None and not self._keep_parent:
                self.unparent()
            invoke()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(later)

    def _refresh_state(self, _popover):
        for command in self._action_commands:
            action = self._actions.lookup_action(_action_name(command.id))
            action.set_enabled(bool(command.visible() and command.enabled()))
            if command.checked is not None:
                action.set_state(GLib.Variant.new_boolean(bool(command.checked())))
        for button, mark, command in self._checked_rows:
            checked = bool(command.checked())
            if mark is not None:
                mark.set_visible(checked)
            (button.add_css_class if checked else button.remove_css_class)("luma-menu-selected")
            button.update_state([Gtk.AccessibleState.CHECKED],
                                [GObject.Value(GObject.TYPE_INT, int(checked))])

    @staticmethod
    def _place_custom(popovers, child, key):
        if not any(popover.add_child(child, key) for popover in popovers):
            raise RuntimeError(f"menu model has no custom slot: {key}")

    @staticmethod
    def _position_submenu(popover):
        row = popover.get_parent()
        # Anchor against the root's edge, including its padding, with the
        # simulator's 7px gap. GTK still owns edge flipping and work-area fit.
        rectangle = Gdk.Rectangle()
        rectangle.x, rectangle.y = -7, -6
        rectangle.width = row.get_allocated_width() + 14
        rectangle.height = row.get_allocated_height()
        popover.set_pointing_to(rectangle)
        popover.set_position(Gtk.PositionType.LEFT if row.get_direction() == Gtk.TextDirection.RTL
                             else Gtk.PositionType.RIGHT)

    @staticmethod
    def _label(text, **kwargs):
        return Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END, **kwargs)

    def _submenu_heading(self, title, count):
        heading = Gtk.Box(spacing=8, accessible_role=Gtk.AccessibleRole.GROUP)
        heading.add_css_class("luma-menu-submenu-heading")
        heading.append(self._label(title, xalign=0, hexpand=True))
        badge = Gtk.Label(label=str(count), valign=Gtk.Align.CENTER)
        badge.add_css_class("luma-menu-count")
        heading.append(badge)
        return heading

    def _action_strip(self, title, commands):
        strip = Gtk.Box(homogeneous=True, accessible_role=Gtk.AccessibleRole.GROUP)
        strip.add_css_class("luma-menu-actions")
        strip.update_property([Gtk.AccessibleProperty.LABEL], [title or "Quick actions"])
        for command in commands:
            button = Gtk.Button(accessible_role=Gtk.AccessibleRole.MENU_ITEM)
            button.add_css_class("luma-menu-action")
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7,
                             valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
            if command.icon:
                column.append(Gtk.Image(icon_name=command.icon, pixel_size=24))
            column.append(self._label(command.label))
            button.set_child(column)
            button.set_action_name("menu." + _action_name(command.id))
            button.update_property([Gtk.AccessibleProperty.LABEL], [command.label])
            strip.append(button)
        return strip

    def _custom_row(self, command: Command) -> Gtk.Widget:
        role = Gtk.AccessibleRole.MENU_ITEM_CHECKBOX if command.checked is not None else Gtk.AccessibleRole.MENU_ITEM
        button = Gtk.Button(hexpand=True, accessible_role=role)
        button.add_css_class("flat")
        button.add_css_class("luma-menu-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        if command.icon:
            line.append(Gtk.Image(icon_name=icons.resolve(command.icon), pixel_size=16))
        if command.description:
            button.add_css_class("two-line")
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
            text.append(self._label(command.label, xalign=0))
            description = self._label(command.description, xalign=0)
            description.add_css_class("luma-menu-description")
            text.append(description)
            line.append(text)
        else:
            line.append(self._label(command.label, xalign=0, hexpand=True))
        if command.destructive:
            button.add_css_class("luma-menu-destructive")
        if command.shortcut:
            keys = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER,
                           accessible_role=Gtk.AccessibleRole.PRESENTATION)
            keys.add_css_class("luma-menu-shortcut")
            for key in command.shortcut:
                cap = Gtk.Label(label=modifier_text(key), accessible_role=Gtk.AccessibleRole.PRESENTATION)
                cap.add_css_class("luma-menu-keycap")
                keys.append(cap)
            line.append(keys)
        if command.count is not None:
            from .content_badges import CountBadge
            line.append(CountBadge(command.count))
        if command.checked is not None:
            checked = bool(command.checked())
            if checked:
                button.add_css_class("luma-menu-selected")
            # v71 lMenu: the chosen row is filled and heavier (.crmi.on), with no check mark.
            mark = None
            state = GObject.Value(GObject.TYPE_INT, int(checked))
            button.update_state([Gtk.AccessibleState.CHECKED], [state])
            self._checked_rows.append((button, mark, command))
        button.set_child(line)
        button.set_action_name("menu." + _action_name(command.id))
        button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                               [command.label, command.description])
        return button

    def present_at_pointer(self, widget: Gtk.Widget, x: float, y: float) -> None:
        """Open with the menu's corner at the pointer, as desktop context menus do.

        A popover below a point is centred on it unless told otherwise, which
        put half of a row's menu to the left of the click and, near a sidebar's
        edge, off the window. Start-aligned, its leading top corner is the
        pointer, and GTK still flips it at the surface's edges.
        """
        if self.get_parent() is None:
            self.set_parent(widget)
        self.set_halign(Gtk.Align.START)
        self.set_pointing_to(point_rectangle(x, y))
        self.set_position(Gtk.PositionType.BOTTOM)
        self.popup()

    def present_for(self, widget: Gtk.Widget, *, focus_first: bool = False) -> None:
        """Open against a widget — the identity pill, or a focused element."""
        if self.get_parent() is None:
            self.set_parent(widget)
        self.set_position(Gtk.PositionType.BOTTOM)
        self.popup()
        if focus_first:
            self.child_focus(Gtk.DirectionType.TAB_FORWARD)


def command_popover(registry: CommandRegistry, *, variant: str = "app") -> Gtk.Popover:
    """The menu, for callers that just want one to parent, point and pop up."""
    menu = Menu(registry, variant=variant)
    # A menu built per click is unparented once it closes, so a row that is
    # right-clicked a hundred times does not carry a hundred dead popovers.
    menu.connect("closed", lambda popover: GLib.idle_add(_unparent, popover))
    return menu


def _unparent(popover: Gtk.Popover) -> bool:
    if popover.get_parent() is not None and not popover.get_visible():
        popover.unparent()
    return False


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def attach_context_menu(widget: Gtk.Widget, registry: CommandRegistry, *,
                        variant: str = "desktop") -> None:
    """Give a widget its own context menu, by pointer and by keyboard.

    Every surface owns its menu: the event does not bubble up into a generic
    one, which is why this is attached per widget rather than installed once.
    A text view or entry already has GTK's menu; give those an extra menu with
    `command_menu` instead of a second popover over the same click.
    """
    if hasattr(widget, "set_extra_menu"):
        widget.insert_action_group("menu", command_actions(registry))
        widget.set_extra_menu(command_menu(registry))
        return
    gesture = Gtk.GestureClick(button=3)

    def pressed(gesture, _presses, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        command_popover(registry, variant=variant).present_at_pointer(widget, x, y)

    gesture.connect("pressed", pressed)
    widget.add_controller(gesture)
    long_press = Gtk.GestureLongPress(touch_only=True)
    long_press.connect("pressed", lambda controller, x, y: pressed(controller, 1, x, y))
    widget.add_controller(long_press)
    keys = Gtk.EventControllerKey()

    def key_pressed(_controller, keyval: int, _code: int, state) -> bool:
        shift_f10 = keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK
        if keyval == Gdk.KEY_Menu or shift_f10:
            command_popover(registry, variant=variant).present_for(widget, focus_first=True)
            return True
        return False

    keys.connect("key-pressed", key_pressed)
    widget.add_controller(keys)


def bar_menu(anchor: Gtk.Widget, items, *, title: str | None = None, label: str = "Menu",
             align: str = "end", width: str | None = None,
             where: str = "bar", width_px: int | None = None):
    """A short menu for `anchor`, the v71 way (`lMenu`): every menu rises from the bar on a phone.

    - On a computer: a floating card under or over the anchor (`FloatingMenu`).
    - On a phone, from a button in an action bar: the bar grows into the menu,
      a panel above its own row; the button is the raised chip and tapping it
      again folds the menu.
    - On a phone, from anywhere else: the bar's frame, on the 16 gutter, 34 up,
      26 round, over a light scrim, with no grabber.

    `items` holds MenuItem, BarAction, a heading string or None (a hairline).
    A row closes the menu after it acts. Returns what was shown.
    `where="anchor", width_px=240` is an undimmed menu over its key (a keypad without a bar).

        menus.bar_menu(button, [MenuItem("Rename", icon="pencil", on_activate=rename), None,
                                MenuItem("Delete", icon="trash-2", on_activate=delete)])
    """
    from .action_bubble import FloatingMenu
    from .action_center import ActionCenter
    from .bar_frame import BarFrame, phone
    from .bar_panel import panel_list

    if where not in ("bar", "anchor"):
        raise ValueError('menu placement must be "bar" or "anchor"')
    if where == "anchor" and width_px is None:
        raise ValueError('an anchored menu needs width_px')
    if where == "bar" and width_px is not None:
        raise ValueError('width_px is only for an anchored menu')
    rows = list(items)
    if where == "anchor":
        return BarFrame.present(anchor, panel_list(rows, label=label), kind="menu", title=title,
                                anchor_width=width_px)
    if not phone(anchor):
        return FloatingMenu(rows, label=label, title=title, width=width).popup(anchor, align=align)
    center = anchor.get_ancestor(ActionCenter)
    if center is not None and center.state in ("bar", "double"):
        content = panel_list(([title] if title else []) + rows, label=label)
        center.grow(f"menu:{id(anchor)}", content, anchor=anchor)
        return content
    return BarFrame.present(anchor, panel_list(rows, label=label), kind="menu", title=title)


__all__ = ["Menu", "accelerator", "attach_context_menu", "bar_menu", "command_actions",
           "command_menu", "command_popover", "modifier_text"]
