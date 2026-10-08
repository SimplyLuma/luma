"""Real shared Messages GTK window: add, use and remove a network account (ADR-023) with a fake helper."""
import json, os, sys, tempfile, time
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
from prairie_apps.messages import ConversationRow, MessagesApplication, MessagesWindow
from prairie_apps.messages_accounts import Accounts, network

sys.path.insert(0, str(Path(__file__).resolve().parent))
from messages_accounts_unit import FakeSecrets  # noqa: E402

FAKE = Path(__file__).resolve().parent / 'fake_messages_bridge.py'
context = GLib.MainContext.default()


def pump(seconds=.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending(): context.iteration(False)
        time.sleep(.005)


def wait(predicate, what, timeout=12):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end: raise AssertionError('Messages GTK state timed out: ' + what)
        pump(.03)

def save_snapshot(window, path):
    # The window must be mapped and allocated, or the snapshot is empty.
    wait(lambda: window.get_mapped() and window.get_width() > 0 and window.get_height() > 0, 'window allocated'); pump(.3)
    paint = Gtk.WidgetPaintable.new(window); snap = Gtk.Snapshot(); paint.snapshot(snap, window.get_width(), window.get_height())
    node = snap.to_node(); assert node, 'empty snapshot'
    assert window.get_renderer().render_texture(node, None).save_to_png(str(path))



def descendants(widget):
    stack = [widget]
    while stack:
        item = stack.pop(); yield item
        child = item.get_first_child()
        while child is not None: stack.append(child); child = child.get_next_sibling()


def texts(widget):
    return [w.get_text() for w in descendants(widget) if isinstance(w, Gtk.Label)]


def login_step(dialog):
    page = dialog.login_page
    if page is None: return ''
    return page.get_child().get_content().get_child().get_child().get_child().get_name()


out = Path(os.environ['LUMA_MESSAGES_CONNECT_UI_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
results = []
app = MessagesApplication(); assert app.register(None)
for width in (920, 360):
    with tempfile.TemporaryDirectory(prefix='messages-accounts-ui-') as temporary:
        root = Path(temporary)
        os.environ['XDG_DATA_HOME'] = str(root / 'data')
        helpers = root / 'libexec'; helpers.mkdir()
        (helpers / 'whatsapp').write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n'); (helpers / 'whatsapp').chmod(0o755)
        app.accounts, app.helper_directory, app.account_secrets = Accounts(root / 'accounts'), helpers, FakeSecrets()
        os.environ['LUMA_PRESENTATION_MODE'] = 'fullscreen-mobile' if width == 360 else 'windowed'
        window = MessagesWindow(app); window.set_default_size(width, 760); window.present(); pump(.3)
        assert [s.label for s in window.services] == ['This device'], window.services

        window._show_accounts(); pump(.3)
        dialog = window.accounts_dialog
        assert 'No accounts yet' in texts(dialog), texts(dialog)
        dialog._show_networks(); pump(.2)
        dialog._show_network(network('whatsapp')); pump(.2)
        assert any('WhatsApp doesn' in text for text in texts(dialog)), 'the unofficial-client warning is shown before linking'
        dialog._start_login(network('whatsapp'), 'qr')
        wait(lambda: login_step(dialog) == 'login-login.qr', 'QR code shown')
        assert any(isinstance(w, Gtk.DrawingArea) for w in descendants(dialog.login_page)), 'the QR code is drawn'
        save_snapshot(window, out / f'accounts-qr-{width}.png')
        dialog.login.submit('confirm', 'scanned')
        wait(lambda: login_step(dialog) == 'login-login.done', 'sign-in finished')
        assert [s.label for s in window.services] == ['This device', 'WhatsApp'], [s.label for s in window.services]
        service = window.services[1]
        assert app.account_secrets.items == {service.account.id: 'fake-session-token'}

        wait(lambda: service.provider.status['state'] == 'ready', 'account connected')
        wait(lambda: {row.record.display_name for row in [r for r in descendants(window.thread_list) if isinstance(r, ConversationRow)]} >= {'Fake Friend', 'Fake Group'}, 'account chats listed')
        dialog.close(); pump(.3)

        group = next(r for r in descendants(window.thread_list) if isinstance(r, ConversationRow) and r.record.display_name == 'Fake Group')
        window._open_thread(group.record, reveal=True, service=group.service); pump(.2)
        assert 'Other Person' in texts(window.message_box), 'a group message says who wrote it'
        assert not window.call_button.get_sensitive(), 'a network conversation id is never dialled'
        assert window.thread_subtitle.get_text() == 'WhatsApp', window.thread_subtitle.get_text()

        window.open_address(f'{service.id}|chat.one'); pump(.2)
        window.composer_buffer.set_text('Hello through the account')
        wait(lambda: window.send_button.get_sensitive(), 'composer ready')
        window._send_message()
        sent = Path(window.accounts.directory(service.account.id)) / 'sent.jsonl'
        wait(lambda: sent.exists() and window.store.thread('chat.one')[-1].state == 'sent', 'sent through the helper')
        assert json.loads(sent.read_text())['text'] == 'Hello through the account'
        pump(.5)
        save_snapshot(window, out / f'accounts-chat-{width}.png')

        # A picture that didn't download says so and offers Try Again instead of vanishing; a photo
        # this system can't decode (HEIC without a HEVC decoder) says that rather than showing nothing.
        service.provider._process.request('fake.incoming_media', {'id': f'pic{width}', 'parts': [
            {'part': '0', 'state': 'failed', 'error': 'expired', 'retryable': True},
            {'part': '1', 'content': '\x00\x00\x00\x18ftypheic' + '\x00' * 64, 'mime': 'image/heic', 'name': 'IMG_0001.heic'}]})
        wait(lambda: "The photo didn't download." in texts(window.message_box), 'the missing picture is shown as missing')
        assert any("can't show HEIC photos" in text for text in texts(window.message_box)), texts(window.message_box)
        retry = next(w for w in descendants(window.message_box) if isinstance(w, Gtk.Button) and w.get_label() == 'Try Again')
        pump(.3)
        save_snapshot(window, out / f'accounts-media-{width}.png')
        retry.emit('clicked')
        wait(lambda: "The photo didn't download." not in texts(window.message_box), 'Try Again brought the picture in')
        assert len(window.store.thread('chat.one')[-1].attachments) == 2

        # A message arriving in another conversation is announced as a desktop notification that opens it.
        sent_notifications = []
        original_send = app.send_notification
        app.send_notification = lambda ident, notification: sent_notifications.append((ident, notification))
        service.provider._process.request('fake.incoming', {'id': f'n{width}', 'conversation': 'group.one', 'text': 'Notify me'})
        wait(lambda: sent_notifications, 'a notification for the new message')
        ident, notification = sent_notifications[0]
        assert ident == f'message:{service.id}|group.one', ident
        app.send_notification = original_send

        account_id = service.account.id
        window.remove_account_service(service); pump(.3)
        assert [s.label for s in window.services] == ['This device']
        assert json.loads((root / 'accounts' / 'logged-out.json').read_text())['account'] == account_id
        assert not (root / 'accounts' / account_id).exists() and app.account_secrets.items == {}
        results.append({'width': width, 'add_by_qr': True, 'group_sender': True, 'send': True, 'media_retry': True, 'remove_wipes': True})
        window.close(); pump(.3)
(out / 'result.json').write_text(json.dumps({'passed': True, 'real_shared_GTK': True, 'fake_helper_only': True, 'cases': results}, indent=2))
print('PASS real shared Messages GTK accounts: add by QR, group senders, send, missing pictures and Try Again, remove; fake helper only')
