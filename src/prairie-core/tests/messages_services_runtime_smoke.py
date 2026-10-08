"""Real shared Messages GTK window with two services (ADR-022); synthetic transports only.

This device's own messages and a phone reached through Luma Connect appear in
one list. Each conversation says which service it goes through, and sending in
it uses that service and no other.
"""
import json, os, tempfile, time
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
from prairie_apps.messages import ConversationRow, MessagesApplication, MessagesWindow
from prairie_apps.messages_backend import MessageStore, MessagingCapability
from luma_continuity.bootstrap import create_identity
from luma_continuity.message_provider import MessageProvider, SelectedPhone
from luma_continuity.policy import Journal

context = GLib.MainContext.default()


def pump(seconds=.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending(): context.iteration(False)
        time.sleep(.005)


def wait(predicate, what):
    end = time.monotonic() + 8
    while not predicate():
        if time.monotonic() > end: raise AssertionError('Messages GTK state timed out: ' + what)
        pump(.03)

def save_snapshot(window, path):
    # The window must be mapped and allocated, or the snapshot is empty.
    wait(lambda: window.get_mapped() and window.get_width() > 0 and window.get_height() > 0, 'window allocated'); pump(.3)
    paint = Gtk.WidgetPaintable.new(window); snap = Gtk.Snapshot(); paint.snapshot(snap, window.get_width(), window.get_height())
    node = snap.to_node(); assert node, 'empty snapshot'
    assert window.get_renderer().render_texture(node, None).save_to_png(str(path))



def rows(window):
    found, child = [], window.thread_list.get_first_child()
    while child is not None:
        if isinstance(child, ConversationRow): found.append(child)
        child = child.get_next_sibling()
    return found


def labels(widget):
    found, stack = [], [widget]
    while stack:
        item = stack.pop()
        if isinstance(item, Gtk.Label) and item.has_css_class('messages-row-service'): found.append(item.get_text())
        child = item.get_first_child()
        while child is not None: stack.append(child); child = child.get_next_sibling()
    return found


class FakeModem:
    """This device's modem: available, and it records what it is asked to send."""
    def __init__(self): self.sent = []
    def inspect(self): return MessagingCapability(True, '')
    def snapshot(self): return ()
    def send(self, address, body): self.sent.append((address, body))


out = Path(os.environ['LUMA_MESSAGES_CONNECT_UI_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
app = MessagesApplication(); assert app.register(None)
results = []
NATIVE, PHONE = '+12025550140', '+12025550123'
for width in (920, 360):
    with tempfile.TemporaryDirectory(prefix='messages-services-ui-') as temporary:
        root = Path(temporary)
        os.environ['XDG_DATA_HOME'] = str(root / 'data')
        native_store = MessageStore(); native_store.add(NATIVE, 'Native incoming conversation', direction='incoming', timestamp=100); native_store.close()
        directory = root / 'identity'; create_identity(directory)
        peer, epoch, account = 'a' * 64, 'b' * 32, 'synthetic-account'
        journal = Journal(directory / 'continuity.db'); journal.approve(peer, epoch, [], account=account, outgoing_grants=['messages.read', 'messages.send']); journal.close()
        records = [dict(uid='synthetic-incoming', address=PHONE, body='Phone incoming conversation', timestamp=123, direction='incoming', state='received', attachments=[])]
        sends = []

        def exchange(request):
            if request['capability'] == 'messages.send':
                sends.append(request['payload']['body'])
                row = dict(uid='synthetic-out-' + request['id'], address=request['payload']['address'], body=request['payload']['body'], timestamp=124, direction='outgoing', state='sent', attachments=[])
                records.append(row); return {'state': 'complete', 'result': {'uid': row['uid'], 'state': 'sent'}}
            result = {'threads': [dict(address=PHONE, display_name='Phone contact')], 'truncated': False} if 'query' in request['payload'] else {'messages': [r for r in records if r['address'] == request['payload']['address']]}
            return {'state': 'complete', 'result': result}

        provider = MessageProvider(directory, SelectedPhone(peer, epoch, account, 'Synthetic phone', '127.0.0.1', 18444), account_observation=lambda: {'account': account, 'signed_in': True, 'stale': False},
                                   store_factory=MessageStore, dispatch=GLib.idle_add, exchange_factory=lambda *_a, **_k: exchange)
        app.message_provider = provider
        os.environ['LUMA_PRESENTATION_MODE'] = 'fullscreen-mobile' if width == 360 else 'windowed'
        window = MessagesWindow(app)
        modem = FakeModem(); window.native_service.transport = modem
        window.set_default_size(width, 760); window.present()
        assert [service.label for service in window.services] == ['This device', 'Synthetic phone'], window.services
        assert window.service is window.services[1], 'a phone service is where the window opens'
        wait(lambda: provider.status['state'] == 'ready' and len(rows(window)) == 2, 'both services listed')
        by_address = {row.record.address: row for row in rows(window)}
        # Rows no longer print their service; each row still carries it, and the open conversation's header names it.
        assert labels(by_address[NATIVE]) == [] and labels(by_address[PHONE]) == [], 'no service callout on rows'
        assert by_address[NATIVE].service is window.native_service and by_address[PHONE].service is window.services[1]

        # This device's conversation sends through this device's modem only.
        window._open_thread(by_address[NATIVE].record, reveal=True, service=by_address[NATIVE].service); pump()
        assert window.store is window.native_service.store and window.current_address == NATIVE
        wait(lambda: window.native_service.capability.available, 'native capability')
        window.composer_buffer.set_text('Reply on this device')
        wait(lambda: window.send_button.get_sensitive(), 'native composer ready')
        window._send_message()
        wait(lambda: modem.sent == [(NATIVE, 'Reply on this device')], 'native send')
        assert sends == [], 'the phone was not used for this device\'s conversation'

        # The phone's conversation sends through the phone only.
        window.open_address(f'phone:{peer}|{PHONE}'); pump()
        assert window.service is window.services[1] and window.current_address == PHONE
        window.composer_buffer.set_text('Reply through the phone')
        wait(lambda: window.send_button.get_sensitive(), 'phone composer ready')
        window._send_message()
        wait(lambda: sends == ['Reply through the phone'] and window.store.thread(PHONE)[-1].state == 'sent', 'phone send')
        assert modem.sent == [(NATIVE, 'Reply on this device')], 'this device\'s modem was not used for the phone\'s conversation'
        assert window.thread_subtitle.get_text().endswith('· Synthetic phone'), window.thread_subtitle.get_text()

        # A new conversation offers the choice and uses what was picked.
        window._show_new_message(); pump()
        assert window.send_service_picker.get_parent().get_visible()
        window.send_service_picker.set_selected(0)
        window.recipient_search.set_text('+12025550177'); pump(.2)
        window._recipient_activate_entry(); pump()
        assert window.service is window.native_service and window.current_address == '+12025550177'
        assert window.thread_subtitle.get_text() == 'New conversation · This device', window.thread_subtitle.get_text()
        window._back_to_inbox(); pump(.8)

        save_snapshot(window, out / ('messages-services-' + str(width) + '.png'))
        results.append({'width': width, 'services': 2, 'native_send': 1, 'phone_send': 1, 'new_conversation_service_choice': True})
        window.close(); pump(.3)
        assert window.get_visible() is False and not provider._closed, 'closing keeps accounts connected in the background'
        window.quit(); pump(.3); assert provider._closed
(out / 'result.json').write_text(json.dumps({'passed': True, 'real_shared_GTK': True, 'synthetic_transport_only': True, 'cases': results}, indent=2))
print('PASS real shared Messages GTK with two services; synthetic transports only')
