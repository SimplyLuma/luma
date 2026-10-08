#!/usr/bin/env python3
"""Shared Phone call-provider integration against exact qualified Core3 source."""
import argparse
import difflib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
path=a.source/'prairie_apps/phone.py';original=path.read_text();s=original
changes=[
('    def do_activate(self) -> None:\n','    def do_activate(self) -> None:\n        if getattr(getattr(self, "call_provider", None), "closed", False):\n            from luma_continuity.call_provider import selected_provider\n            self.call_provider = selected_provider(dispatch=GLib.idle_add)\n'),
('        install_phone_theme()','        install_phone_theme()\n        if not hasattr(self, "call_provider"):\n            import importlib.util\n            self.call_provider = None\n            if importlib.util.find_spec("luma_continuity") is not None:\n                from luma_continuity.call_provider import selected_provider\n                self.call_provider = selected_provider(dispatch=GLib.idle_add)'),
('    NativeCall,\n','    NativeCall,\n    PhoneCapability,\n'),
('        self.store = CallStore()','        self.continuity = getattr(application, "call_provider", None)\n        self._paired_control_pending = False\n        self._paired_ringer = None\n        self.store = CallStore()'),
('        self.capability = inspect_phone_capability()','        self.capability = PhoneCapability(False, "Connecting to phone…") if self.continuity else inspect_phone_capability()'),
('''        self.monitor = IncomingCallMonitor(
            on_state=self._native_state_changed,
            on_ended=self._native_call_deleted,
        )
        try:
            self.monitor.start()
        except GLib.Error:
            pass
''','''        self.monitor = None
        if self.continuity:
            from .phone_feedback import Ringer
            self._paired_ringer = Ringer(APPLICATION_ID)
            self.continuity.start(self._paired_calls_changed)
        else:
            self.monitor = IncomingCallMonitor(on_state=self._native_state_changed, on_ended=self._native_call_deleted)
            try: self.monitor.start()
            except GLib.Error: pass
'''),
('    def _accept_call(self, _button: Gtk.Button) -> None:\n','    def _accept_call(self, _button: Gtk.Button) -> None:\n        if self.continuity: self._paired_control("answer"); return\n'),
('    def _decline_call(self, _button: Gtk.Button) -> None:\n','    def _decline_call(self, _button: Gtk.Button) -> None:\n        if self.continuity: self._paired_control("decline"); return\n'),
('    def _end_call(self, _button: Gtk.Button) -> None:\n','    def _end_call(self, _button: Gtk.Button) -> None:\n        if self.continuity: self._paired_control("hangup"); return\n'),
('''    @staticmethod
    def _dial_native(number: str):
        transport = preferred_voice_transport()''','''    def _dial_native(self, number: str):
        transport = self.continuity or preferred_voice_transport()'''),
('        self.session = self.session.with_call_id(call_id)','        if self.continuity and not call_id: return GLib.SOURCE_REMOVE\n        self.session = self.session.with_call_id(call_id)'),
('    def _restore_native_call(self) -> bool:\n','    def _restore_native_call(self) -> bool:\n        if self.continuity: return GLib.SOURCE_REMOVE\n'),
('    def _refresh(self) -> None:\n','    def _refresh(self) -> None:\n        if self.continuity: self.continuity.invalidate()\n'),
('    def _toggle_mute(self, button: Gtk.ToggleButton) -> None:\n','    def _toggle_mute(self, button: Gtk.ToggleButton) -> None:\n        if self.continuity: return\n'),
('    def _toggle_speaker(self, button: Gtk.ToggleButton) -> None:\n','    def _toggle_speaker(self, button: Gtk.ToggleButton) -> None:\n        if self.continuity: return\n'),
('    def _sync_audio_route(self) -> None:\n','''    def _sync_audio_route(self) -> None:
        if self.continuity:
            for button in (self.mute_button, self.speaker_button, self.keypad_button):
                button.set_sensitive(False)
                button.set_tooltip_text("Call audio remains on your phone")
            return
'''),
('            self.mute_button.set_active(audio_input_muted())','            if not self.continuity: self.mute_button.set_active(audio_input_muted())'),
('        self.monitor.stop()','        if self.monitor: self.monitor.stop()\n        if self.continuity: self.continuity.close()\n        if self._paired_ringer: self._paired_ringer.stop()'),
('    def _native_state_changed(self, call_id: str, state: str, reason: str) -> None:\n','''    def _paired_calls_changed(self, _snapshot, ready) -> None:
        if self._closed: return
        self.capability = PhoneCapability(ready and self.continuity.control_authorized(),
            "Calls via your phone. Audio stays on your phone." if ready else "Phone connection unavailable.")
        self._show_service_state(self.capability.available, self.capability.reason)
        self._set_number(self.number)
        calls = self.continuity.calls() if ready else ()
        live = next((call for call in calls if call.phase not in {CallPhase.ENDED, CallPhase.FAILED, CallPhase.IDLE}), None)
        if live:
            self.transport = self.continuity
            self.session = CallSession.from_native(live)
            self._present_call()
            if live.phase is CallPhase.INCOMING:
                self._paired_ringer.start(); self.present()
            else: self._paired_ringer.stop()
        else:
            self._paired_ringer.stop()
            if not ready and self.session.phase is not CallPhase.IDLE:
                self._stop_timer()
                self.call_state.set_label("PHONE DISCONNECTED")
                self.call_timer.set_label("Continue on your phone")
            elif ready and self.session.phase is not CallPhase.IDLE:
                self.session = self.session.deleted("remote-ended")
                self._render_call_state()
        for button in (self.accept_button, self.decline_button, self.end_button):
            button.set_sensitive(ready and not self._paired_control_pending and self.continuity.control_authorized())

    def _paired_control(self, operation) -> None:
        if self._paired_control_pending or not self.session.call_id: return
        self._paired_control_pending = True
        for button in (self.accept_button, self.decline_button, self.end_button): button.set_sensitive(False)
        method = {"answer": self.continuity.accept, "decline": self.continuity.decline, "hangup": self.continuity.hangup}[operation]
        future = self.executor.submit(method, self.session.call_id)
        def done(result):
            def publish():
                if self._closed: return GLib.SOURCE_REMOVE
                self._paired_control_pending = False
                try: result.result()
                except Exception:
                    self._show_error("Call request not confirmed", "Check the current call state before trying again.")
                self.continuity.invalidate()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(publish)
        future.add_done_callback(done)

    def _native_state_changed(self, call_id: str, state: str, reason: str) -> None:
'''),
]
for old,new in changes:
    expected=2 if old=='        self.capability = inspect_phone_capability()' else 1
    if s.count(old)!=expected:raise SystemExit('Phone source context changed: '+old[:80])
    s=s.replace(old,new)
