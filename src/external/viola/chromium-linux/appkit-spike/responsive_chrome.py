# SPDX-License-Identifier: GPL-3.0-only
"""Width-based browser chrome; Chromium remains the owner of every tab and command."""
import json
from urllib.parse import urlsplit
from gi.repository import GLib, Gtk
from luma_appkit import ActionCenter, BarAction, BarSearch
from luma_appkit.bar_panel import PanelRow, panel_list


class ResponsiveChrome:
    def __init__(self, host):
        self.host = host
        self.phone = False
        self.query_generation = 0
        self.query_timer = None
        self.search = None
        self.signature = None
        self.panel = None
        self.center = ActionCenter().attach(host.sidebar_layout.overlay)
        self.center.set_name('viola-phone-bar')
        self.center.hide_bar()
        host.add_tick_callback(self.allocated)

    def allocated(self, *_):
        phone = not self.host.mini and 0 < self.host.get_width() < 560
        if phone != self.phone:
            self.phone = self.host.phone = phone
            self.host.address_suggestions.close()
            layout = self.host.sidebar_layout
            layout.hide_peek()
            self.host.toolbar.set_visible(not phone)
            if self.host.phone_device:
                self.host.title_bar.set_visible(not phone)
            self.host.set_phone_bleed(phone)
            layout.fixed.set_visible(not phone and not layout.collapsed)
            layout.handle.set_visible(not phone and not layout.collapsed)
            layout.edge.set_visible(not phone and layout.collapsed)
            if phone:
                self.render(force=True)
            else:
                self.query_generation += 1
                self.center.hide_bar()
                self.panel = None
                self.host.page.grab_focus()
        return GLib.SOURCE_CONTINUE

    def tabs(self):
        state = self.host.state or {}
        unique = {}
        for key in ('favorites', 'pinned', 'today'):
            for tab in state.get(key, []):
                unique.setdefault(tab['id'], tab)
        return list(unique.values())

    def render(self, *, force=False):
        if not self.phone:
            return
        state = self.host.state or {}
        tabs = self.tabs()
        url = state.get('activeUrl', '')
        signature = (state.get('activeTabId'), url, state.get('canGoBack'), len(tabs))
        if not force and signature == self.signature:
            return
        self.signature = signature
        self.query_generation += 1
        self.panel = None
        domain = urlsplit(url).netloc if url.startswith(('https://', 'http://')) else ''
        self.search = BarSearch(domain or 'Search or type an address',
            label='Address and search', text='' if url == 'about:blank' else url,
            opens=True, on_change=self.changed, on_activate=self.navigate)
        self.center.show_bar([
            BarAction('chevron-left', tooltip='Back', sensitive=bool(state.get('canGoBack')),
                      on_activate=lambda: self.host.send('nav:back')),
            self.search,
            BarAction('', str(len(tabs)), text_glyph=True, tooltip=f'{len(tabs)} tabs',
                      on_activate=self.show_tabs),
            BarAction('ellipsis', tooltip='More', on_activate=self.show_more),
        ], fill=True)

    def focus_address(self):
        self.center.open_search(self.search)
        # ActionCenter owns the live field; the BarSearch descriptor is the
        # resting well, not the hosted editor. Select after its focus idle.
        def select_address():
            field = self.host.get_focus()
            if self.center.searching and isinstance(field, Gtk.Editable):
                field.select_region(0, -1)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(select_address)
        self.changed(self.search.text)

    def navigate(self, text):
        if text.strip() and self.host.services:
            self.query_generation += 1
            self.host.submit(lambda: self.host.services.navigate(text))
            self.center.close_search()
            self.center.fold()
            self.panel = None
            self.host.page.grab_focus()

    def changed(self, text):
        self.query_generation += 1
        generation = self.query_generation
        if self.query_timer is not None:
            GLib.source_remove(self.query_timer)
        def query():
            self.query_timer = None
            if not self.phone or not self.host.services or len(text.encode('utf-8')) > 2048:
                return GLib.SOURCE_REMOVE
            tab = (self.host.state or {}).get('activeTabId')
            payload = json.dumps({'q': text, 'mode': 'edit'})
            self.host.submit(lambda: self.host.services.evaluate('overlay',
                'vela.invoke("palette:query", ' + payload + ')'),
                lambda items: self.suggestions(generation, tab, items))
            return GLib.SOURCE_REMOVE
        self.query_timer = GLib.timeout_add(120, query)

    def suggestions(self, generation, tab, items):
        if (not self.phone or generation != self.query_generation or
                tab != (self.host.state or {}).get('activeTabId') or not isinstance(items, list)):
            return
        rows = [PanelRow(item.get('title') or item.get('url', ''),
                         subtitle=item.get('detail') or item.get('url'), icon='search',
                         on_activate=lambda item=item: self.commit_suggestion(item, tab))
                for item in items[:8]]
        if rows and self.center.searching:
            # Suggestions must not take keyboard focus away from the address.
            field = self.host.get_focus()
            self.center.grow(f'address-{generation}', panel_list(rows, label='Address suggestions'))
            if isinstance(field, Gtk.Editable):
                GLib.idle_add(lambda: (field.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def commit_suggestion(self, item, tab):
        self.query_generation += 1
        self.center.close_search()
        self.center.fold()
        self.host.address_suggestions.commit(item, 'edit', tab)

    def show_tabs(self):
        if self.panel == 'tabs':
            self.center.fold(); self.panel = None
            return
        self.query_generation += 1
        state = self.host.state or {}
        rows = []
        for tab in self.tabs():
            rows.append(PanelRow(tab.get('title') or tab.get('url') or 'New tab',
                subtitle=tab.get('url'), icon='globe', current=tab['id'] == state.get('activeTabId'),
                on_activate=lambda key=tab['id']: self.activate_tab(key)))
        rows.append(PanelRow('New tab', icon='plus', on_activate=self.host._new_tab))
        self.center.grow('tabs', panel_list(rows, label='Tabs'))
        self.panel = 'tabs'

    def activate_tab(self, key):
        self.center.fold(); self.panel = None
        self.host.send('tab:activate', {'tabId': key})

    def show_more(self):
        if self.panel == 'more':
            self.center.fold(); self.panel = None
            return
        state = self.host.state or {}
        rows = [PanelRow('Forward', icon='chevron-right', sensitive=bool(state.get('canGoForward')), on_activate=lambda: self.command('nav:forward')),
                PanelRow('Reload', icon='rotate-cw', on_activate=lambda: self.command('nav:reload')),
                PanelRow('Bookmark', icon='star', on_activate=lambda: self.command('tab:bookmark')),
                PanelRow('New private window', icon='eye-off', on_activate=self.host._new_private_window),
                PanelRow('Browser menu', icon='ellipsis', on_activate=self.browser_menu)]
        self.center.grow('more', panel_list(rows, label='More'))
        self.panel = 'more'

    def command(self, channel):
        self.center.fold(); self.panel = None
        self.host.send(channel)

    def browser_menu(self):
        anchor = self.center.bar_row.get_last_child()
        self.host.submit(lambda: self.host.services.open_menu('browser:menu',
            {'anchorRect': {'x': 0, 'y': 0, 'width': 48, 'height': 48}}),
            lambda model: self.host.show_menu(anchor, model, position=Gtk.PositionType.TOP))
