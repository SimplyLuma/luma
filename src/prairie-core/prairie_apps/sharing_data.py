# SPDX-License-Identifier: Apache-2.0
"""Portable calendar/task copies; a device-local identifier is never a link."""
from datetime import datetime, timezone


def _text(value):
    return str(value or '').replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _time(name, value):
    if value is None: return []
    if isinstance(value, datetime):
        return [name + ':' + value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')]
    return [name + ';VALUE=DATE:' + value.strftime('%Y%m%d')]


def _calendar(lines):
    # Fold by octets without cutting a UTF-8 character (RFC5545).
    folded = []
    for line in ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Project Luma//EN', *lines, 'END:VCALENDAR']:
        part = ''
        for char in line:
            if len((part + char).encode('utf-8')) > 75:
                folded.append(part); part = ' '
            part += char
        folded.append(part)
    return '\r\n'.join(folded) + '\r\n'


def event_copy(event):
    return _calendar(['BEGIN:VEVENT', 'UID:' + _text(event.uid), 'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'SUMMARY:' + _text(event.summary),
                      *_time('DTSTART', event.start.date() if event.all_day else event.start),
                      *_time('DTEND', event.end.date() if event.all_day else event.end),
                      'LOCATION:' + _text(event.location), 'DESCRIPTION:' + _text(event.description), 'END:VEVENT'])


def tasks_copy(records):
    lines = []
    for task in records:
        lines += ['BEGIN:VTODO', 'UID:' + _text(task.uid), 'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'SUMMARY:' + _text(task.title),
                  *_time('DTSTART', task.start), *_time('DUE', task.due),
                  'STATUS:' + ('COMPLETED' if task.completed else 'NEEDS-ACTION'),
                  'DESCRIPTION:' + _text(task.notes), 'PRIORITY:' + str(task.priority)]
        if task.parent: lines.append('RELATED-TO;RELTYPE=PARENT:' + _text(task.parent))
        for comment in task.comments: lines.append('COMMENT:' + _text(comment.text))
        lines.append('END:VTODO')
    return _calendar(lines)
