# SPDX-License-Identifier: GPL-3.0-only
"""Native address suggestions over the existing bounded Chromium service."""
import json
from urllib.parse import urlsplit
from gi.repository import Gdk, GLib, Gtk, Pango
from luma_appkit import Command, CommandGroup, CommandRegistry
from luma_appkit.menus import Menu


class SuggestionMenu(Menu):
    """Keep AppKit rows/actions, but bound text and reuse Chromium favicons."""
    def __init__(self, registry, owner, items):
        self.owner = owner
        self.items = items
        super().__init__(registry)
        self.add_css_class('viola-address-suggestions')

    def _custom_row(self, command):
        row = super()._custom_row(command)
        def constrain(widget):
            if isinstance(widget, Gtk.Label):
                widget.set_ellipsize(Pango.EllipsizeMode.END)
                widget.set_max_width_chars(1)
                widget.set_hexpand(True)
            child = widget.get_first_child()
            while child:
                constrain(child)
                child = child.get_next_sibling()
        constrain(row)
        item = self.items[int(command.id.rsplit('-', 1)[1])]
        icon = row.get_child().get_first_child()
        url = item.get('url', '')
        if (item.get('type') != 'search' and url.startswith(('https://', 'http://'))
                and isinstance(icon, Gtk.Image) and self.owner.host.favicons):
            def loaded(texture):
                if texture is not None and self.owner.popup is self:
                    icon.set_from_paintable(texture)
            # Cache hits can be synchronous; defer until the popup is owned.
            GLib.idle_add(lambda: self.owner.host.favicons.request(url, loaded) or False)
        return row


