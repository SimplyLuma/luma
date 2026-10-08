#!/usr/bin/env python3
"""Produce a focused patch without modifying the frozen canonical candidate."""
import argparse
import difflib
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
original = args.source.read_text()
modified = original
replacements = [
('        self.store = MessageStore(); self.store.recover_interrupted()\n',
 '        self.continuity = getattr(application, "message_provider", None)\n'
 '        if self.continuity is None:\n'
 '            try:\n'
 '                from luma_continuity.message_provider import selected_provider\n'
 '            except ModuleNotFoundError as error:\n'
 '                if error.name not in {"luma_continuity", "luma_continuity.message_provider"} or (Path.home() / ".config/luma-connect/messages-phone.json").exists(): raise\n'
 '            else:\n'
 '                self.continuity = selected_provider(store_factory=MessageStore, dispatch=GLib.idle_add)\n'
 '        self.store = MessageStore(self.continuity.store_path if self.continuity else None)\n'
 '        self.store.recover_interrupted()\n'),
('        self.transport = ModemMessagingTransport()\n        self.mms_transport = MmsMessagingTransport()\n',
 '        # The selected provider must never acquire the local modem.\n'
 '        self.transport = self.continuity.sms_transport() if self.continuity else ModemMessagingTransport()\n'
 '        self.mms_transport = self.continuity.mms_transport() if self.continuity else MmsMessagingTransport()\n'),
('        contacts = load_contacts()\n',
 '        contacts = self.continuity.contacts() if self.continuity else load_contacts()\n'),
('            record = writer.message(uid)\n            if record.attachments:\n',
 '            record = writer.message(uid)\n'
 '            if self.continuity:\n'
 '                result = self.continuity.send_message(uid)\n'
 '                state = result["state"]\n'
 '            elif record.attachments:\n'),
('    def __init__(self) -> None:\n        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE); self.pending_address = ""',
 '    def __init__(self, *, message_provider=None) -> None:\n'
 '        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE); self.pending_address = ""\n'
  '        self.message_provider = message_provider'),
('            writer.update_state(\n                uid, state, transport_id,\n            )\n',
 '            # Provider owns queue/receipt state; a fast receipt must not be overwritten.\n'
 '            if self.continuity is None or error:\n'
 '                writer.update_state(uid, state, transport_id)\n'),
('    def _poll_received(self) -> bool:\n        if self.closed: return GLib.SOURCE_REMOVE\n',
 '    def _poll_received(self) -> bool:\n        if self.closed: return GLib.SOURCE_REMOVE\n'
 '        if self.continuity:\n'
 '            self.continuity.refresh()\n'
 '            return GLib.SOURCE_CONTINUE\n'),
('        self._reload_threads()\n        GLib.idle_add(self._start_external_context_load)\n',
 '        self._reload_threads()\n'
 '        if self.continuity: self.continuity.start(self._continuity_changed)\n'
 '        GLib.idle_add(self._start_external_context_load)\n'),
('    def _start_external_context_load(self) -> bool:\n',
 '    def _continuity_changed(self, state) -> None:\n'
 '        if self.closed: return\n'
 '        self.capability = self.transport.inspect()\n'
 '        detail = {"offline": "Phone offline. Messages stay queued.",\n'
 '                  "unavailable": "Phone access is unavailable.",\n'
 '                  "attention": "Some sends need review. Do not resend until their status is known."}.get(state["state"], self.capability.reason)\n'
 '        self.transport_status.set_label(detail); self.transport_status.set_visible(True)\n'
 '        self._composer_changed(self.composer_buffer)\n'
 '        self._reload_threads(); self._render_messages()\n\n'
 '    def _start_external_context_load(self) -> bool:\n'),
('        self.closed = True\n        if self.presence_expiry:',
 '        self.closed = True\n        if self.continuity: self.continuity.close()\n        if self.presence_expiry:'),
]
for old, new in replacements:
    if modified.count(old) != 1:
        raise SystemExit('Canonical source changed; re-review provider patch context')
    modified = modified.replace(old, new)
# Existing status row becomes explicit device/queue provenance when selected.
modified = modified.replace('set_visible(not capability.available)', 'set_visible(self.continuity is not None or not capability.available)')
modified = modified.replace('set_visible(not self.capability.available)', 'set_visible(self.continuity is not None or not self.capability.available)')
modified = modified.replace('self.transport_status.set_visible(not ready)', 'self.transport_status.set_visible(self.continuity is not None or not ready)')
compile(modified, str(args.source), 'exec')
patch = ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True),
    fromfile='a/src/prairie-core/prairie_apps/messages.py',
    tofile='b/src/prairie-core/prairie_apps/messages.py'))
args.output.write_text(patch)
print('source sha256:', hashlib.sha256(original.encode()).hexdigest())
print('patch sha256:', hashlib.sha256(patch.encode()).hexdigest())
