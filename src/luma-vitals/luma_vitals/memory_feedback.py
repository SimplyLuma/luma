# SPDX-License-Identifier: Apache-2.0
"""Explain verified memory failures through the existing Vitals session loop.

No process is stopped here. A terminal unit failure from the person's
real systemd user manager is evidence; a disappearing window, SIGBUS or an arbitrary log line is not.
"""
from __future__ import annotations

import json
import subprocess
import time

from .agents import Notifier
from .crashes import _field, _json_lines
from .detectors import Event, friendly

# SD_MESSAGE_UNIT_FAILURE_RESULT, systemd259 unit.c: terminal failure outcome.
UNIT_FAILURE_MESSAGE_ID = 'd9b373ed55a64feb8242e02dbe79a49c'
SYSTEMD_EXECUTABLE = '/usr/lib/systemd/systemd'
MAX_RECORDS = 256
MAX_NOTIFICATIONS = 5
FIELDS = '__CURSOR,_BOOT_ID,_COMM,_EXE,_UID,USER_UNIT,USER_INVOCATION_ID,INVOCATION_ID,UNIT_RESULT,MESSAGE_ID'


def memory_query(arguments: list[str]) -> str:
    """A slow/unreadable journal must not delay Vitals' resource sampling."""
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout if result.returncode == 0 else ''


class MemoryWatch:
    def __init__(self, crash_watch, runner=None):
        self.watch = crash_watch
        self.runner = runner or memory_query

    def events(self) -> list[Event]:
        watch = self.watch
        if not watch.boot_id:
            return []
        if watch.state.get('memory-feedback-boot') != watch.boot_id:
            watch.state.set('memory-feedback-boot', watch.boot_id)
            for key in ('memory-user-cursor', 'memory-seen'):
                watch.state.set(key, '')
        try:
            seen = json.loads(watch.state.get('memory-seen') or '[]')
            if not isinstance(seen, list):
                seen = []
        except (TypeError, ValueError):
            seen = []
        seen = [key for key in seen if isinstance(key, str)][-MAX_RECORDS:]
        found = []
        cursor = watch.state.get('memory-user-cursor')
        query = ['journalctl', '-b', '0', '-o', 'json', '--no-pager',
                 f'--lines={MAX_RECORDS}', f'--output-fields={FIELDS}',
                 '_COMM=systemd', f'_EXE={SYSTEMD_EXECUTABLE}', f'_UID={watch.uid}',
                 f'MESSAGE_ID={UNIT_FAILURE_MESSAGE_ID}', 'UNIT_RESULT=oom-kill']
        if cursor:
            query.append(f'--after-cursor={cursor}')
        entries = _json_lines(self.runner(query))
        if entries:
            watch.state.set('memory-user-cursor', _field(entries[-1], '__CURSOR') or cursor or '')
        for entry in entries:
            if (_field(entry, '_BOOT_ID') != watch.boot_id or
                    _field(entry, '_COMM') != 'systemd' or
                    _field(entry, '_EXE') != SYSTEMD_EXECUTABLE or
                    _field(entry, '_UID') != str(watch.uid) or
                    _field(entry, 'MESSAGE_ID') != UNIT_FAILURE_MESSAGE_ID or
                    _field(entry, 'UNIT_RESULT') != 'oom-kill'):
                continue
            unit = _field(entry, 'USER_UNIT')
            if not unit.endswith(('.scope', '.service')) or '/' in unit or any(ord(char) < 32 for char in unit):
                continue
            invocation = _field(entry, 'USER_INVOCATION_ID') or _field(entry, 'INVOCATION_ID')
            identity = 'unit:' + unit + ':' + invocation if invocation else 'cursor:' + _field(entry, '__CURSOR')
            if identity == 'cursor:' or identity in seen:
                continue
            seen.append(identity)
            found.append(Event('out-of-memory', unit,
                               f'{friendly(unit)} closed after running out of memory',
                               {'source': 'systemd-user-final-result', 'cursor': _field(entry, '__CURSOR'),
                                'invocation': invocation, 'boot_id': watch.boot_id}))
        watch.state.set('memory-seen', json.dumps(seen[-MAX_RECORDS:]))
        return found


class MemoryFeedback:
    def __init__(self, notifier=None, clock=time.monotonic):
        self.notifier = notifier or Notifier()
        self.clock = clock
        self.told = {}
        self.pending = {}
        self.next_pressure_notice = 0

    def observe(self, events: list[Event]) -> None:
        killed = [event for event in events if event.kind == 'out-of-memory']
        # Repeated terminal records must not produce repeated notices.
        now = self.clock()
        self.told = {unit: stamp for unit, stamp in self.told.items() if now - stamp < 120}
        for event in killed:
            if event.unit not in self.told:
                self.pending[event.unit] = event
        self.pending = dict(list(self.pending.items())[-MAX_RECORDS:])
        unique = list(self.pending)
        names = [friendly(unit) for unit in unique]
        if names:
            if len(names) == 1:
                title = f'{names[0]} closed because memory ran out'
            else:
                title = 'Memory ran out'
            shown = ', '.join(names[:MAX_NOTIFICATIONS])
            sent = self.notifier.notify(title, f'{shown} closed to free memory. You can open it again when you’re ready.'
                                 if len(names) == 1 else
                                 f'{shown} closed to free memory. You can open them again when you’re ready.')
            if sent:
                self.told.update({unit: now for unit in unique})
                self.pending.clear()
                self.next_pressure_notice = now + 300
        elif (not killed and now >= self.next_pressure_notice and
              any(event.kind == 'memory-pressure' for event in events)):
            sent = self.notifier.notify('Memory is under pressure',
                                       'Save your work and close unused apps to help this computer stay responsive.')
            # Sustained pressure must not interrupt every resource sample.
            # An unavailable endpoint can retry on a later measured event.
            self.next_pressure_notice = now + (300 if sent else 60)
