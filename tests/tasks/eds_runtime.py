# SPDX-License-Identifier: Apache-2.0
"""Run only under a private dbus-run-session with throwaway XDG directories."""
from pathlib import Path
import os
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
assert os.environ.get("LUMA_TASKS_ISOLATED_TEST") == "1", "Refusing to modify real EDS data"
assert "/luma-tasks-test-" in os.environ.get("XDG_DATA_HOME", ""), "Need isolated data directory"
from prairie_apps.tasks_backend import TasksRepository, read_task, _modules
from datetime import date
from gi.repository import GLib

changes = []
repo = TasksRepository(lambda: changes.append(True))
sources, records = repo.load()
assert sources and not records, (sources, records)
source = next(s for s in sources if s.default)
uid = repo.add(source.uid, {"title": "Roundtrip fixture", "due": date(2026, 9, 22), "priority": 5})
component = repo._get(source.uid, uid)
_, _, I = _modules()
for line in ("RRULE:FREQ=WEEKLY;COUNT=3", "X-OTHER-CLIENT:keep", "CATEGORIES:alpha,beta"):
    component.add_property(I.Property.new_from_string(line))
repo._write(source.uid, component)
task = read_task(repo._get(source.uid, uid), source.uid)
action = repo.edit(task, {"title": "Renamed fixture"})
assert "X-OTHER-CLIENT:keep" in repo._get(source.uid, uid).as_ical_string()
repo.undo(action)
step_uid = repo.add(source.uid, {"title": "Checklist step", "parent": uid, "completed": True})
task = read_task(repo._get(source.uid, uid), source.uid)
action, message = repo.complete(task, True)
assert read_task(repo._get(source.uid, uid), source.uid).due == date(2026, 9, 29)
assert not read_task(repo._get(source.uid, step_uid), source.uid).completed
repo.undo(action)
assert read_task(repo._get(source.uid, step_uid), source.uid).completed
repo.configure(source.uid, ("Website", "Press", "Release"), (("ada@example.com", "Ada Verlaine"),), True)
sources, tasks = repo.load()
assert sources[0].sections == ("Website", "Press", "Release"), sources
assert sources[0].members == (("ada@example.com", "Ada Verlaine"),), sources
assert len(tasks) == 2, tasks
repo.comment(next(t for t in tasks if t.uid == uid), "Test comment", "Ada")
assert len(read_task(repo._get(source.uid, uid), source.uid).comments) == 1
source2 = repo.create_list("Second test list")
sources, _ = repo.load()
assert source2 in {s.uid for s in sources}
repo.add(source2, {"title": "Second source"})
_, tasks = repo.load()
assert len(tasks) == 3
for _ in range(100):
    while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
    if changes: break
    time.sleep(.01)
assert changes, "EDS view did not deliver changes"
task = read_task(repo._get(source.uid, uid), source.uid)
action = repo.delete(task)
repo.undo(action)
assert repo._get(source.uid, uid).get_uid() == uid
# Detached occurrences must never update or delete their master by accident.
master = I.Component.new_from_string("BEGIN:VTODO\r\nUID:detached-fixture\r\nSUMMARY:Master\r\nDTSTART;VALUE=DATE:20260922\r\nDUE;VALUE=DATE:20260922\r\nRRULE:FREQ=WEEKLY\r\nEND:VTODO\r\n")
repo._create(source.uid, master)
exception = master.clone()
exception.add_property(I.Property.new_from_string("RECURRENCE-ID;VALUE=DATE:20260929"))
exception.set_summary("Exception")
repo._write(source.uid, exception)
task = read_task(repo._get(source.uid, "detached-fixture", "20260929"), source.uid)
action = repo.edit(task, {"title": "Exception renamed"})
assert repo._get(source.uid, "detached-fixture").get_summary() == "Master"
repo.undo(action)
task = read_task(repo._get(source.uid, "detached-fixture", "20260929"), source.uid)
action = repo.delete(task)
repo.undo(action)
assert repo._get(source.uid, "detached-fixture", "20260929").get_summary() == "Exception"
assert repo._get(source.uid, "detached-fixture").get_summary() == "Master"
master_task = read_task(repo._get(source.uid, "detached-fixture"), source.uid)
action = repo.delete(master_task)
repo.undo(action)
assert repo._get(source.uid, "detached-fixture", "20260929").get_summary() == "Exception"
repo.close()
print("PASS: real isolated EDS create, multi-source, edit, repeat, comments, metadata, live view, delete and undo")
