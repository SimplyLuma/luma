# SPDX-License-Identifier: GPL-3.0-only
"""Width-based browser chrome; Chromium remains the owner of every tab and command."""
from dataclasses import dataclass
import json
from urllib.parse import quote
from urllib.parse import urlsplit
from gi.repository import Gio, GLib, Gtk
from luma_appkit import ActionCenter, BarAction, BarSearch
from luma_appkit.action_center import register_item
from luma_appkit.bar_share import ShareSheet, ShareSubject, ShareTarget
from luma_appkit.bar_panel import PanelRow, panel_list
from native_footer import NativeFooter


class DownloadPanelFooter(NativeFooter):
    def open_notice(self, item):
        self.host.submit(lambda: self.host.services.send('overlay', 'download:open',
                                                       {'id': item['downloadId']}))


@dataclass
class BrowserAddress:
    widget: Gtk.Widget


def address_control(item, size):
    # The browser owns its composite address field; LumaUI owns the bar.
    widget = item.widget
    if widget.get_parent() is not None:
        raise ValueError('Address is still parented')
    widget.set_visible(True)
    widget.set_valign(Gtk.Align.CENTER)
    widget.bar_flexible = True
    widget.bar_phone_wide = True
    return widget


register_item(BrowserAddress, address_control)


class ResponsiveChrome:
    def __init__(self, host):
        self.host = host
        self.phone = False
        self.compact = False
        self.query_generation = 0
        self.query_timer = None
        self.search = None
        self.signature = None
        self.panel = None
        self.bar_hidden = False
        self.scroll_distance = 0
        self.scroll_direction = 0
        self.center = ActionCenter().attach(host.page_layers)
        self.center.set_name('viola-page-bar')
        host.toolbar.remove(host.toggle)
        clamp = host.address_field.get_parent()
        host.toolbar.remove(clamp)
        clamp.set_child(None)
        host.address.set_width_chars(8 if host.mini else 36)
        host.address.set_max_width_chars(20 if host.mini else 36)
        host.address_field.set_sensitive(False)
        host.address_field.set_size_request(100 if host.mini else 180, 36)
        host.toolbar.set_visible(False)
        host.new_tab = host.sidebar.new_tab_row
        self.render(force=True)
        host.add_tick_callback(self.allocated)

    def allocated(self, *_):
        if self.host.mini:
            # Keep the same retained bar/editor. Optional navigation and
            # downloads remain in the browser menu when the popup is narrow.
            dense = 0 < self.host.get_width() < 600
            for widget in (self.host.forward, self.host.reload, self.download_anchor):
                widget.set_visible(not dense)
            return GLib.SOURCE_CONTINUE
        phone = not self.host.mini and 0 < self.host.get_width() < 560
        compact = not self.host.mini and 0 < self.host.get_width() < 900
        if phone != self.phone or compact != self.compact:
            self.reveal_bar()
            self.phone = self.host.phone = phone
            self.compact = compact
            self.host.address_suggestions.close()
            if getattr(self, 'share_sheet', None):
                self.share_sheet.close()
            layout = self.host.sidebar_layout
            layout.hide_peek()
            self.host.toolbar.set_visible(False)
            self.host.toggle.set_visible(not phone)
            if self.host.phone_device:
                self.host.title_bar.set_visible(not phone)
            self.host.set_phone_bleed(phone)
            layout.fixed.set_visible(not compact and not layout.collapsed)
            layout.handle.set_visible(not compact and not layout.collapsed)
            layout.edge.set_visible(not compact and layout.collapsed)
            self.query_generation += 1
            self.center.close_search()
            self.center.fold()
            self.panel = None
            self.render(force=True)
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
        state = self.host.state or {}
        active = (state.get('activeTabId'), state.get('activeUrl'))
        if active != getattr(self, 'page_identity', None):
            self.page_identity = active
            self.reveal_bar()
        if self.panel == 'downloads' and getattr(self, 'downloads_footer', None):
            self.downloads_footer.state = state
            for card in self.downloads_footer.cards.values():
                self.downloads_footer.card(card.item)
        tabs = self.tabs()
        url = state.get('activeUrl', '')
        if not self.phone and getattr(self, 'share_anchor', None):
            self.share_anchor.set_sensitive(url.startswith(('https://', 'http://')))
        # Desktop navigation sensitivity is updated on the retained buttons
        # by render_state. Rebuilding that bar would unparent a focused entry
        # exactly when New Tab delivers its first state.
        signature = (self.phone, state.get('canGoBack') if self.phone else None,
                     state.get('canGoForward') if self.phone else None,
                     len(tabs) if self.phone else None)
        if not force and signature == self.signature:
            if self.phone and self.search and not self.center.searching:
                self.search.set_text('' if url == 'about:blank' else url)
                word = getattr(self.search.widget, 'word', None)
                if word:
                    word.set_label(urlsplit(url).netloc or 'Search or type an address')
            return
        self.signature = signature
        parent = self.host.address_field.get_parent()
        if parent:
            parent.remove(self.host.address_field)
        self.query_generation += 1
        self.panel = None
        domain = urlsplit(url).netloc if url.startswith(('https://', 'http://')) else ''
        if not self.phone:
            self.render_desktop(state)
            return
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

    def render_desktop(self, state):
        actions = [
            BarAction('chevron-left', tooltip='Back', sensitive=bool(state.get('canGoBack')),
                      on_activate=lambda: self.host.send('nav:back')),
            BarAction('chevron-right', tooltip='Forward', sensitive=bool(state.get('canGoForward')),
                      on_activate=lambda: self.host.send('nav:forward')),
            BarAction('rotate-cw', tooltip='Reload', on_activate=lambda: self.host.send('nav:reload')),
            BrowserAddress(self.host.address_field),
            BarAction('share-2', tooltip='Share page', on_activate=self.show_share),
            BarAction('download', tooltip='Downloads', on_activate=self.show_downloads),
            BarAction('ellipsis', tooltip='Browser menu', on_activate=self.browser_menu),
        ]
        if self.host.mini:
            actions.append(BarAction('', 'Open in Viola', primary=True, keep_label=True,
                tooltip='Move this live page into the full browser',
                on_activate=lambda: self.host.window_manager.command(self.host, 'Open in Viola')))
        self.center.show_bar(actions)
        self.host.back = self.center.bar_row.get_first_child()
        self.host.forward = self.host.back.get_next_sibling()
        self.host.reload = self.host.forward.get_next_sibling()
        self.share_anchor = self.host.address_field.get_next_sibling()
        self.download_anchor = self.share_anchor.get_next_sibling()
        self.host.menu_button = self.download_anchor.get_next_sibling()
        if self.host.mini:
            self.expand = self.host.menu_button.get_next_sibling()
            self.expand.set_visible(not getattr(self.host, 'developer_tools', False))
            if hasattr(self.host, 'mini_presentation'):
                self.host.mini_presentation.expand = self.expand
        self.share_anchor.set_sensitive(state.get('activeUrl', '').startswith(('https://', 'http://')))

    def toggle_pin(self):
        state = self.host.state or {}
        key = state.get('activeTabId')
        if not key:
            return
        tab = next((tab for tab in self.tabs() if tab['id'] == key), {})
        pinned = any(tab['id'] == key for tab in state.get('favorites', []))
        self.host.send('tab:reorder', {'tabId': key,
            'spaceId': tab.get('sourceSpaceId') or state.get('activeSpaceId'),
            'section': 'today' if pinned else 'favorite', 'index': 0})
        self.center.fold()
        self.panel = None

    def show_share(self):
        state = self.host.state or {}
        url = state.get('activeUrl', '')
        if not url.startswith(('https://', 'http://')):
            return
        self.center.fold()
        self.panel = None
        targets = [ShareTarget('mail', 'Email', icon='mail')] if Gio.AppInfo.get_default_for_uri_scheme('mailto') else []
        anchor = self.center.bar_row.get_last_child() if self.phone else self.share_anchor
        active = next((tab for tab in self.tabs() if tab['id'] == state.get('activeTabId')), {})
        self.share_sheet = ShareSheet.present(anchor,
            document=ShareSubject(state.get('activeTitle') or active.get('title') or urlsplit(url).netloc,
                                  subtitle=url, kind='link', icon='globe'),
            targets=targets, choices=('send-copy',), on_choice=self.share)
        # The engine exposes no native QR action; show only supported actions.
        code = self.share_sheet.action_buttons.pop('code', None)
        if code:
            code.get_parent().remove(code)

    def share(self, choice, value):
        sheet = getattr(self, 'share_sheet', None)
        if not sheet:
            return None
        # A sheet shares the page it opened for even if the active tab changes.
        url = sheet.document.subtitle
        if choice == 'copy-link':
            self.host.get_display().get_clipboard().set(url)
            return 'Link copied'
        if choice == 'target' and value == 'mail':
            uri = 'mailto:?subject=' + quote(sheet.document.title) + '&body=' + quote(url)
            Gio.AppInfo.launch_default_for_uri(uri, None)
            sheet.close()
        return None

    def show_downloads(self):
        state = self.host.state or {}
        downloads = state.get('downloads') or []
        if not downloads:
            panel = panel_list([PanelRow('No downloads yet', icon='download', sensitive=False)], label='Downloads')
        else:
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            # Cards in the sidebar retain their parent. The panel owns a
            # separate presenter over the same Chromium download state.
            self.downloads_footer = DownloadPanelFooter(self.host)
            self.downloads_footer.state = state
            for download in downloads[-8:]:
                notice = next((item for item in state.get('notifications', [])
                               if item.get('downloadId') == download.get('id')), None)
                item = notice or dict(id='download-' + download['id'], downloadId=download['id'],
                    url='vela://downloads', title=download.get('filename') or 'Download', body='',
                    syntheticDownload=True)
                card = self.downloads_footer.card(item)
                if not notice:
                    card.open.get_parent().get_last_child().set_visible(False)
                panel.append(card)
        self.center.grow('downloads', panel)
        self.panel = 'downloads'

    def reveal_bar(self):
        self.center.bar.set_visible(True)
        self.bar_hidden = False
        self.scroll_distance = 0

    def pointer_moved(self, x, y):
        if self.bar_hidden and y >= self.host.page.get_height() - 72:
            self.reveal_bar()

    def page_scrolled(self, dx, dy):
        if abs(dy) < abs(dx) or not dy:
            return
        # Scrolling the page ends address editing before chrome retreats.
        if self.host.address_focused():
            self.host.address_suggestions.close()
            self.host.page.grab_focus()
        if self.panel or self.center.searching or self.center.grown or (getattr(self, 'share_sheet', None) and self.share_sheet.get_mapped()):
            return
        direction = 1 if dy > 0 else -1
        if direction != self.scroll_direction:
            self.scroll_distance = 0
            self.scroll_direction = direction
        self.scroll_distance += abs(dy)
        if self.scroll_distance < 32:
            return
        self.bar_hidden = direction > 0
        self.center.bar.set_visible(not self.bar_hidden)
        self.scroll_distance = 0

    def focus_address(self):
        self.reveal_bar()
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
        rows = [PanelRow('Share page', icon='share-2', sensitive=state.get('activeUrl', '').startswith(('https://', 'http://')), on_activate=self.show_share),
                PanelRow('Downloads', icon='download', on_activate=self.show_downloads),
                PanelRow('Forward', icon='chevron-right', sensitive=bool(state.get('canGoForward')), on_activate=lambda: self.command('nav:forward')),
                PanelRow('Reload', icon='rotate-cw', on_activate=lambda: self.command('nav:reload')),
                PanelRow('Unpin from top' if any(tab['id'] == state.get('activeTabId') for tab in state.get('favorites', [])) else 'Pin to top', icon='pin', on_activate=self.toggle_pin),
                PanelRow('New private window', icon='eye-off', on_activate=self.host._new_private_window),
                PanelRow('Browser menu', icon='ellipsis', on_activate=self.browser_menu)]
        self.center.grow('more', panel_list(rows, label='More'))
        self.panel = 'more'

    def command(self, channel):
        self.center.fold(); self.panel = None
        self.host.send(channel)

    def browser_menu(self):
        anchor = self.host.menu_button
        self.host.submit(lambda: self.host.services.open_menu('browser:menu',
            {'anchorRect': {'x': 0, 'y': 0, 'width': 48, 'height': 48}}),
            lambda model: self.host.show_menu(anchor, model, position=Gtk.PositionType.TOP))
