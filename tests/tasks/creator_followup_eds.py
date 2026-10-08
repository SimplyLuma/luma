# SPDX-License-Identifier: Apache-2.0
"""Real isolated EDS intervals, list removal, move verification and rollback."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/prairie-core'))
assert os.environ.get('LUMA_TASKS_ISOLATED_TEST') == '1'
assert '/luma-tasks-test-' in os.environ.get('XDG_DATA_HOME', ''), 'private EDS store required'
from prairie_apps.tasks_backend import TasksRepository, read_task, _modules, value
from prairie_apps.tasks_parse import parse_task
from prairie_apps.sharing_data import tasks_copy
from prairie_apps.tasks_local import data_root
repo = TasksRepository(); repo.load()
_, _, I = _modules()
source = repo.create_list('Move these tasks'); destination = repo.create_list('Keep these tasks')
repo.load()
parsed = parse_task('Lunch tomorrow from 3:00 to 5:00', today=datetime(2026, 10, 6).date())
uid = repo.add(source, {'title': parsed.title, 'start': parsed.start, 'due': parsed.due, 'repeat': 'FREQ=DAILY;COUNT=3'})
component = repo._get(source, uid)
component.add_property(I.Property.new_from_string('X-OTHER-CLIENT:kept'))
component.add_component(I.Component.new_from_string('BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:Reminder\r\nEND:VALARM\r\n'))
repo._write(source, component)
child = repo.add(source, {'title': 'Checklist step', 'parent': uid})
record = read_task(repo._get(source, uid), source)
assert (record.title, record.start, record.due) == ('Lunch', parsed.start, parsed.due)
assert 'DTSTART:' in tasks_copy((record,)) and 'DUE:' in tasks_copy((record,))
repo.comment(record, 'A comment to preserve', 'Tester')
# A recurrence exception must move with its master, never rewrite it.
exception = repo._get(source, uid).clone()
exception.add_property(I.Property.new_from_string('RECURRENCE-ID:' + (parsed.start + timedelta(days=1)).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')))
exception.set_summary('Detached lunch'); repo._write(source, exception)
rid = value(exception, 'RECURRENCE-ID')
repo.configure(source, ('Source section',), (), False)
repo.configure(destination, ('Destination section',), (), False)
kept = repo.add(destination, {'title': 'Existing destination task'})
repo.load()
archive = Path(repo.delete_list(source, destination))
assert archive.stat().st_mode & 0o777 == 0o600
backup = json.loads(archive.read_text()); assert backup['source_data'] and len(backup['components']) == 4
repo.load(); assert source not in {s.uid for s in repo.sources}
moved = read_task(repo._get(destination, uid), destination)
assert moved.start == parsed.start and moved.due == parsed.due and moved.comments[0].text == 'A comment to preserve'
assert 'X-OTHER-CLIENT:kept' in moved.raw and 'BEGIN:VALARM' in moved.raw
assert read_task(repo._get(destination, child), destination).parent == uid
assert repo._get(destination, uid, rid).get_summary() == 'Detached lunch'
assert repo._get(destination, uid).get_summary() == 'Lunch'
assert repo._get(destination, kept).get_summary() == 'Existing destination task'
assert next(s for s in repo.sources if s.uid == destination).sections == ('Destination section',)

failed = repo.create_list('Fail halfway'); repo.load()
first = repo.add(failed, {'title': 'First'}); second = repo.add(failed, {'title': 'Second'})
original_create = repo._create; calls = []
def fail_second(target, component):
    calls.append(component.get_uid())
    if len(calls) == 2: raise RuntimeError('Injected destination failure')
    return original_create(target, component)
repo._create = fail_second
try:
    repo.delete_list(failed, destination)
except RuntimeError as error:
    assert 'Injected destination failure' in str(error)
else: raise AssertionError('failed move must refuse deletion')
finally: repo._create = original_create
repo.load(); assert failed in {s.uid for s in repo.sources}
assert {c.get_uid() for c in repo.clients[failed].get_object_list_sync('#t', None)[1]} == {first, second}
assert not {first, second}.intersection({c.get_uid() for c in repo.clients[destination].get_object_list_sync('#t', None)[1]})
# UID conflict is checked before any mutation, even if the tasks have different titles.
collision = I.Component.new_from_string(f'BEGIN:VTODO\r\nUID:{first}\r\nSUMMARY:Different destination task\r\nEND:VTODO\r\n')
repo._create(destination, collision)
try: repo.delete_list(failed, destination)
except ValueError: pass
else: raise AssertionError('UID conflict must preserve both originals')
assert repo._get(destination, first).get_summary() == 'Different destination task'
assert repo._get(failed, first).get_summary() == 'First'
# Delete-all retains recoverable data before removing the provider-owned source.
archive = Path(repo.delete_list(failed)); assert len(json.loads(archive.read_text())['components']) == 2
repo.load(); assert failed not in {s.uid for s in repo.sources}
repo.close()
print('PASS real EDS interval, comments, unknown fields, alarm, detached occurrence, list move, failure rollback, UID collision, archive and deletion')
