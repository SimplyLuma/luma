#!/usr/bin/env python3
"""Minimal shared Core 71.connect composer correction, with exact contexts."""
import argparse
import difflib
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('source',type=Path)
parser.add_argument('output',type=Path)
args=parser.parse_args()
original=args.source.read_text();modified=original
replacements=[
('self.transport_status.set_label(detail); self.transport_status.set_visible(True)',
 'self._set_transport_status(detail, self.capability.available)'),
('self.transport_status.set_label(capability.reason)\n        self.transport_status.set_visible(self.continuity is not None or not capability.available)',
 'self._set_transport_status(capability.reason, capability.available)'),
('self.transport_status.set_visible(self.continuity is not None or not self.capability.available); bottom.append(self.transport_status)',
 'self._set_transport_status(self.capability.reason, self.capability.available); bottom.append(self.transport_status)'),
('self.transport_status.set_label(reason)\n        self.transport_status.set_visible(self.continuity is not None or not ready)',
 'self._set_transport_status(reason, ready)'),
('self.capability = self.transport.inspect(); self.transport_status.set_label(self.capability.reason); self.transport_status.set_visible(self.continuity is not None or not self.capability.available)',
 'self.capability = self.transport.inspect(); self._set_transport_status(self.capability.reason, self.capability.available)'),
('        composer_keys = Gtk.EventControllerKey(); composer_keys.connect("key-pressed", self._composer_key_pressed); self.composer_view.add_controller(composer_keys)',
 '''        self._composer_return_down = set()
        composer_keys = Gtk.EventControllerKey()
        composer_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        composer_keys.connect("key-pressed", self._composer_key_pressed)
        composer_keys.connect("key-released", self._composer_key_released)
        self.composer_view.add_controller(composer_keys)'''),
('    def _composer_focus_changed(self, *_args) -> None:\n',
 '''    def _composer_focus_changed(self, *_args) -> None:
        if not self.composer_view.has_focus(): self._composer_return_down.clear()
'''),
('''    def _composer_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        if (Gdk.keyval_name(keyval) or "") not in {"Return", "KP_Enter"} or not state & Gdk.ModifierType.CONTROL_MASK: return False
        self._send_message(); return True
''',
'''    def _set_transport_status(self, reason: str, ready: bool) -> None:
        # Hiding the label also removes its CSS padding and layout allocation.
        # Offline/locked/review states remain visible even if a queue can accept.
        actionable = not ready or (self.continuity is not None and self.continuity.status.get("state") != "ready")
        self.transport_status.set_label(reason)
        self.transport_status.set_visible(bool(reason.strip()) and actionable)

    def _composer_key_pressed(self, _controller, keyval, keycode, state) -> bool:
        if (Gdk.keyval_name(keyval) or "") not in {"Return", "KP_Enter"}: return False
        if state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK): return False
        key = keycode or keyval
        if key in self._composer_return_down: return True
        self._composer_return_down.add(key)
        # Capture sees preedit before TextView commits it. Let GTK confirm IME
        # composition, but consume any repeats until this Return is released.
        if self.composer_preedit: return False
        self._send_message()
        return True

    def _composer_key_released(self, _controller, keyval, keycode, _state) -> None:
        self._composer_return_down.discard(keycode or keyval)
'''),
]
for old,new in replacements:
    if modified.count(old)!=1:raise SystemExit('Core composer context changed; re-review required: '+old[:80])
    modified=modified.replace(old,new)
compile(modified,str(args.source),'exec')
args.output.write_text(''.join(difflib.unified_diff(original.splitlines(True),modified.splitlines(True),
    fromfile='a/prairie_apps/messages.py',tofile='b/prairie_apps/messages.py')))
