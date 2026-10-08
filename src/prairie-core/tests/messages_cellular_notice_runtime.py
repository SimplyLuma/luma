"""Real Messages GTK window on a computer without a modem; synthetic store only.

This device's text service is unavailable there. Opening a conversation must
not show a status line; writing a draft shows one calm line saying why it
cannot be sent, and clearing the draft hides it again. A device whose modem
is ready never shows it.
"""
import json, os, tempfile, time
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
from prairie_apps.messages import MessagesApplication, MessagesWindow
from prairie_apps.messages_backend import MessageStore, MessagingCapability, ModemMessagingTransport

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


class Modem:
    def __init__(self, capability): self.capability = capability
    def inspect(self): return self.capability
    def snapshot(self): return ()
    def send(self, _address, _body): raise AssertionError('nothing may be sent')


def no_mmcli(_arguments):
    raise FileNotFoundError('mmcli')


# The real adapter on a computer with no ModemManager answers calmly.
NO_MODEM = ModemMessagingTransport(runner=no_mmcli).inspect()
assert not NO_MODEM.available and 'Cellular messaging is unavailable' not in NO_MODEM.reason, NO_MODEM

out = Path(os.environ['LUMA_MESSAGES_CONNECT_UI_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
app = MessagesApplication(); assert app.register(None)
ADDRESS = '+12025550140'
results = []
for width in (920, 360):
    with tempfile.TemporaryDirectory(prefix='messages-cellular-notice-') as temporary:
        root = Path(temporary)
        os.environ['XDG_DATA_HOME'] = str(root / 'data'); os.environ['HOME'] = str(root)
        store = MessageStore(); store.add(ADDRESS, 'A conversation kept on this computer', direction='incoming', timestamp=100); store.close()
        os.environ['LUMA_PRESENTATION_MODE'] = 'fullscreen-mobile' if width == 360 else 'windowed'
        window = MessagesWindow(app)
        assert [service.label for service in window.services] == ['This device'], window.services
        window.native_service.transport = Modem(NO_MODEM)
        window.set_default_size(width, 760); window.present()
        window._refresh(); pump()
        window.open_address(ADDRESS); pump(.3)
        wait(lambda: window.current_address == ADDRESS, 'conversation open')
        assert not window.native_service.capability.available
        assert not window.transport_status.get_visible(), 'an open conversation with nothing to send shows no notice'

        window.composer_buffer.set_text('Running late'); pump(.2)
        assert window.transport_status.get_visible(), 'a draft that cannot go out says why'
        assert window.transport_status.get_label() == NO_MODEM.reason
        assert not window.send_button.get_sensitive()
        # Quiet secondary ink, not the destructive red the notice used to use.
        ink = window.transport_status.get_color()
        assert ink.red - max(ink.green, ink.blue) < 0.15, (ink.red, ink.green, ink.blue)
        paint = Gtk.WidgetPaintable.new(window); snap = Gtk.Snapshot(); paint.snapshot(snap, window.get_width(), window.get_height())
        assert window.get_renderer().render_texture(snap.to_node(), None).save_to_png(str(out / f'draft-{width}.png'))

        window.composer_buffer.set_text(''); pump(.2)
        assert not window.transport_status.get_visible(), 'clearing the draft hides the notice'

        window.native_service.transport = Modem(MessagingCapability(True, ''))
        window._refresh(); window.composer_buffer.set_text('Running late'); pump(.2)
        assert not window.transport_status.get_visible() and window.send_button.get_sensitive()
        window.close(); pump()
        results.append({'width': width, 'open_quiet': True, 'draft_explained': True, 'calm_ink': True})
print(json.dumps(results))
