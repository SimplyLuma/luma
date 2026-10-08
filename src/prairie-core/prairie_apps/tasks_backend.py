# SPDX-License-Identifier: Apache-2.0
"""Tasks model and EDS repository. No GTK imports; all I/O runs on the caller's worker.

ICalGLib owns parsing and recurrence. Edits clone the latest VTODO and replace
only explicitly edited properties, retaining alarms, recurrence and extensions.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
import json
import re
import uuid


@dataclass(frozen=True)
class TaskList:
    uid: str
    name: str
    location: str = "On this computer"
    writable: bool = True
    default: bool = False
    sections: tuple[str, ...] = ()
    members: tuple[tuple[str, str], ...] = ()
    shared: bool = False
    commentable: bool = True


@dataclass(frozen=True)
class Comment:
    text: str
    author: str = ""
    created: str = ""


@dataclass(frozen=True)
class Task:
    uid: str
    source_uid: str
    title: str
    due: date | datetime | None = None
    completed: bool = False
    notes: str = ""
    priority: int = 0
    section: str = ""
    parent: str = ""
    waiting: str = ""
    assignee: str = ""
    assignee_name: str = ""
    repeat: str = ""
    order: str = ""
    completed_at: datetime | None = None
    raw: str = ""
    comments: tuple[Comment, ...] = ()
    recurrence_id: str = ""
    start: date | datetime | None = None

    @property
    def key(self): return self.source_uid, self.uid, self.recurrence_id

    @property
    def recurring(self): return bool(self.repeat or "\nRDATE" in self.raw)


def day(value):
    if isinstance(value, datetime): return value.astimezone().date()
    return value


def due_words(value, today=None):
    if value is None: return ""
    today = today or date.today()
    due = day(value)
    delta = (due - today).days
    label = {0: "Today", -1: "Yesterday", 1: "Tomorrow"}.get(delta)
    if label is None: label = due.strftime("%A" if 1 < delta < 7 else "%b %-d" if due.year == today.year else "%b %-d, %Y")
    if isinstance(value, datetime): label += value.astimezone().strftime(" %-I:%M %p")
    return label


def select_tasks(tasks, view, *, today=None, inbox="", me="", show_completed=False, search=""):
    today = today or date.today()
    result = []
    for task in tasks:
        if task.parent: continue
        if search.casefold() not in (task.title + " " + task.notes).casefold(): continue
        if view == "completed":
            if task.completed: result.append(task)
            continue
        if task.completed and not show_completed: continue
        due = day(task.due)
        if view == "today" and (due is None or due > today): continue
        if view == "upcoming" and due is None: continue
        if view == "inbox" and task.source_uid != inbox: continue
        if view == "mine" and (not me or task.assignee.casefold() != me.casefold()): continue
        if view not in ("today", "upcoming", "inbox", "mine") and task.source_uid != view: continue
        result.append(task)
    def order(task):
        try:
            number = Decimal(task.order or "0")
            return number if number.is_finite() else Decimal(0)
        except InvalidOperation: return Decimal(0)
    smart = view in ("today", "upcoming", "inbox", "mine", "completed")
    return tuple(sorted(result, key=lambda t: (t.completed, day(t.due) or date.max if smart else date.min, order(t), t.priority or 10, t.title.casefold(), t.uid)))


def group_tasks(tasks, view, *, today=None, lists=()):
    today = today or date.today()
    names = {source.uid: source.name for source in lists}
    groups = {}
    for task in tasks:
        due = day(task.due)
        if view in ("today", "upcoming"):
            name = "Overdue" if due and due < today else due_words(due, today) if due and due <= today + timedelta(days=7) else "Later"
        elif view == "mine": name = names.get(task.source_uid, "Tasks")
        else: name = task.section or "Tasks"
        groups.setdefault(name, []).append(task)
    source = next((source for source in lists if source.uid == view), None)
    ordered = (tuple(source.sections) if source else ()) + tuple(name for name in groups if not source or name not in source.sections)
    return tuple((name, tuple(groups.get(name, ()))) for name in ordered)


def _modules():
    from .eds_backend import _calendar_modules
    return _calendar_modules()


def properties(component):
    _, _, ical = _modules()
    prop = component.get_first_property(ical.PropertyKind.ANY_PROPERTY)
    result = []
    while prop is not None:
        result.append(prop)
        prop = component.get_next_property(ical.PropertyKind.ANY_PROPERTY)
    return result


def _name(prop): return prop.get_property_name().upper()


def value(component, name):
    return next(((p.get_x() if name.startswith("X-") else p.get_value_as_string()) or "" for p in properties(component) if _name(p) == name), "")


def _time(value_):
    if not value_ or value_.is_null_time() or not value_.is_valid_time(): return None
    if value_.is_date(): return date(value_.get_year(), value_.get_month(), value_.get_day())
    if value_.get_timezone() is None and not value_.is_utc():
        return datetime(value_.get_year(), value_.get_month(), value_.get_day(), value_.get_hour(), value_.get_minute(), value_.get_second()).astimezone()
    return datetime.fromtimestamp(value_.as_timet_with_zone(value_.get_timezone()), timezone.utc)


def read_task(component, source_uid):
    _, _, ical = _modules()
    parent = waiting = assignee = assignee_name = ""
    comments = []
    for prop in properties(component):
        if _name(prop) == "COMMENT":
            comments.append(Comment(prop.get_comment(), prop.get_parameter_as_string("X-LUMA-AUTHOR") or "", prop.get_parameter_as_string("X-LUMA-CREATED") or ""))
        if _name(prop) == "RELATED-TO":
            relation = prop.get_parameter_as_string("RELTYPE") or "PARENT"
            if relation == "DEPENDS-ON": waiting = prop.get_value_as_string()
            elif relation == "PARENT": parent = prop.get_value_as_string()
        if _name(prop) == "ATTENDEE" and not assignee:
            assignee = (prop.get_value_as_string() or "").removeprefix("mailto:")
            assignee_name = prop.get_parameter_as_string("CN") or assignee
    description = component.get_first_property(ical.PropertyKind.DESCRIPTION_PROPERTY)
    completed_prop = component.get_first_property(ical.PropertyKind.COMPLETED_PROPERTY)
    return Task(component.get_uid(), source_uid, component.get_summary() or "Untitled", _time(component.get_due()),
                value(component, "STATUS") == "COMPLETED" or value(component, "PERCENT-COMPLETE") == "100",
                description.get_description() if description else "", int(value(component, "PRIORITY") or 0),
                value(component, "X-LUMA-SECTION"), parent, waiting, assignee, assignee_name,
                value(component, "RRULE"), value(component, "X-APPLE-SORT-ORDER"),
                _time(completed_prop.get_completed()) if completed_prop else None, component.as_ical_string(), tuple(comments), value(component, "RECURRENCE-ID"), _time(component.get_dtstart()))


def task_from_ical(raw, source_uid):
    _, _, ical = _modules()
    return read_task(ical.Component.new_from_string(raw), source_uid)


def _escape(text):
    return text.replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def stamp(value_):
    if isinstance(value_, datetime): return value_.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return value_.strftime("%Y%m%d")


def replace_property(component, name, lines):
    _, _, ical = _modules()
    for prop in properties(component):
        if _name(prop) == name: component.remove_property(prop)
    for line in lines:
        prop = ical.Property.new_from_string(line)
        if prop is None: raise ValueError(f"Invalid {name.lower()}")
        component.add_property(prop)


def patch_task(component, changes):
    """Apply only named fields; unknown properties and nested VALARMs survive."""
    result = component.clone()
    for field, content in changes.items():
        if field in ("title", "notes", "section", "order"):
            name = {"title": "SUMMARY", "notes": "DESCRIPTION", "section": "X-LUMA-SECTION", "parent": "RELATED-TO", "order": "X-APPLE-SORT-ORDER"}[field]
            if field == "title" and not content.strip(): raise ValueError("Enter a task title.")
            replace_property(result, name, [f"{name}:{_escape(content)}"] if content else [])
        elif field == "repeat":
            if content:
                _, _, ical = _modules()
                parsed = ical.Recurrence.new_from_string(content)
                if parsed is None or parsed.get_freq() == ical.RecurrenceFrequency.NO_RECURRENCE: raise ValueError("Enter a valid recurrence rule.")
            replace_property(result, "RRULE", ["RRULE:" + content] if content else [])
            replace_property(result, "X-LUMA-REPEAT-ANCHOR", [])
            if not content:
                # Never means no future occurrences, including imported date
                # additions. Keep RECURRENCE-ID: it identifies an EDS exception,
                # and removing it here would change which object is edited.
                replace_property(result, "RDATE", [])
                replace_property(result, "EXDATE", [])
        elif field == "assignee":
            _, _, ical = _modules()
            # Preserve other attendees and their participation parameters.
            prior = read_task(component, "").assignee
            for prop in properties(result):
                if _name(prop) == "ATTENDEE" and (prop.get_value_as_string() or "").removeprefix("mailto:") == prior:
                    result.remove_property(prop)
            if content:
                if not re.fullmatch(r"[^\s@;:,]+@[^\s@;:,]+", content): raise ValueError("Enter an email address for the assignee.")
                result.add_property(ical.Property.new_from_string("ATTENDEE:mailto:" + content))
        elif field == "parent":
            _, _, ical = _modules()
            for prop in properties(result):
                if _name(prop) == "RELATED-TO" and (prop.get_parameter_as_string("RELTYPE") or "PARENT") == "PARENT": result.remove_property(prop)
            if content: result.add_property(ical.Property.new_from_string("RELATED-TO:" + _escape(content)))
        elif field == "waiting":
            _, _, ical = _modules()
            for prop in properties(result):
                if _name(prop) == "RELATED-TO" and prop.get_parameter_as_string("RELTYPE") == "DEPENDS-ON": result.remove_property(prop)
            if content: result.add_property(ical.Property.new_from_string("RELATED-TO;RELTYPE=DEPENDS-ON:" + _escape(content)))
        elif field in ("due", "start"):
            name = 'DUE' if field == 'due' else 'DTSTART'
            replace_property(result, name, [(name + ":" if isinstance(content, datetime) else name + ";VALUE=DATE:") + stamp(content)] if content else [])
        elif field == "priority":
            if content not in (0, 1, 5, 9): raise ValueError("Unknown priority")
            replace_property(result, "PRIORITY", [f"PRIORITY:{content}"])
        elif field == "completed":
            replace_property(result, "STATUS", ["STATUS:COMPLETED" if content else "STATUS:NEEDS-ACTION"])
            replace_property(result, "PERCENT-COMPLETE", ["PERCENT-COMPLETE:100" if content else "PERCENT-COMPLETE:0"])
            replace_property(result, "COMPLETED", ["COMPLETED:" + stamp(datetime.now(timezone.utc))] if content else [])
        else: raise ValueError(f"Unsupported task field: {field}")
    replace_property(result, "LAST-MODIFIED", ["LAST-MODIFIED:" + stamp(datetime.now(timezone.utc))])
    return result


def next_recurring_due(before):
    """Use ICalGLib to advance the retained anchor, additions and exclusions."""
    _, _, ical = _modules()
    due = before.get_due()
    if due is None: raise ValueError("Set a due date before completing a repeating task.")
    anchor = value(before, "X-LUMA-REPEAT-ANCHOR")
    start = ical.Time.new_from_string(anchor) if anchor else due.clone()
    if due.get_timezone(): start.set_timezone(due.get_timezone())
    excluded = {p.get_exdate().as_ical_string() for p in properties(before) if _name(p) == "EXDATE"}
    candidates = []
    for prop in properties(before):
        if _name(prop) == "RDATE":
            occurrence = prop.get_rdate().get_time()
            if occurrence and not occurrence.is_null_time() and occurrence.compare(due) > 0:
                candidates.append(occurrence)
        if _name(prop) == "RRULE":
            iterator = ical.RecurIterator.new(prop.get_rrule(), start)
            for _ in range(100000):
                occurrence = iterator.next()
                if not occurrence or occurrence.is_null_time(): break
                if occurrence.compare(due) > 0 and occurrence.as_ical_string() not in excluded:
                    candidates.append(occurrence); break
            else: raise ValueError("This repeat is too dense to advance safely.")
    candidates = [c for c in candidates if c.as_ical_string() not in excluded]
    return (min(candidates, key=lambda t: t.as_ical_string()) if candidates else None), start


@dataclass(frozen=True)
class Undo:
    source_uid: str
    before: str
    after: str | None
    created_uid: str = ""
    created_raw: str = ""
    related: tuple = ()


class TasksRepository:
    """Used by one serial worker. Views emit invalidations, never GTK work."""
    def __init__(self, changed=lambda: None):
        self.changed = changed
        self.registry = None
        self.clients = {}
        self.views = {}
        self.sources = ()
        self.me = ""
        self.metadata = {}
        self.errors = ()
        self.cache = {}
        self.disabled = os.environ.get("PRAIRIE_EDS_MODE") == "disabled"

    def load(self):
        if self.disabled: return (), ()
        data, cal, _ = _modules()
        if self.registry is None:
            self.registry = data.SourceRegistry.new_sync(None)
            for signal in ("source-added", "source-removed", "source-changed"):
                self.registry.connect(signal, lambda *_: self.changed())
        default = self.registry.ref_default_task_list()
        identity = self.registry.ref_default_mail_identity()
        if identity:
            self.me = identity.get_extension(data.SOURCE_EXTENSION_MAIL_IDENTITY).get_address() or ""
        sources, tasks = [], []
        enabled = self.registry.list_enabled(data.SOURCE_EXTENSION_TASK_LIST)
        live = {source.get_uid() for source in enabled}
        for uid in tuple(self.views):
            if uid not in live:
                self.views.pop(uid).stop(); self.clients.pop(uid, None)
        errors = []
        for source in enabled:
            uid = source.get_uid()
            parent = self.registry.ref_source(source.get_parent()) if source.get_parent() else None
            backend = source.get_extension(data.SOURCE_EXTENSION_TASK_LIST).get_backend_name()
            location = "On this computer" if backend == "local" else (parent.get_display_name() if parent else source.get_display_name())
            try:
                if uid not in self.clients:
                    # EDS's uint32(-1) opens its local cache immediately. Zero
                    # means wait indefinitely, and five seconds stalls each source.
                    self.clients[uid] = cal.Client.connect_sync(source, cal.ClientSourceType.TASKS, 2**32 - 1, None)
                client = self.clients[uid]
                sources.append(TaskList(uid, source.get_display_name(), location, not client.is_readonly(), bool(default and default.get_uid() == uid)))
                if uid not in self.views:
                    ok, view = client.get_view_sync("#t", None)
                    if not ok: raise RuntimeError("Could not watch task changes")
                    for signal in ("objects-added", "objects-modified", "objects-removed"):
                        view.connect(signal, lambda *_: self.changed())
                    self.views[uid] = view
                    view.start()
                ok, components = client.get_object_list_sync("#t", None)
                if not ok: raise RuntimeError("Could not read tasks")
                metadata = next((c for c in components if value(c, "X-LUMA-KIND") == "PROJECT"), None)
                self.metadata[uid] = metadata
                records = [read_task(component, uid) for component in components if value(component, "X-LUMA-KIND") != "PROJECT"]
                sections = tuple(json.loads(value(metadata, "X-LUMA-SECTIONS") or "[]")) if metadata else ()
                members = tuple(tuple(m) for m in json.loads(value(metadata, "X-LUMA-MEMBERS") or "[]")) if metadata else ()
                sections = tuple(dict.fromkeys(sections + tuple(t.section for t in records if t.section)))
                members = tuple(dict.fromkeys(members + tuple((t.assignee, t.assignee_name) for t in records if t.assignee)))
                previous = sources[-1]
                sources[-1] = TaskList(previous.uid, previous.name, previous.location, previous.writable, previous.default, sections, members,
                                      bool(sections or members or (metadata and value(metadata, "X-LUMA-SHARED") == "TRUE")))
                tasks.extend(records)
                self.cache[uid] = tuple(records)
            except Exception as error:
                if not any(s.uid == uid for s in sources): sources.append(TaskList(uid, source.get_display_name(), location, False, bool(default and default.get_uid() == uid)))
                tasks.extend(self.cache.get(uid, ()))
                errors.append(f"{source.get_display_name()}: {error}")
        self.sources = tuple(sources)
        self.errors = tuple(errors)
        return self.sources, tuple(tasks)

    def _get(self, source_uid, uid, recurrence_id=""):
        ok, component = self.clients[source_uid].get_object_sync(uid, recurrence_id or None, None)
        if not ok or component is None: raise RuntimeError("The task is no longer available.")
        return component

    def _backup(self, source_uid):
        from .tasks_local import backup_source, data_root
        import hashlib
        digest = hashlib.sha256(source_uid.encode()).hexdigest()
        if (data_root() / "backups" / (digest + ".json")).exists(): return
        ok, components = self.clients[source_uid].get_object_list_sync("#t", None)
        if not ok: raise RuntimeError("Could not back up this task list. Nothing was changed.")
        backup_source(source_uid, (c.as_ical_string() for c in components))

    def _write(self, source_uid, component):
        self._backup(source_uid)
        _, cal, _ = _modules()
        if not self.clients[source_uid].modify_object_sync(component, cal.ObjModType.THIS, cal.OperationFlags.CONFLICT_FAIL, None):
            raise RuntimeError("The task could not be saved.")

    def _create(self, source_uid, component):
        self._backup(source_uid)
        _, cal, _ = _modules()
        ok, uid = self.clients[source_uid].create_object_sync(component, cal.OperationFlags.CONFLICT_FAIL, None)
        if not ok or not uid: raise RuntimeError("The task could not be created.")
        return uid

    def _delete(self, source_uid, uid, recurrence_id=""):
        self._backup(source_uid)
        _, cal, _ = _modules()
        if not self.clients[source_uid].remove_object_sync(uid, recurrence_id or None, cal.ObjModType.THIS, cal.OperationFlags.CONFLICT_FAIL, None):
            raise RuntimeError("The task could not be deleted.")

    def add(self, source_uid, changes):
        _, _, ical = _modules()
        component = ical.Component.new_from_string("BEGIN:VTODO\r\nUID:" + str(uuid.uuid4()) + "\r\nDTSTAMP:" + stamp(datetime.now(timezone.utc)) + "\r\nEND:VTODO\r\n")
        return self._create(source_uid, patch_task(component, {"completed": False, **changes}))

    def edit(self, task, changes):
        before = self._get(*task.key)
        # Reject edits to the same fields if another client changed them since display.
        current = read_task(before, task.source_uid)
        for field in changes:
            if getattr(current, field) != getattr(task, field):
                raise RuntimeError("This task changed elsewhere. Reload it before saving.")
        after = patch_task(before, changes)
        self._write(task.source_uid, after)
        return Undo(task.source_uid, before.as_ical_string(), self._get(*task.key).as_ical_string())

    def _children(self, task):
        client = self.clients.get(task.source_uid)
        if client is None: return ()
        ok, components = client.get_object_list_sync("#t", None)
        if not ok: raise RuntimeError("Could not read the checklist. Nothing was changed.")
        descendants, parents = [], {task.uid}
        remaining = [c for c in components if c.get_uid() != task.uid]
        while True:
            children = [c for c in remaining if read_task(c, task.source_uid).parent in parents]
            if not children: break
            descendants.extend(children)
            parents = {c.get_uid() for c in children}
            remaining = [c for c in remaining if c.get_uid() not in parents]
        return tuple(descendants)

    def delete(self, task):
        before = self._get(*task.key)
        if before.as_ical_string() != task.raw: raise RuntimeError("This task changed elsewhere. Reload it before deleting.")
        removed = []
        children = self._children(task) if not task.recurrence_id else ()
        if not task.recurrence_id and task.source_uid in self.clients:
            ok, components = self.clients[task.source_uid].get_object_list_sync("#t", None)
            if not ok: raise RuntimeError("Could not read recurrence exceptions. Nothing was changed.")
            children += tuple(c for c in components if c.get_uid() == task.uid and value(c, "RECURRENCE-ID"))
        try:
            for component in reversed(children):
                self._delete(task.source_uid, component.get_uid(), value(component, "RECURRENCE-ID"))
                removed.append(component)
            self._delete(*task.key)
        except Exception:
            for component in reversed(removed):
                if value(component, "RECURRENCE-ID"): self._write(task.source_uid, component)
                else: self._create(task.source_uid, component)
            raise
        return Undo(task.source_uid, before.as_ical_string(), None, related=tuple(Undo(task.source_uid, c.as_ical_string(), None) for c in children))

    def import_components(self, destination, components):
        if not any(s.uid == destination and s.writable for s in self.sources):
            raise ValueError('Choose a writable destination list.')
        components = tuple(components)
        ok, existing = self.clients[destination].get_object_list_sync('#t', None)
        if not ok:
            raise RuntimeError('Could not read the destination. Nothing was moved.')
        identifiers = {c.get_uid() for c in existing or ()}
        if any(c.get_uid() in identifiers for c in components):
            raise ValueError('The destination already contains this task identity. Nothing was moved.')
        _, _, ical = _modules()
        def durable(component):
            props = tuple(sorted(p.as_ical_string() for p in properties(component) if _name(p) not in ('DTSTAMP', 'CREATED', 'LAST-MODIFIED', 'SEQUENCE')))
            children = []
            child = component.get_first_component(ical.ComponentKind.ANY_COMPONENT)
            while child is not None:
                children.append(durable(child)); child = component.get_next_component(ical.ComponentKind.ANY_COMPONENT)
            return int(component.isa()), props, tuple(sorted(children))
        created = []
        try:
            for original in sorted(components, key=lambda c: bool(value(c, 'RECURRENCE-ID'))):
                component = patch_task(original, {'section': ''})
                rid = value(component, 'RECURRENCE-ID')
                if rid:
                    self._write(destination, component)
                else:
                    uid = self._create(destination, component); created.append(uid)
                    if uid != component.get_uid():
                        raise RuntimeError('The destination changed the task identity. The original was kept.')
                if durable(component) != durable(self._get(destination, component.get_uid(), rid)):
                    raise RuntimeError('The destination did not preserve this task. The original was kept.')
        except Exception:
            for uid in reversed(created):
                self._delete(destination, uid)
            raise
        return tuple(created)

    def move(self, task, destination):
        if destination == task.source_uid:
            raise ValueError('Choose a different list.')
        before = self._get(*task.key)
        if before.as_ical_string() != task.raw:
            raise RuntimeError('This task changed. Reload before moving it.')
        if task.recurrence_id:
            raise ValueError('Move the complete recurring task rather than a detached occurrence.')
        components = (before,) + self._children(task)
        ok, others = self.clients[task.source_uid].get_object_list_sync('#t', None)
        if not ok:
            raise RuntimeError('Could not read recurrence exceptions. Nothing was moved.')
        components += tuple(c for c in others if c.get_uid() == task.uid and value(c, 'RECURRENCE-ID'))
        created = self.import_components(destination, components)
        try:
            self.delete(task)
        except Exception:
            for uid in reversed(created):
                self._delete(destination, uid)
            raise
        return None

    def complete(self, task, completed):
        if not task.recurring or not completed: return self.edit(task, {"completed": completed}), "Completed" if completed else "Reopened"
        _, _, ical = _modules()
        before = self._get(*task.key)
        if before.as_ical_string() != task.raw: raise RuntimeError("This task changed elsewhere. Reload it before completing.")
        if value(before, "RECURRENCE-ID"):
            return self.edit(task, {"completed": True}), "Completed occurrence"
        next_due, start = next_recurring_due(before)
        if next_due is None: return self.edit(task, {"completed": True}), "Completed · repeat finished"
        copy = patch_task(before, {"completed": True})
        copy.set_uid(str(uuid.uuid4()))
        for name in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID", "X-LUMA-REPEAT-ANCHOR"):
            replace_property(copy, name, [])
        successor = {"due": _time(next_due), "completed": False}
        if isinstance(task.start, datetime) and isinstance(task.due, datetime):
            successor['start'] = _time(next_due).astimezone() - (task.due.astimezone() - task.start.astimezone())
        after = patch_task(before, successor)
        replace_property(after, "X-LUMA-REPEAT-ANCHOR", ["X-LUMA-REPEAT-ANCHOR:" + start.as_ical_string()])
        # Keep local recurrence wall time and its timezone across DST.
        after.set_due(next_due)
        original_due = before.get_first_property(ical.PropertyKind.DUE_PROPERTY)
        tzid = original_due.get_parameter_as_string("TZID")
        if tzid: after.get_first_property(ical.PropertyKind.DUE_PROPERTY).set_parameter_from_string("TZID", tzid)
        children = self._children(task)
        copied_uid = self._create(task.source_uid, copy)
        related = []
        changed_children = []
        copied_children = []
        identifiers = {task.uid: copied_uid}
        try:
            for child in children:
                archived = child.clone()
                archived.set_uid(str(uuid.uuid4()))
                identifiers[child.get_uid()] = archived.get_uid()
                parent = read_task(child, task.source_uid).parent
                archived = patch_task(archived, {"parent": identifiers[parent]})
                child_uid = self._create(task.source_uid, archived)
                copied_children.append(child_uid)
                child_next = patch_task(child, {"completed": False})
                self._write(task.source_uid, child_next)
                changed_children.append(child)
                related.append(Undo(task.source_uid, child.as_ical_string(), self._get(task.source_uid, child.get_uid()).as_ical_string(), child_uid, self._get(task.source_uid, child_uid).as_ical_string()))
            self._write(task.source_uid, after)
        except Exception:
            for child in reversed(changed_children): self._write(task.source_uid, child)
            for child_uid in reversed(copied_children): self._delete(task.source_uid, child_uid)
            self._delete(task.source_uid, copied_uid)
            raise
        return Undo(task.source_uid, before.as_ical_string(), self._get(*task.key).as_ical_string(), copied_uid,
                    self._get(task.source_uid, copied_uid).as_ical_string(), tuple(related)), "Done. The next one is " + due_words(_time(next_due)) + "."

    def undo(self, action):
        _, _, ical = _modules()
        actions = (action,) + action.related
        # Check every existing record before modifying any of them.
        for item in actions:
            before = ical.Component.new_from_string(item.before)
            if item.after is not None:
                current = self._get(item.source_uid, before.get_uid(), value(before, "RECURRENCE-ID"))
                if current.as_ical_string() != item.after: raise RuntimeError("This task changed since that action. Undo would overwrite newer work.")
            if item.created_uid:
                copy = self._get(item.source_uid, item.created_uid)
                if copy.as_ical_string() != item.created_raw: raise RuntimeError("The completed copy changed. Undo would overwrite newer work.")
        restored, removed = [], []
        try:
            for item in actions:
                before = ical.Component.new_from_string(item.before)
                if item.after is None and not value(before, "RECURRENCE-ID"): self._create(item.source_uid, before)
                else: self._write(item.source_uid, before)
                restored.append(item)
            for item in reversed(actions):
                if item.created_uid:
                    self._delete(item.source_uid, item.created_uid)
                    removed.append(item)
        except Exception:
            for item in reversed(removed): self._create(item.source_uid, ical.Component.new_from_string(item.created_raw))
            for item in reversed(restored):
                if item.after is None: self._delete(item.source_uid, ical.Component.new_from_string(item.before).get_uid(), value(ical.Component.new_from_string(item.before), "RECURRENCE-ID"))
                else: self._write(item.source_uid, ical.Component.new_from_string(item.after))
            raise

    def comments_are_local(self, source_uid):
        # Google Tasks has no comment field: its EDS adapter discards COMMENT.
        data, _, _ = _modules()
        source = self.registry.ref_source(source_uid)
        return bool(source and source.get_extension(data.SOURCE_EXTENSION_TASK_LIST).get_backend_name() == "gtasks")

    def comment(self, task, text, author):
        _, _, ical = _modules()
        if not text.strip(): raise ValueError("Write a comment first.")
        before = self._get(*task.key)
        after = before.clone()
        prop = ical.Property.new_from_string("COMMENT:" + _escape(text.strip()))
        prop.set_parameter_from_string("X-LUMA-AUTHOR", author or "Me")
        prop.set_parameter_from_string("X-LUMA-CREATED", datetime.now(timezone.utc).isoformat())
        after.add_property(prop)
        self._write(task.source_uid, after)
        return Undo(task.source_uid, before.as_ical_string(), self._get(*task.key).as_ical_string())

    def configure(self, source_uid, sections, members, shared):
        """Sync project metadata as a marked VTODO in the same CalDAV collection.

        ESource custom keys are local only. A marked component keeps ordered
        sections and explicit members with the project across devices.
        """
        _, _, ical = _modules()
        sections = tuple(dict.fromkeys(s.strip() for s in sections if s.strip()))
        for email, name in members:
            if not re.fullmatch(r"[^\s@;:,]+@[^\s@;:,]+", email): raise ValueError("Each member needs an email address.")
        existing = self.metadata.get(source_uid)
        component = self._get(source_uid, existing.get_uid()) if existing else ical.Component.new_from_string(
            "BEGIN:VTODO\r\nUID:luma-project-settings\r\nSUMMARY:Project settings\r\nSTATUS:COMPLETED\r\nX-LUMA-KIND:PROJECT\r\nEND:VTODO\r\n")
        for name, content in (("X-LUMA-SECTIONS", json.dumps(sections)), ("X-LUMA-MEMBERS", json.dumps(members)), ("X-LUMA-SHARED", "TRUE" if shared else "FALSE")):
            replace_property(component, name, [name + ":" + _escape(content)])
        if existing: self._write(source_uid, component)
        else: self._create(source_uid, component)

    def create_list(self, name):
        data, _, _ = _modules()
        if not name.strip(): raise ValueError("Enter a list name.")
        source = data.Source.new(None, None)
        source.set_display_name(name.strip())
        source.set_parent("local-stub")
        source.get_extension(data.SOURCE_EXTENSION_TASK_LIST).set_backend_name("local")
        if not self.registry.commit_source_sync(source, None): raise RuntimeError("The list could not be created.")
        return source.get_uid()

    def update_list(self, source_uid, *, name=None, color=None):
        data, _, _ = _modules()
        source = self.registry.ref_source(source_uid)
        if source is None:
            raise ValueError("That list is no longer available.")
        if name is not None:
            if not name.strip(): raise ValueError("Enter a list name.")
            source.set_display_name(name.strip())
        if color is not None:
            source.get_extension(data.SOURCE_EXTENSION_TASK_LIST).set_color(color)
        source.write_sync(None)

    def delete_list(self, source_uid, destination=None):
        """Archive before removal; copy and verify every task before moving.

        A failed destination write leaves the original list intact and removes
        only objects this operation created. Never remove an account container.
        """
        from .tasks_local import atomic_json, data_root
        source = self.registry.ref_source(source_uid)
        if source is None or not source.get_removable():
            raise ValueError('This provider does not allow removing this list.')
        if source_uid not in self.clients:
            raise ValueError('Reload this list before deleting it.')
        if destination == source_uid:
            raise ValueError('Choose a different list.')
        if destination and not any(s.uid == destination and s.writable for s in self.sources):
            raise ValueError('Choose a writable destination list.')
        ok, components = self.clients[source_uid].get_object_list_sync('#t', None)
        if not ok:
            raise RuntimeError('Could not read this list. Nothing was deleted.')
        components = tuple(components or ())
        archive = data_root() / 'deleted-lists' / (str(uuid.uuid4()) + '.json')
        source_data, _length = source.to_string()
        atomic_json(archive, {'source': source_uid, 'name': source.get_display_name(),
                             'source_data': source_data,
                             'components': [c.as_ical_string() for c in components]})
        _, cal, ical = _modules()
        def durable(component):
            props = tuple(sorted(p.as_ical_string() for p in properties(component)
                                 if _name(p) not in ('DTSTAMP', 'CREATED', 'LAST-MODIFIED', 'SEQUENCE')))
            children = []
            child = component.get_first_component(ical.ComponentKind.ANY_COMPONENT)
            while child is not None:
                children.append(durable(child))
                child = component.get_next_component(ical.ComponentKind.ANY_COMPONENT)
            return int(component.isa()), props, tuple(sorted(children))
        snapshot = tuple(sorted(durable(c) for c in components))
        moved = tuple(c for c in components if value(c, 'X-LUMA-KIND') != 'PROJECT')
        created = []
        try:
            if destination:
                ok, existing = self.clients[destination].get_object_list_sync('#t', None)
                if not ok:
                    raise RuntimeError('Could not read the destination. Nothing was moved.')
                identifiers = {c.get_uid() for c in existing or ()}
                if any(c.get_uid() in identifiers for c in moved):
                    raise ValueError('The destination already has one of these tasks. Nothing was moved.')
                # Create masters before their recurrence exceptions. Project
                # metadata belongs to its list and stays in the recovery archive.
                for component in sorted(moved, key=lambda c: bool(value(c, 'RECURRENCE-ID'))):
                    rid = value(component, 'RECURRENCE-ID')
                    if rid:
                        self._write(destination, component)
                    else:
                        uid = self._create(destination, component)
                        created.append(uid)
                        if uid != component.get_uid():
                            raise RuntimeError('The destination changed a task identifier. The original list was kept.')
                    copied = self._get(destination, component.get_uid(), rid)
                    if durable(component) != durable(copied):
                        raise RuntimeError('The destination did not preserve this task. The original list was kept.')
            ok, current = self.clients[source_uid].get_object_list_sync('#t', None)
            if not ok or tuple(sorted(durable(c) for c in current or ())) != snapshot:
                raise RuntimeError('This list changed while moving. The original list was kept. Try again.')
            if not source.remove_sync(None):
                raise RuntimeError('The list could not be deleted.')
        except Exception:
            cleanup_errors = []
            for uid in reversed(created):
                try:
                    if not self.clients[destination].remove_object_sync(uid, None, cal.ObjModType.ALL,
                                                                       cal.OperationFlags.CONFLICT_FAIL, None):
                        raise RuntimeError('Could not remove the copied series.')
                except Exception as error: cleanup_errors.append(str(error))
            if cleanup_errors:
                raise RuntimeError('The original list was kept, but a copied task could not be removed. Check the destination list.')
            raise
        return str(archive)

    def close(self):
        for view in self.views.values(): view.stop()
        self.views.clear()
