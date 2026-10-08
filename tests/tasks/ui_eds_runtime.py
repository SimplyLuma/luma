# SPDX-License-Identifier: Apache-2.0
"""Native interactions against the disposable EDS server, never user data."""
from pathlib import Path
import os
import json
import sys
import time
from datetime import date, timedelta
assert os.environ.get("LUMA_TASKS_ISOLATED_TEST") == "1"
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "src/prairie-core"), str(root / "src/luma-platform/appkit")]
os.environ.pop("LUMA_TASKS_FIXTURE", None)
from prairie_apps.tasks import TasksApplication
from prairie_apps.tasks_backend import TasksRepository
from gi.repository import GLib
app = TasksApplication()
assert app.register(None)
app.activate()
window = app.props.active_window
assert window is not None

def wait(predicate, message, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for _ in range(50):
            if not GLib.MainContext.default().pending(): break
            GLib.MainContext.default().iteration(False)
        if predicate(): return
        time.sleep(.01)
    raise AssertionError(message)

wait(lambda: window.data["lists"], "Initial load")
window._new()
window.add_field.set_text("Invoice tomorrow !!")
window._draft_changed(window.add_field.text)
window._add_task()
wait(lambda: len(window.data["tasks"]) == 1 and not window.busy, "Quick add")
window._finish_entry()
task = window.data["tasks"][0]
assert task["t"] == "Invoice" and task["pri"] == 2 and task["due"] == 1
key = task["id"]
window._pick(key)
window._edit(key, {"flag": True})
wait(lambda: window.task(key).get("flag") and not window.busy, "Persistent local flag")
window._edit(key, {"who": ["me", "local@example.com"]})
wait(lambda: window.task(key)["who"] == ["me", "local@example.com"] and not window.busy, "Local multiple assignees")
window.source.load()
assert window.source.data["tasks"][0]["flag"] is True
assert window.source.data["tasks"][0]["who"] == ["me", "local@example.com"]
assert window.source.records[key].assignee == ""
backup_files = list((Path(os.environ["XDG_DATA_HOME"]) / "luma/tasks/backups").glob("*.json"))
assert len(backup_files) == 2
backup_bytes = {p: p.read_bytes() for p in backup_files}
local_backup = next(p for p in backup_files if p.name == "local-original.json")
assert json.loads(backup_bytes[local_backup]) == {"version": 1, "tasks": {}}
_, title, notes = window._pending_fields
title.get_buffer().set_text("Invoice edited")
notes.get_buffer().set_text("Persistent notes")
window._select_view("upcoming")
wait(lambda: window.task(key)["t"] == "Invoice edited" and window.view == "upcoming" and not window.busy, "Detail save on navigation")
assert window.task(key)["notes"] == "Persistent notes"
window._edit(key, {"done": True})
wait(lambda: window.task(key)["done"] and not window.busy, "Complete")
window._mutate(window.undo_action)
wait(lambda: not window.task(key)["done"] and not window.busy, "Undo completion")
window._add_step(key, "Checklist step")
wait(lambda: len(window.task(key)["subs"]) == 1 and not window.busy, "Add checklist step")
window._mutate(lambda: window.source.delete(key), deselect=True)
wait(lambda: not window.data["tasks"] and not window.busy, "Delete parent and step")
window._mutate(window.undo_action)
wait(lambda: len(window.data["tasks"]) == 1 and len(window.task(key)["subs"]) == 1 and not window.busy, "Undo parent and step")
other = TasksRepository()
_, records = other.load()
parent = next(t for t in records if not t.parent)
other.edit(parent, {"title": "Changed by another client"})
wait(lambda: window.task(key)["t"] == "Changed by another client", "External live edit")
other.close()
ticks, finished = [], []
GLib.timeout_add(10, lambda: ticks.append(True) or not finished)
window._run(lambda: time.sleep(.4), lambda result, error: finished.append(True))
wait(lambda: finished, "Slow worker")
assert len(ticks) >= 15
assert all(p.read_bytes() == original for p, original in backup_bytes.items())
window.close(); app.quit()
print("PASS: disposable EDS quick add, detail flush, completion/undo, checklist deletion/undo, external edit and worker")
