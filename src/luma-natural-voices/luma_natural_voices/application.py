# SPDX-License-Identifier: Apache-2.0
"""Optional managed voice: actual SSIP discovery and native sample playback."""
from concurrent.futures import ThreadPoolExecutor
import gi
gi.require_version('Adw', '1'); gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib
from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry, EmptyState
import speechd

APP_ID = 'org.projectluma.NaturalVoices'
VOICE_NAME = 'en_US-ljspeech-medium'

class VoiceWindow(AppWindow):
    def __init__(self, app):
        commands = CommandRegistry((CommandGroup('', (
            Command('voices.refresh', 'Refresh voices', self.refresh, 'lumaui-refresh-cw-symbolic'),
            Command('voices.quit', 'Quit Natural Voices', app.quit, 'lumaui-log-out-symbolic', shortcut=('Ctrl','Q')),
        )),))
        super().__init__(application=app, app_id=APP_ID, title='Natural Voices',
                         icon_name='org.projectluma.Leaf', commands=commands,
                         default_width=640, default_height=500, minimum_width=320)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='voice-discovery')
        self.client = None; self.closed = False; self.busy = False; self.playing = False; self.finished_pending = None; self.stopping = False; self.speech_generation = 0
        self.connect('close-request', self._closed)
        self._show_state('Finding your voices', 'Looking for the voices registered on this computer.')
        self.refresh()

    def _show_state(self, title, description, primary=None, secondary=None):
        self.set_body(EmptyState(title, description, 'lumaui-volume-2-symbolic',
                                 primary=primary, secondary=secondary))

    def _dispatch(self, operation, complete):
        if self.busy or self.closed: return
        self.busy=True
        future = self.pool.submit(operation)
        def done(f):
            try: result=f.result(); error=None
            except Exception as e: result=None; error=str(e)
            def deliver():
                self.busy=False
                if not self.closed:
                    complete(result,error)
                    if self.finished_pending is not None:
                        generation=self.finished_pending; self.finished_pending=None; self._finished(generation)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
        future.add_done_callback(done)

    def refresh(self):
        if self.busy or self.closed: return
        self.speech_generation += 1
        self.playing=False; self.stopping=False
        def discover():
            if self.client: self.client.cancel(); self.client.close(); self.client=None
            self.client=speechd.SSIPClient('luma-natural-voices')
            if 'piper' not in self.client.list_output_modules(): return False
            self.client.set_output_module('piper')
            return any(name==VOICE_NAME for name,language,variant in self.client.list_synthesis_voices())
        def found(available,error):
            if available:
                self._show_state('Natural English voice',
                    'LJSpeech reads on this computer. Your text stays here. Choose it in Leaf’s voice picker.',
                    ('Play a sample', self.play), ('Refresh voices', self.refresh))
            else:
                self._show_state('Reload your voices',
                    'The voice is installed, but your speech service has not discovered it yet.' if not error
                    else 'The speech service could not be reached. Try reloading your voices.',
                    ('Reload voices', self.reload), ('Refresh voices', self.refresh))
        self._dispatch(discover,found)

    def reload(self):
        def restart():
            if self.client: self.client.cancel(); self.client.close(); self.client=None
            proxy=Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE,
                None, 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
                'org.freedesktop.systemd1.Manager', None)
            proxy.call_sync('RestartUnit', GLib.Variant('(ss)', ('speech-dispatcher.service','replace')),
                            Gio.DBusCallFlags.NONE, 10000, None)
        def restarted(_result,error):
            if error:
                self._show_state('Your voices need a new session',
                    'Sign out and back in so the speech service can discover the installed voice.',
                    ('Refresh voices', self.refresh))
            else: self.refresh()
        self._dispatch(restart,restarted)

    def play(self):
        if self.playing or not self.client: return
        self.playing=True; self.stopping=False
        self.speech_generation += 1
        generation=self.speech_generation
        self._show_state('Playing the natural voice', 'Welcome to Luma. Your books can sound this natural.',
                    ('Stop', self.stop))
        def speak():
            self.client.set_output_module('piper'); self.client.set_synthesis_voice(VOICE_NAME)
            self.client.set_data_mode(speechd.DataMode.TEXT)
            def event(kind, **_kwargs):
                if kind in (speechd.CallbackType.END, speechd.CallbackType.CANCEL):
                    GLib.idle_add(self._finished,generation)
            self.client.speak('Welcome to Luma. Your books can sound this natural.', callback=event,
                              event_types=(speechd.CallbackType.END,speechd.CallbackType.CANCEL))
        def started(_result,error):
            if error:
                self.playing=False
                self._show_state('The sample could not play',
                    'Check your sound output and refresh the voices before trying again.',
                    ('Refresh voices', self.refresh))
        self._dispatch(speak,started)

    def _finished(self, generation):
        if generation != self.speech_generation: return GLib.SOURCE_REMOVE
        self.playing=False; self.stopping=False
        if not self.closed:
            if self.busy: self.finished_pending=generation
            else: self.refresh()
        return GLib.SOURCE_REMOVE

    def stop(self):
        if not self.playing or self.stopping or not self.client or self.closed: return
        self.stopping=True
        generation=self.speech_generation
        client=self.client
        self._show_state('Stopping the sample', 'Finishing the current audio playback.')
        # CANCEL acknowledges the command, while the speech event acknowledges
        # actual playback termination. Keep the playing state until that event;
        # otherwise a new sample can be queued behind audio that is still active.
        future=self.pool.submit(client.cancel)
        def done(f):
            try: f.result(); error=None
            except Exception as e: error=str(e)
            def deliver():
                if error and not self.closed and generation == self.speech_generation:
                    self.playing=False; self.stopping=False
                    self._show_state('The sample could not stop',
                        'Refresh the voices to reconnect to the speech service.',
                        ('Refresh voices', self.refresh))
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
        future.add_done_callback(done)

    def _closed(self,*_args):
        if self.closed: return False
        self.closed=True
        def cleanup():
            if self.client:
                try: self.client.cancel()
                finally: self.client.close(); self.client=None
        # Queue after any in-flight discovery/sample operation, including a
        # client that has not yet been assigned when the window is closed.
        self.pool.submit(cleanup)
        self.pool.shutdown(wait=False)
        return False

class VoiceApplication(Adw.Application):
    def __init__(self): super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    def do_activate(self):
        window=self.get_active_window() or VoiceWindow(self);window.present()
    def do_shutdown(self):
        for window in self.get_windows():
            if isinstance(window, VoiceWindow): window._closed()
        Adw.Application.do_shutdown(self)

def main(argv=None): return VoiceApplication().run(argv)
