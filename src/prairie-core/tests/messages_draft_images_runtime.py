#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real GTK composer and draft storage; isolated fixture transport, no network."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src/luma-platform/appkit'), str(ROOT / 'src/prairie-core')]
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk
from luma_appkit import install_appkit, install_lumaui
from prairie_apps.messages import install_messages_theme
from prairie_apps.messages_app_port import LumaUIMessagesWindow
from prairie_apps.messages_backend import MessageStore
from prairie_apps.messages_mms import MmsCapability
from prairie_apps.messages_preferences import ConversationPreferences


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def wait(predicate):
    deadline = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < deadline, 'Draft update timed out'
        pump()
        time.sleep(.01)
    pump()


app = Adw.Application(application_id='org.projectluma.Messages.DraftImagesTest',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
if (ROOT / 'src/prairie-core/style/messages.css').is_file():
    os.environ.setdefault('LUMA_MESSAGES_STYLE_PATH', str(ROOT / 'src/prairie-core/style/messages.css'))
install_appkit(); install_lumaui(); install_messages_theme()
with tempfile.TemporaryDirectory(prefix='message-drafts-') as temporary:
    directory = Path(temporary)
    for width in (1180, 420):
        window = LumaUIMessagesWindow(app)
        window.set_default_size(width, 740)
        window.present(); pump()
        surface = window.surface
        fixture_store = window.service.store
        store = MessageStore(directory / f'messages-{width}.db')
        window.service.store = store
        window.service.mms_capability = MmsCapability(True, '', max_bytes=25_000_000, max_attachments=10)
        address = '+12025550123'
        store.set_display_name(address, 'Draft recipient')
        store.add(address, 'Existing conversation', direction='incoming')
        surface.fixture = None
        surface.preferences = ConversationPreferences(directory / f"preferences-{width}.json")
        window._open_thread(store.threads()[0], reveal=True, service=window.service)
        intro = surface.history.get_first_child()
        avatar = intro.get_first_child()
        wait(lambda: avatar.get_mapped() and avatar.get_height() >= 88)
        wait(lambda: not surface._end_pending)
        surface.scroller.get_vadjustment().set_value(0)
        # Render through the scroll viewport: ancestor masks must keep the full face visible.
        pump(); time.sleep(.15); pump()
        ok, bounds = avatar.compute_bounds(surface.scroller)
        assert ok and bounds.get_y() >= 0, (width, ok, bounds.get_y(), window.get_height(), surface.body.get_height(), surface.scroller.get_vadjustment().get_value(), surface.scroller.get_vadjustment().get_upper(), surface.scroller.get_vadjustment().get_page_size())
        viewport_width, viewport_height = surface.scroller.get_width(), surface.scroller.get_height()
        paintable = Gtk.WidgetPaintable.new(surface.scroller)
        def snapshot_node():
            snapshot = Gtk.Snapshot()
            paintable.snapshot(snapshot, viewport_width, viewport_height)
            return snapshot.to_node()
        # A newly attached paintable receives its contents on a frame; mapping
        # the widget does not mean that frame has already been delivered.
        wait(lambda: snapshot_node() is not None)
        rendered = window.get_renderer().render_texture(snapshot_node(), Graphene.Rect().init(0, 0, viewport_width, viewport_height))
        download = Gdk.TextureDownloader.new(rendered)
        download.set_format(Gdk.MemoryFormat.R8G8B8A8)
        pixels, stride = download.download_bytes()
        x = round(bounds.get_x() + bounds.get_width() / 2)
        top = round(bounds.get_y() + 10)
        bottom = round(bounds.get_y() + bounds.get_height() - 10)
        data = pixels.get_data()
        top_alpha, bottom_alpha = (data[y * stride + x * 4 + 3] for y in (top, bottom))
        assert top_alpha > 0 and top_alpha >= bottom_alpha * .85, (top_alpha, bottom_alpha)
        surface.bar_entry.set_text('Caption remains')
        image = directory / 'selected.png'
        texture = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8,
                                       GLib.Bytes.new(bytes([180, 80, 40, 255]) * 4), 8)
        assert texture.save_to_png(str(image))
        count = len(store.thread(address))
        window._stage_attachment(image)
        wait(lambda: len(store.draft_attachments(address)) == 1 and surface.center.head_row.get_visible())
        assert surface.bar_entry.text == 'Caption remains'
        assert len(store.thread(address)) == count, 'Picking sent the attachment'
        draft = store.draft_attachments(address)[0]
        window._remove_attachment(draft.uid)
        assert not store.draft_attachments(address)
        assert not surface.center.head_row.get_visible()
        # Dispatch the real TextView paste action; text paste must retain GTK's behavior.
        surface.bar_entry.widget.view.get_clipboard().set(texture)
        surface.bar_entry.widget.view.emit('paste-clipboard')
        wait(lambda: len(store.draft_attachments(address)) == 1 and surface.center.head_row.get_visible())
        assert len(store.thread(address)) == count, 'Pasting sent the attachment'
        surface.bar_entry.set_text('')
        surface._send()
        wait(lambda: len(store.thread(address)) == count + 1)
        sent = next(message for message in store.thread(address) if message.direction == 'outgoing')
        assert sent.body == '' and len(sent.attachments) == 1, (sent.body, sent.attachments)
        assert not store.draft_attachments(address)
        surface.bar_entry.widget.view.get_clipboard().set('Pasted text')
        surface.bar_entry.widget.view.emit('paste-clipboard')
        wait(lambda: surface.bar_entry.text == 'Pasted text')
        original_service = window._luma_service
        window._luma_service = lambda: SimpleNamespace(provider=SimpleNamespace(status={"state": "ready"}))
        window._luma_identity_asked = True
        window._luma_identity = {"handle": "drafttester"}
        surface.render_sidebar()
        assert surface.empty.secondary_button.get_label() == 'Your Luma username'
        assert surface._recipients('hivecoworking')[0]['handle'] == 'hivecoworking'
        assert surface._recipients('@hivecoworking')[0]['handle'] == 'hivecoworking'
        assert all(not result['handle'] for result in surface._recipients('12025550123'))
        window._luma_identity = {}
        surface.render_sidebar()
        assert surface.empty.secondary_button.get_label() == 'Choose a Luma username'
        window._luma_service = original_service
        window.close(); fixture_store.close(); pump()
print('Messages image drafts: picker staging, clipboard paste, removal, caption preservation, attachment-only Send and text paste passed at desktop/narrow widths')
