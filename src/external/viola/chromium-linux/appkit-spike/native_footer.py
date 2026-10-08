# SPDX-License-Identifier: GPL-3.0-only
"""Native notices and mini-player over existing notification/media services."""
import base64
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, GObject, Gtk, Pango


class NativeFooter(Gtk.Box):
    compact_media = GObject.Property(type=bool, default=False)
    compact_notifications = GObject.Property(type=bool, default=False)

    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.host = host
        self.state = {}
        self.cards = {}
        self.media_identity = None
        self.pending_live_preview = None
        self.has_preview = False
        self.sidebar_height = 800
        self.add_css_class('viola-footer')
        self.notices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class('viola-notice-header')
        heading = Gtk.Label(label='NOTIFICATIONS', xalign=0)
        heading.add_css_class('viola-notice-heading')
        header.append(heading)
        self.count = Gtk.Label()
        self.count.add_css_class('viola-notice-count')
        header.append(self.count)
        spacer = Gtk.Box(hexpand=True)
        header.append(spacer)
        clear = Gtk.Button(label='Clear')
        clear.add_css_class('viola-notice-clear')
        clear.connect('clicked', lambda *_: host.send('notification:clear', {'includeKept': True}))
        header.append(clear)
        self.notices.append(header)
        self.notice_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.notices.append(self.notice_list)
        self.append(self.notices)
        self.player = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.player.add_css_class('viola-player')
        self.player.set_overflow(Gtk.Overflow.HIDDEN)
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
        self.art_frame = Gtk.AspectFrame(ratio=16/9, obey_child=False, child=self.picture)
        activate = Gtk.GestureClick(button=1)
        activate.connect('released', lambda *_: self.open_media_tab())
        self.picture.add_controller(activate)
        context_menu = Gtk.GestureClick(button=3)
        context_menu.connect('pressed', self.open_media_menu)
        self.player.add_controller(context_menu)
        self.player.append(self.art_frame)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class('viola-player-row')
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class('viola-player-title')
        self.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.artist.add_css_class('viola-player-artist')
        labels.append(self.title)
        labels.append(self.artist)
        row.append(labels)
        self.previous = host.button('media-skip-backward-symbolic', 'Previous', lambda: host.send('media:prev'))
        self.play = host.button('media-playback-start-symbolic', 'Play or pause', lambda: host.send('media:playpause'))
        self.play.add_css_class('viola-player-play')
        self.next = host.button('media-skip-forward-symbolic', 'Next', lambda: host.send('media:next'))
        for button in (self.previous, self.play, self.next):
            row.append(button)
        self.player.append(row)
        self.append(self.player)
        self.notices.set_visible(False)
        self.player.set_visible(False)
        self.connect('notify::compact-media', lambda *_: self.update_visibility())
        self.connect('notify::compact-notifications', lambda *_: self.update_visibility())

    def open_media_menu(self, gesture, _presses, x, y):
        media = self.state.get('media') or {}
        tab_id = media.get('tabId')
        if not tab_id:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        def activate(_nonce, path):
            current = (self.state.get('media') or {}).get('tabId')
            if path == [0] and current == tab_id:
                def pop_out():
                    services = self.host.services
                    if services and ((services.state or {}).get('media') or {}).get('tabId') == tab_id:
                        services.send('sidebar', 'media:pip')
                self.host.submit(pop_out)
        self.host.show_menu(self.player, {'nonce': 'mini-player', 'items': [
            dict(index=0, kind='item', label='Picture in Picture',
                 visible=True, enabled=True)]}, x, y, local_activate=activate)

    def open_media_tab(self):
        media = self.state.get('media') or {}
        if media.get('tabId'):
            self.host.send('tab:activate', {'tabId': media['tabId']})

    def card(self, item):
        key = item['id']
        card = self.cards.get(key)
        if card is None:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class('viola-notice-card')
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
            card.icon = Gtk.Image(pixel_size=14)
            card.icon.add_css_class('viola-notice-icon')
            row.append(card.icon)
            card.open = Gtk.Button(hexpand=True)
            card.open.add_css_class('viola-notice-open')
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            card.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            card.title.add_css_class('viola-notice-title')
            card.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            card.detail.add_css_class('viola-notice-detail')
            labels.append(card.title)
            labels.append(card.detail)
            card.open.set_child(labels)
            card.open.connect('clicked', lambda *_: self.open_notice(card.item))
            row.append(card.open)
            dismiss = self.host.button('window-close-symbolic', 'Dismiss notification',
                                        lambda: self.dismiss_notice(card.item))
            row.append(dismiss)
            card.append(row)
            card.download_status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            card.download_status.add_css_class('viola-download-status')
            card.append(card.download_status)
            card.progress = Gtk.ProgressBar(show_text=False, hexpand=True)
            card.progress.add_css_class('viola-download-progress')
            card.append(card.progress)
            card.last_download_received = None
            card.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            card.block = Gtk.Button(label='Block')
            card.allow = Gtk.Button(label='Allow')
            card.block.connect('clicked', lambda *_: self.host.send('permission:respond', {'id': key, 'decision': 'block'}))
            card.allow.connect('clicked', lambda *_: self.host.send('permission:respond', {'id': key, 'decision': 'allow'}))
            card.actions.append(card.block)
            card.actions.append(card.allow)
            card.append(card.actions)
            from native_download_actions import DownloadActions
            card.download_actions = DownloadActions(self, card)
            self.cards[key] = card
        card.item = item
        title = item.get('title') or item.get('host') or 'Notification'
        detail = item.get('body') or ''
        download = next((row for row in self.state.get('downloads', [])
                         if row.get('id') == item.get('downloadId')), None)
        if download:
            title = {'done': 'Download complete', 'paused': 'Download paused',
                     'progressing': 'Downloading', 'cancelled': 'Download cancelled',
                     'interrupted': 'Download interrupted', 'failed': 'Download failed'}.get(download.get('state'), title)
            detail = download.get('filename') or item.get('title') or detail
        active_download = bool(download and download.get('state') in ('progressing', 'paused'))
        card.download_status.set_visible(active_download)
        card.progress.set_visible(active_download)
        if active_download:
            received = max(0, download.get('received') or 0)
            total = max(0, download.get('total') or 0)
            paused = download.get('state') == 'paused'
            status = item.get('body') or title
            if total:
                fraction = min(1, received / total)
                card.progress.set_fraction(fraction)
                if paused:
                    status = str(round(fraction * 100)) + '% · ' + status
            elif paused:
                card.progress.set_fraction(0)
            elif received != card.last_download_received:
                card.progress.pulse()
            card.last_download_received = received
            card.download_status.set_label(status)
            card.progress.set_text(status)
            if paused:
                card.progress.add_css_class('paused')
            else:
                card.progress.remove_css_class('paused')
        card.title.set_label(title)
        card.detail.set_label(detail)
        card.open.set_tooltip_text(detail + '\n' + (item.get('body') or ''))
        card.icon.set_from_icon_name('folder-download-symbolic' if item.get('url') == 'vela://downloads'
                                     else 'dialog-information-symbolic')
        action = item.get('type') in ('permission', 'session-restore')
        card.actions.set_visible(action)
        card.block.set_label('Not now' if item.get('type') == 'session-restore' else 'Block')
        card.allow.set_label('Restore' if item.get('type') == 'session-restore' else 'Allow')
        return card

    def open_notice(self, item):
        if item.get('type') not in ('permission', 'session-restore'):
            self.host.send('notification:open', {'id': item['id']})

    def dismiss_notice(self, item):
        if item.get('type') in ('permission', 'session-restore'):
            self.host.send('permission:respond', {'id': item['id'], 'decision': 'dismiss'})
        else:
            self.host.send('notification:dismiss', {'id': item['id']})

    def render(self, state):
        self.state = state
        items = ([state['permissionPrompt']] if state.get('permissionPrompt') else []) + state.get('notifications', [])
        self.count.set_label(str(len(items)))
        self.notices.set_visible(bool(items))
        desired = [self.card(item) for item in items]
        previous = None
        for card in desired:
            if card.get_parent() is None:
                self.notice_list.insert_child_after(card, previous)
            else:
                self.notice_list.reorder_child_after(card, previous)
            previous = card
        keys = {item['id'] for item in items}
        for key in list(self.cards):
            if key not in keys:
                self.notice_list.remove(self.cards.pop(key))
        media = state.get('media')
        show_media = bool(media and media.get('tabId') != state.get('activeTabId'))
        self.player.set_visible(show_media)
        self.set_visible(bool(items or show_media))
        if media:
            identity = media.get('tabId')
            if identity != self.media_identity:
                self.media_identity = identity
                self.has_preview = False
                self.picture.set_paintable(None)
            if self.pending_live_preview and self.pending_live_preview[0] == identity:
                self.apply_live_preview(self.pending_live_preview[1])
            self.title.set_label(media.get('title') or '')
            self.artist.set_label(media.get('artist') or '')
            self.play.get_child().set_from_icon_name('media-playback-pause-symbolic'
                                                    if media.get('playing') else 'media-playback-start-symbolic')
        self.update_visibility()

    def live_preview(self, paintable, tab_id):
        # Media state and decoded video arrive on independent channels.
        # Retain one target-bound paintable if the first frame wins that race.
        self.pending_live_preview = (tab_id, paintable)
        if tab_id == self.media_identity:
            self.apply_live_preview(paintable)

    def apply_live_preview(self, paintable):
        self.picture.set_paintable(paintable)
        self.has_preview = paintable is not None
        if paintable and paintable.get_intrinsic_height() > 0:
            self.art_frame.set_ratio(paintable.get_intrinsic_width() / paintable.get_intrinsic_height())
        self.update_visibility()

    def preview(self, payload):
        source = payload.get('url') if isinstance(payload, dict) else None
        if not self.state.get('media') or not isinstance(source, str):
            return
        if not source.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')) or len(source) > 4*1024*1024:
            return
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(base64.b64decode(source.split(',', 1)[1], validate=True)))
            self.picture.set_paintable(texture)
            self.art_frame.set_ratio(texture.get_width() / texture.get_height())
            self.has_preview = True
            self.update_visibility()
        except Exception:
            pass

    def resize(self, height):
        self.sidebar_height = height
        self.props.compact_media = height < 700
        self.props.compact_notifications = height < 560

    def update_visibility(self):
        self.art_frame.set_visible(self.has_preview and not self.props.compact_media)
        active_download = any(row.get('state') in ('progressing', 'paused')
                              for row in self.state.get('downloads', []))
        self.notice_list.set_visible(not self.props.compact_notifications or active_download)
