# SPDX-License-Identifier: Apache-2.0
"""Clock-only bounded plans, never commands, paths or caller-selected app IDs."""
import json
import time
from .clock_backend import valid_uid, SOUNDS

BUS = 'org.projectluma.ClockHost1'
OBJECT = '/org/projectluma/ClockHost1'
MAX_BYTES = 128 * 1024

def validate_plan(text, *, now=None):
    if not isinstance(text, str) or len(text.encode()) > MAX_BYTES:
        raise ValueError('Clock plan is too large.')
    data = json.loads(text)
    if type(data) is not dict or set(data) != {'alarms', 'timers'}:
        raise ValueError('Clock accepts only alarms and timers.')
    now = time.time() if now is None else now
    def integer(item, key, low, high):
        value = item.get(key)
        if type(value) is not int or not low <= value <= high: raise ValueError('Invalid Clock '+key+'.')
        return value
    def label(item):
        value = item.get('label')
        if type(value) is not str or len(value) > 256 or '\x00' in value: raise ValueError('Invalid Clock label.')
        return value
    result = {}; seen = set()
    for kind in ('alarms', 'timers'):
        entries = data[kind]
        if type(entries) is not list or len(entries) > 128: raise ValueError('Too many Clock records.')
        result[kind] = []
        for item in entries:
            if type(item) is not dict: raise ValueError('Invalid Clock record.')
            uid = valid_uid(item.get('uid', ''))
            if uid in seen: raise ValueError('Duplicate Clock record.')
            seen.add(uid)
            if kind == 'alarms':
                if set(item) != {'uid','label','hour','minute','days','enabled','sound','snooze_minutes','ring_seconds'}: raise ValueError('Invalid alarm fields.')
                days = item.get('days')
                if type(days) is not list or len(days) > 7 or any(type(d) is not int or not 0 <= d <= 6 for d in days) or len(set(days)) != len(days): raise ValueError('Invalid alarm repeat.')
                if type(item.get('enabled')) is not bool or item.get('sound') not in SOUNDS: raise ValueError('Invalid alarm settings.')
                row = dict(uid=uid,label=label(item),hour=integer(item,'hour',0,23),minute=integer(item,'minute',0,59),days=sorted(days),enabled=item['enabled'],sound=item['sound'],snooze_minutes=integer(item,'snooze_minutes',1,60),ring_seconds=integer(item,'ring_seconds',1,3600))
            else:
                if set(item) != {'uid','label','fires_at','total_seconds'}: raise ValueError('Invalid timer fields.')
                row = dict(uid=uid,label=label(item),fires_at=integer(item,'fires_at',1,int(now)+366*86400),total_seconds=integer(item,'total_seconds',1,366*86400))
            result[kind].append(row)
    return result