class NativeAddress:
    def __init__(self, host):
        self.host = host
        self.popup = None
        self.timer = None
        self.generation = 0
        self.restoring_focus = False
        self.search_destination = None
        self.search_texture = None
        host.address.connect('changed', lambda *_: self.changed())
        focus = Gtk.EventControllerFocus()
        focus.connect('leave', lambda *_: GLib.idle_add(self.focus_left))
        host.address.add_controller(focus)

    def refresh_icon(self):
        entry = self.host.address
        if self.search_destination and (self.host.address_focused() or self.owns_focus()):
            if self.search_texture is not None:
                entry.set_icon_from_paintable(Gtk.EntryIconPosition.PRIMARY, self.search_texture)
            else:
                entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, 'system-search-symbolic')
            entry.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY,
                                        'Search with ' + urlsplit(self.search_destination).netloc)
        else:
            connection = (self.host.state or {}).get('activeConnection') or {}
            entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY,
                'channel-secure-symbolic' if connection.get('kind') == 'secure' else 'channel-insecure-symbolic')
            entry.set_icon_tooltip_text(Gtk.EntryIconPosition.PRIMARY, None)

    def update_search_icon(self, generation, tab, items):
        # The existing palette uses Chromium's classifier and configured
        # search destination. Do not invent a second URL heuristic here.
        typed = next((item for item in items if item.get('type') in ('search', 'url')), None)
        destination = typed.get('url', '') if typed and typed.get('type') == 'search' else ''
        parsed = urlsplit(destination)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            self.search_destination = self.search_texture = None
            self.refresh_icon()
            return
        destination = parsed.scheme + '://' + parsed.netloc + '/'
        if destination != self.search_destination:
            self.search_texture = None
        self.search_destination = destination
        self.refresh_icon()
        if self.host.favicons and self.search_texture is None:
            def loaded(texture):
                if (generation == self.generation and self.host.state
                        and self.host.state.get('activeTabId') == tab
                        and (self.host.address_focused() or self.owns_focus())):
                    self.search_texture = texture
                    self.refresh_icon()
            self.host.favicons.request(self.search_destination, loaded)

    def owns_focus(self):
        focus = self.host.get_focus()
        return bool(self.popup and focus and (focus is self.popup or focus.is_ancestor(self.popup)))

    def focus_left(self):
        if not self.host.address_focused() and not self.owns_focus():
            self.close()
        return GLib.SOURCE_REMOVE

    def changed(self):
        if self.restoring_focus:
            return
        self.generation += 1
        # Retain the last classified icon during the debounce. Only a new
        # classifier result, empty input or end of editing changes its meaning.
        if not self.host.address.get_text().strip() or not self.host.address_focused():
            self.search_destination = self.search_texture = None
        self.refresh_icon()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.host.services and self.host.address_focused():
            self.timer = GLib.timeout_add(120, self.query)

    def query(self):
        self.timer = None
        generation = self.generation
        text = self.host.address.get_text()
        if len(text.encode('utf-8')) > 2048:
            self.dismiss_popup()
            return GLib.SOURCE_REMOVE
        # New Tab already has a real identity; suggestions edit that tab.
        mode = 'edit'
        tab = self.host.state.get('activeTabId') if self.host.state else None
        request = json.dumps({'q': text, 'mode': mode})
        self.host.submit(lambda: self.host.services.evaluate('overlay',
            'vela.invoke("palette:query", ' + request + ')'),
            lambda items: self.received(generation, tab, mode, items))
        return GLib.SOURCE_REMOVE

    def received(self, generation, tab, mode, items):
        if (generation != self.generation or not self.host.address_focused()
                or not self.host.state or self.host.state.get('activeTabId') != tab):
            return
        selection = self.host.address.get_selection_bounds()
        position = self.host.address.get_position()
        self.dismiss_popup()
        if not isinstance(items, list):
            return
        self.update_search_icon(generation, tab, items)
        if not items:
            return
        icons = {'search': 'system-search-symbolic', 'history': 'document-open-recent-symbolic',
                 'tab': 'view-grid-symbolic', 'url': 'web-browser-symbolic'}
        commands = tuple(Command('suggestion-' + str(index), item.get('title') or item.get('url', ''),
            lambda item=item: self.commit(item, mode, tab),
            icon=icons.get(item.get('type'), 'web-browser-symbolic'),
            description=item.get('detail') or item.get('url') or ' ') for index, item in enumerate(items[:8]))
        popup = SuggestionMenu(CommandRegistry((CommandGroup(None, commands),)), self, items[:8])
        self.popup = popup
        popup.set_parent(self.host)
        popup.set_autohide(False)
        valid, bounds = self.host.address_field.compute_bounds(self.host)
        if not valid:
            self.dismiss_popup()
            return
        rectangle = Gdk.Rectangle()
        rectangle.x, rectangle.y = int(bounds.get_x()), int(bounds.get_y())
        rectangle.width, rectangle.height = int(bounds.get_width()), int(bounds.get_height())
        popup.set_pointing_to(rectangle)
        popup.set_position(Gtk.PositionType.BOTTOM)
        popup.set_halign(Gtk.Align.START)
        popup.set_size_request(rectangle.width, -1)
        popup.add_tick_callback(self.track_geometry)
        popup.connect('closed', self.closed)
        focus = Gtk.EventControllerFocus()
        focus.connect('leave', lambda *_: GLib.idle_add(self.focus_left))
        popup.add_controller(focus)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.popup_key)
        popup.add_controller(keys)
        self.restoring_focus = True
        try:
            popup.popup()
            self.host.address.grab_focus()
            if len(selection) == 2:
                self.host.address.select_region(*selection)
            else:
                self.host.address.select_region(position, position)
        finally:
            self.restoring_focus = False

    def track_geometry(self, popup, _clock):
        if self.popup is not popup:
            return GLib.SOURCE_REMOVE
        valid, bounds = self.host.address_field.compute_bounds(self.host)
        if valid:
            rectangle = Gdk.Rectangle()
            rectangle.x, rectangle.y = int(bounds.get_x()), int(bounds.get_y())
            rectangle.width, rectangle.height = int(bounds.get_width()), int(bounds.get_height())
            geometry = (rectangle.x, rectangle.y, rectangle.width, rectangle.height)
            if getattr(popup, '_address_geometry', None) != geometry:
                popup._address_geometry = geometry
                popup.set_pointing_to(rectangle)
                popup.set_size_request(rectangle.width, -1)
        return GLib.SOURCE_CONTINUE

    def popup_key(self, _controller, key, _code, _state):
        if key != Gdk.KEY_Escape:
            return False
        self.close()
        self.restoring_focus = True
        try:
            self.host.address.grab_focus()
        finally:
            self.restoring_focus = False
        return True

    def enter_results(self):
        if self.popup:
            self.popup.child_focus(Gtk.DirectionType.TAB_FORWARD)
            return True
        return False

    def commit(self, item, mode, tab):
        if not self.host.state or self.host.state.get('activeTabId') != tab:
            self.close()
            return
        self.close()
        self.host.new_tab_pending = False
        self.host.page.grab_focus()
        def commit():
            if self.host.services.state.get('activeTabId') != tab:
                return
            if mode == 'new':
                self.host.services.send('sidebar', 'tab:new', {})
            else:
                self.host.services.send('sidebar', 'palette:open', {'mode': mode})
            # Match the existing overlay: navigation suggestions commit their
            # validated destination; tab switching retains the opaque token.
            chosen = item if item.get('type') == 'tab' else {
                'type': 'raw', 'url': item['url'], 'title': item['url']}
            self.host.services.send('overlay', 'palette:commit', {'item': chosen, 'mode': mode, 'meta': False})
        self.host.submit(commit)

    def closed(self, popup):
        if self.popup is popup:
            self.popup = None
        GLib.idle_add(lambda: popup.unparent() or False if popup.get_parent() else False)

    def dismiss_popup(self):
        if self.popup:
            popup, self.popup = self.popup, None
            popup.popdown()

    def close(self):
        self.generation += 1
        self.search_destination = self.search_texture = None
        self.refresh_icon()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.dismiss_popup()