compile(s,str(path),'exec')
patch=''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='a/prairie_apps/phone.py',tofile='b/prairie_apps/phone.py'))
daemon=a.source/'bin/prairie-phone-daemon';old=daemon.read_text()
start=old.index('class Ringer:');end=old.index('class ScreenWaker:',start)
ringer=old[start:end].replace('def __init__(self) -> None:', 'def __init__(self, app_id="org.projectluma.PhoneDaemon") -> None:\n        self.app_id = app_id').replace('(APP_ID, "phone-incoming-call"','(self.app_id, "phone-incoming-call"')
module='# SPDX-License-Identifier: Apache-2.0\n"""Shared native incoming-call feedback, owned by feedbackd."""\nfrom gi.repository import Gio, GLib\n\n'+ringer
updated=old[:start]+'from prairie_apps.phone_feedback import Ringer\n\n\n'+old[end:]
compile(module,'phone_feedback.py','exec');compile(updated,str(daemon),'exec')
patch+=''.join(difflib.unified_diff(old.splitlines(True),updated.splitlines(True),fromfile='a/bin/prairie-phone-daemon',tofile='b/bin/prairie-phone-daemon'))
patch+=''.join(difflib.unified_diff([],module.splitlines(True),fromfile='/dev/null',tofile='b/prairie_apps/phone_feedback.py'))
backend=a.source/'prairie_apps/phone_backend.py';before=backend.read_text()
anchor='    def accept(self, call_id: str) -> None:'
if before.count(anchor)!=1:raise SystemExit('IMS transport context changed')
after=before.replace(anchor,'''    def decline(self, call_id: str) -> None:
        if not call_id:
            raise ValueError("Invalid call identifier.")
        # The native reducer compares and declines atomically. Never emulate
        # this with GetCalls followed by HangUp: Accept may win between them.
        if self._call("Decline", "(s)", (call_id,)) != (True,):
            raise PermissionError("The incoming call has changed.")

'''+anchor)
compile(after,str(backend),'exec')
patch+=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/prairie_apps/phone_backend.py',tofile='b/prairie_apps/phone_backend.py'))
a.output.write_text(patch)
