# SPDX-License-Identifier: Apache-2.0
"""Private-display fixture interactions; no captures and no real EDS access."""
from pathlib import Path
import os
import sys
import time
import threading
assert os.environ.get("LUMA_TASKS_ISOLATED_TEST") == "1"
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "src/prairie-core"), str(root / "src/luma-platform/appkit")]
os.environ["LUMA_TASKS_FIXTURE"] = str(root / "tests/fixtures/tasks-v70.json")
from prairie_apps.tasks import TasksApplication
from gi.repository import GLib
fixture = Path(os.environ["LUMA_TASKS_FIXTURE"])
before = fixture.read_bytes()
app = TasksApplication()
assert app.register(None)
app.activate()
window = app.props.active_window
assert window is not None, "Window startup failed"
def wait(predicate, message, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for _ in range(50):
            if not GLib.MainContext.default().pending(): break
            GLib.MainContext.default().iteration(False)
        if predicate(): return
        time.sleep(.01)
    raise AssertionError(message)
wait(lambda: len(window.data["tasks"]) == 16, "Fixture load")
def find_named(widget, name):
    if widget.get_name() == name: return widget
    child = widget.get_first_child()
    while child:
        found = find_named(child, name)
        if found: return found
        child = child.get_next_sibling()
def check_share_alignment(view):
    window._select_view(view)
    wait(lambda: window.view == view and not window.busy, "List header ready")
    share = find_named(window.page, "tk-share")
    words = window.page.get_first_child().get_first_child()
    assert share is not None
    def aligned():
        share_ok, share_rect = share.compute_bounds(window)
        words_ok, words_rect = words.compute_bounds(window)
        return (share.get_mapped() and words.get_mapped() and share_ok and words_ok
                and share_rect.get_height() > 0 and words_rect.get_height() > 0
                and abs(share_rect.get_y() + share_rect.get_height()
                        - words_rect.get_y() - words_rect.get_height()) <= 1)
    wait(aligned, "Share aligns with the bottom of the title/subtitle: " + view)
    print("PASS: list header Share bottom alignment", view, window.get_width())
for view in ("personal", "launch"):
    check_share_alignment(view)
def check_title_allocation(key, *, phone=False):
    window._pick(key)
    wait(lambda: window.details.subject == key, "Title details open")
    if phone:
        wait(lambda: window.details.is_drawer, "Phone title is in the actual details drawer")
    _, title, _ = window._pending_fields
    def fully_allocated():
        buffer = title.get_buffer()
        last = buffer.get_end_iter()
        last.backward_char()
        first_rect = title.get_iter_location(buffer.get_start_iter())
        last_rect = title.get_iter_location(last)
        return (title.get_mapped() and title.get_width() > 0 and first_rect.height > 0
                and title.get_height() >= last_rect.y + last_rect.height)
    wait(fully_allocated, "Task title has room for every wrapped line: " + key)
    assert title.text == window.task(key)["t"]
    print("PASS: title allocation", key, title.get_width(), title.get_height())
for key in ("t104", "t100"):
    check_title_allocation(key)
window._pick("t100")
wait(lambda: window.details.subject == "t100", "Details open")
edit = window.source.edit
entered, release = threading.Event(), threading.Event()
def delayed_edit(key, changes):
    entered.set()
    assert release.wait(8), "Delayed fixture save was not released"
    return edit(key, changes)
window.source.edit = delayed_edit
try:
    key, title, notes = window._pending_fields
    title.get_buffer().set_text("First submitted title")
    notes.get_buffer().set_text("First submitted notes")
    window._flush_fields()
    wait(entered.is_set, "Fixture save entered worker")
    title.get_buffer().set_text("Newer title typed during save")
    notes.get_buffer().set_text("Newer notes typed during save")
finally:
    release.set()
wait(lambda: not window.busy and window.task("t100")["t"] == "First submitted title", "First fixture save completed")
wait(lambda: window._pending_fields is not None, "Newer detail draft survives save completion")
_, title, notes = window._pending_fields
assert title.text == "Newer title typed during save", "Save discarded a newer title draft"
assert notes.text == "Newer notes typed during save", "Save discarded a newer notes draft"
window.source.edit = edit
window._select_view("personal")
wait(lambda: window.view == "personal" and not window.busy and window.task("t100")["t"] == "Newer title typed during save", "Navigation saves the newer detail draft")
assert window.task("t100")["notes"] == "Newer notes typed during save"
for reverted_fields in (("t",), ("notes",), ("t", "notes")):
    window._select_view("personal")
    window._pick("t100")
    key, title, notes = window._pending_fields
    baseline = {field: window.task(key)[field] for field in ("t", "notes")}
    editors = {"t": title, "notes": notes}
    entered, release = threading.Event(), threading.Event()
    writes = []
    def delayed_reverted_edit(key, changes):
        writes.append(dict(changes))
        entered.set()
        assert release.wait(8), "Reverted fixture save was not released"
        return edit(key, changes)
    window.source.edit = delayed_reverted_edit
    try:
        for field in reverted_fields:
            editors[field].get_buffer().set_text(baseline[field] + " [in-flight]")
        window._flush_fields()
        wait(entered.is_set, "Reverted fixture save entered worker")
        for field in reverted_fields:
            editors[field].get_buffer().set_text(baseline[field])
        window._select_view("home")
        assert window.view == "personal", "Navigation discarded a reversion during an in-flight save: " + str(reverted_fields)
    finally:
        release.set()
        window.source.edit = edit
    wait(lambda: window.view == "home" and not window.busy
         and all(window.task(key)[field] == baseline[field] for field in ("t", "notes")),
         "Navigation persists the reverted title/notes: " + str(reverted_fields))
    assert set(writes[0]) == set(reverted_fields), "Unedited fields entered the first write"
    print("PASS: navigation preserves in-flight reversion", reverted_fields)
for field_name, operation_name, submit_name in (
    ("tk-comment", "comment", "_comment"),
    ("tk-add-step", "add_step", "_add_step"),
):
    window._pick("t100")
    entry = find_named(window, field_name)
    entry.set_text("Unsent " + field_name)
    window._reload()
    wait(lambda: find_named(window, field_name) is not entry, "Details refreshed")
    entry = find_named(window, field_name)
    assert entry.get_text() == "Unsent " + field_name, "Reload discarded the unsent draft: " + field_name
    operation = getattr(window.source, operation_name)
    def submit(entry):
        if submit_name == "_comment":
            getattr(window, submit_name)("t100", entry.get_parent())
        else:
            getattr(window, submit_name)("t100", entry.get_text())
    def failed_draft(*_): raise RuntimeError("Isolated draft write failure")
    setattr(window.source, operation_name, failed_draft)
    submit(entry)
    wait(lambda: not window.busy, "Failed draft write")
    assert find_named(window, field_name).get_text() == "Unsent " + field_name
    entered, release = threading.Event(), threading.Event()
    def delayed_draft(*args):
        entered.set()
        assert release.wait(8), "Draft write was not released"
        return operation(*args)
    setattr(window.source, operation_name, delayed_draft)
    try:
        submit(entry)
        wait(entered.is_set, "Draft write entered worker")
        entry.set_text("Newer " + field_name)
    finally:
        release.set()
        setattr(window.source, operation_name, operation)
    wait(lambda: not window.busy and find_named(window, field_name) is not entry,
         "Successful draft write refreshed details")
    assert find_named(window, field_name).get_text() == "Newer " + field_name, "Completion discarded a newer draft: " + field_name
    task = window.task("t100")
    if operation_name == "comment":
        assert any(row[2] == "Unsent " + field_name for row in task["act"] if row[1] == "c")
    else:
        assert any(row[0] == "Unsent " + field_name for row in task["subs"])
    window._pick("t104")
    assert find_named(window, field_name).get_text() == "", "Draft leaked into another task"
    window._pick("t100")
    assert find_named(window, field_name).get_text() == "Newer " + field_name
    submit(find_named(window, field_name))
    wait(lambda: not window.busy and find_named(window, field_name).get_text() == "",
         "Submitted draft cleared after success")
    print("PASS: reload/failure/newer completion and task-scoped draft", field_name)
window._pick("t100")
key, title, notes = window._pending_fields
title.get_buffer().set_text("Edited fixture title")
notes.get_buffer().set_text("Edited fixture notes")
window._select_view("personal")
wait(lambda: window.task("t100")["t"] == "Edited fixture title" and window.view == "personal" and not window.busy, "Flush fields before navigation")
assert window.task("t100")["notes"] == "Edited fixture notes"
window._pick("t100")
window._edit("t100", {"done": True})
wait(lambda: window.task("t100")["done"] and not window.busy, "Complete")
window._mutate(window.undo_action, "Undone")
wait(lambda: not window.task("t100")["done"] and not window.busy, "Undo")
window._new()
window.add_field.set_text("Runtime task tomorrow !high")
window._draft_changed(window.add_field.text)
window._mutate(lambda: time.sleep(.1))
window._add_task()
assert window.draft == "Runtime task tomorrow !high", "Busy submission discarded the draft"
assert window.add_field.text == window.draft
wait(lambda: not window.busy, "Background write before quick add")
assert len(window.data["tasks"]) == 16
add = window.source.add
def failed_add(_parsed): raise RuntimeError("Isolated add failure")
window.source.add = failed_add
window._add_task()
wait(lambda: not window.busy, "Failed add")
assert window.draft == "Runtime task tomorrow !high", "Failed submission discarded the draft"
assert window.add_field.text == window.draft
window.source.add = add
window._add_task()
window.add_field.set_text("Next draft")
window._draft_changed(window.add_field.text)
wait(lambda: len(window.data["tasks"]) == 17 and not window.busy, "Quick add")
added = next(t for t in window.data["tasks"] if t["t"] == "Runtime task")
assert added["due"] == 1 and added["pri"] == 3
assert window.selected == added["id"], "A successful quick add did not reveal its task"
assert window.draft == "Next draft" and window.add_field.text == "Next draft", "Completed add discarded a newer draft"
window._finish_entry()
wait(lambda: not window.busy and window.task(added["id"]) is not None, "Quick-entry close")
window._select_view("flag")
window._search("No matching task")
window._new()
window.add_field.set_text("Fresh from Flagged")
window._draft_changed(window.add_field.text)
window.add_field.widget.line.emit("activate")  # The Enter path, with no due date or flag.
wait(lambda: len(window.data["tasks"]) == 18 and not window.busy, "Enter created the task")
revealed = next(t for t in window.data["tasks"] if t["t"] == "Fresh from Flagged")
assert window.selected == revealed["id"] and window.view == revealed["l"] and not window.query, \
    "Enter saved a task that remained hidden by its previous view or search"
window._finish_entry()
window._pick(added["id"])
key, title, notes = window._pending_fields
title.get_buffer().set_text("Draft kept after delete undo")
notes.get_buffer().set_text("Draft notes kept after delete undo")
window._delete()
wait(lambda: window.task(key) is None and not window.busy, "Delete flushes the dirty draft")
window._mutate(window.undo_action, "Undone")
wait(lambda: window.task(key) is not None and not window.busy, "Delete undo restores the task")
assert window.task(key)["t"] == "Draft kept after delete undo"
assert window.task(key)["notes"] == "Draft notes kept after delete undo"
window._pick("t100")
key, title, notes = window._pending_fields
title.get_buffer().set_text("Draft during background edit")
window._mutate(lambda: (time.sleep(.05), window.source.edit(key, {"notes": "Background notes"}))[1])
window._select_view("home")
wait(lambda: window.view == "home" and not window.busy and window.task(key)["t"] == "Draft during background edit", "Navigation deferred until edits finish")
assert window.task(key)["notes"] == "Background notes", "Unedited stale notes overwrote the background edit"
ticks, finished = [], []
GLib.timeout_add(10, lambda: ticks.append(True) or not finished)
window._run(lambda: time.sleep(.4), lambda result, error: finished.append(True))
wait(lambda: finished, "Worker completion")
assert len(ticks) >= 15, ticks
window.details.close()
wait(lambda: not window.details.shown and not window.busy, "Close desktop details before phone layout")
window.set_default_size(390, 820)
wait(lambda: 0 < window.get_width() <= 639, "Phone resize")
for view in ("personal", "launch"):
    check_share_alignment(view)
for key in ("t104", "t112"):
    check_title_allocation(key, phone=True)
window._select_view("launch")
wait(lambda: window.view == "launch" and not window.busy, "Shared list")
anchor = find_named(window, "tk-share")
assert anchor is not None
window._share(anchor)
from luma_appkit import MenuSection
sections = [row.widget for row in window._sharing_menu._rows if isinstance(row, MenuSection)]
assert len(sections) == 5, "Four people and the invitation field are required"
wait(lambda: all(w.get_mapped() and w.get_width() > 0 for w in sections), "Phone sharing sections survive reparenting")
window._sharing_menu.close()
assert fixture.read_bytes() == before
window.close(); app.quit()
print("PASS: fixture startup, newer title/notes during save, edit flush, completion/undo, quick entry, dirty delete/undo, responsive worker and phone sharing sections")
