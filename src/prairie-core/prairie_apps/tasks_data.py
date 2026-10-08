# SPDX-License-Identifier: Apache-2.0
"""Tasks view data and safe existing-store adapters, without GTK.

Fixture changes are confined to an in-memory copy. Real changes use the
existing EDS repository and its conflict checks, preserving untouched fields.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import colorsys
import os
import re
import uuid

from .tasks_backend import day
from .tasks_parse import parse_clock
from .tasks_local import LocalTasks
from .tasks_fixture import FixtureRepository, source_from_environment

VIEWS = (("today", "Today", "sun"), ("upcoming", "Upcoming", "calendar"),
         ("mine", "Assigned to me", "user"), ("flag", "Flagged", "flag"),
         ("done", "Done", "check-check"))
PRIORITIES = ("None", "Low", "Medium", "High")


def in_view(task, view):
    due = task.get("due")
    if view == "today": return not task["done"] and due is not None and due <= 0
    if view == "upcoming": return not task["done"] and due is not None and due > 0
    if view == "mine": return not task["done"] and "me" in task["who"]
    if view == "flag": return not task["done"] and task.get("flag", False)
    if view == "done": return task["done"]
    return task["l"] == view


def visible_tasks(tasks, view, query=""):
    query = query.strip().casefold()
    return [t for t in tasks if in_view(t, view) and (view == "done" or not t["done"])
            and (not query or query in t["t"].casefold())]


def day_label(offset, today):
    if offset is None: return ""
    if offset < 0: return "Yesterday" if offset == -1 else f"{-offset} days ago"
    if offset in (0, 1): return ("Today", "Tomorrow")[offset]
    moment = today + timedelta(days=offset)
    return moment.strftime("%A" if offset < 7 else "%a, %b %-d")



def comment_time(value, *, now=None):
    """Format the existing Hub millisecond or local ISO timestamp for display only."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        if re.fullmatch(r"-?[0-9]{1,15}", raw):
            moment = datetime.fromtimestamp(int(raw) / 1000).astimezone()
        else:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
    except (ValueError, OverflowError, OSError):
        return ""
    today = (now or datetime.now().astimezone()).date()
    return day_label((moment.date() - today).days, today) + " · " + moment.strftime("%-I:%M %p")


def task_groups(tasks, lists, view, query="", today=None):
    shown = visible_tasks(tasks, view, query)
    if view == "today":
        return [("Overdue", [t for t in shown if t["due"] < 0]),
                ("Today", [t for t in shown if t["due"] == 0])]
    if view == "upcoming":
        return [(day_label(d, today or date.today()), [t for t in shown if t["due"] == d])
                for d in sorted({t["due"] for t in shown})]
    source = next((s for s in lists if s["id"] == view), None)
    if source and source.get("secs"):
        return [(name, [t for t in shown if t.get("sec", 0) == i])
                for i, name in enumerate(source["secs"])]
    return [("", shown)]


def suggested_tasks(tasks):
    return [t for t in tasks if not t["done"] and t.get("due") is not None
            and 0 < t["due"] <= 3][:3]


def quick_chips(draft, parsed, today, lists, people):
    """v70 quick-entry chips describe explicit tokens, not view defaults."""
    if not draft.strip(): return []
    chips = []
    explicit_date = re.search(r"\b(today|tonight|tomorrow|tmr|next\s+week|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", draft, re.I)
    if parsed.due is not None and (explicit_date or isinstance(parsed.due, datetime)):
        label = day_label((day(parsed.due) - today).days, today) if explicit_date else ""
        if isinstance(parsed.due, datetime):
            label += (", " if label else "") + ((parsed.start.strftime("%-I:%M %p") + "–") if parsed.start else "") + parsed.due.strftime("%-I:%M %p")
        chips.append(("calendar", label))
    if parsed.priority: chips.append(("flag", {1: "High", 5: "Medium", 9: "Low"}[parsed.priority]))
    source = next((s for s in lists if s["id"] == parsed.source_uid), None)
    if source and source["n"] in parsed.chips: chips.append(("list", source["n"]))
    person = people.get(parsed.assignee)
    if person and person["n"] in parsed.chips:
        chips.append(("user", "You" if parsed.assignee == "me" else person["n"].split()[0]))
    return chips


class TasksData:
    def __init__(self, changed=None):
        self.repository = source_from_environment(changed)
        self.fixture = isinstance(self.repository, FixtureRepository)
        self.today = self.repository.today if self.fixture else date.today()
        self.data = deepcopy(self.repository.data) if self.fixture else {
            "today": self.today.isoformat(), "lists": [], "tasks": [], "people": {}}
        self.local = None if self.fixture else LocalTasks()
        self.records = {}
        self.errors = ()

    def load(self):
        if self.fixture: return deepcopy(self.data)
        sources, records = self.repository.load()
        local = self.local.read()["tasks"]
        self.records = {}
        people = {}
        lists = [{"id": s.uid, "n": s.name, "ppl": [key for key, _ in s.members],
                  "secs": list(s.sections), "writable": s.writable, "commentable": s.commentable, "default": s.default,
                  "h": self._source_hue(s.uid)}
                 for s in sources]
        for source in sources:
            for key, name in source.members: people[key] = {"n": name or key}
        tasks = []
        for record in records:
            if record.parent: continue
            key = "|".join(record.key)
            self.records[key] = record
            due = (day(record.due) - self.today).days if record.due else None
            clock = record.due.astimezone().strftime("%-I:%M %p") if isinstance(record.due, datetime) else ""
            who = ["me" if record.assignee.casefold() == self.repository.me.casefold()
                   else record.assignee] if record.assignee else []
            if who: people[who[0]] = {"n": "You" if who[0] == "me" else record.assignee_name or record.assignee}
            source = next(s for s in sources if s.uid == record.source_uid)
            children = [t for t in records if t.source_uid == record.source_uid and t.parent == record.uid]
            tasks.append({"id": key, "t": record.title, "l": record.source_uid,
                          "done": record.completed, "pri": 0 if not record.priority else
                          3 if record.priority <= 4 else 2 if record.priority <= 6 else 1,
                          "due": due, "time": clock, "repeat": record.repeat, "recurring": record.recurring, "notes": record.notes, "who": who,
                          "start_time": record.start.astimezone().strftime("%-I:%M %p") if isinstance(record.start, datetime) else "",
                          "start_day": (day(record.start) - self.today).days if record.start else None,
                          "sec": source.sections.index(record.section) if record.section in source.sections else 0,
                          "subs": [[t.title, t.completed] for t in children],
                          "step_keys": [t.key for t in children],
                          "act": [["me" if not c.author or c.author.casefold() in {self.repository.me.casefold(), os.environ.get("USER", "").casefold()} else c.author,
                                   "c", c.text, comment_time(c.created, now=datetime.combine(self.today, datetime.min.time()))] for c in record.comments]})
            fields = local.get(key, {})
            tasks[-1]["comments_local"] = self.repository.comments_are_local(record.source_uid)
            tasks[-1]["act"].extend([["me" if not c["author"] or c["author"].casefold() in
                                     {self.repository.me.casefold(), os.environ.get("USER", "").casefold()}
                                     else c["author"], "c", c["text"], comment_time(c["created"], now=datetime.combine(self.today, datetime.min.time()))]
                                    for c in fields.get("comments", [])])
            tasks[-1]["flag"] = bool(fields.get("flag", False))
            if "who" in fields and not record.source_uid.startswith("luma-shared:"):
                tasks[-1]["who"] = list(fields["who"])
                for person in fields["who"]:
                    people.setdefault(person, {"n": "You" if person == "me" else person})
            for child in children: self.records["|".join(child.key)] = child
        self.errors = self.repository.errors
        self.data = {"today": self.today.isoformat(), "lists": lists, "tasks": tasks, "people": people}
        return deepcopy(self.data)

    def _source_hue(self, uid):
        if uid.startswith('luma-shared:'):
            value = self.repository.source_color(uid)
        else:
            if self.repository.registry is None:
                return 210
            source = self.repository.registry.ref_source(uid)
            if source is None: return 210
            from .tasks_backend import _modules
            data, _, _ = _modules()
            value = source.get_extension(data.SOURCE_EXTENSION_TASK_LIST).get_color() or ''
        if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
            return 210
        try:
            red, green, blue = (int(value[pos:pos + 2], 16) / 255 for pos in (1, 3, 5))
        except ValueError:
            return 210
        return round(colorsys.rgb_to_hsv(red, green, blue)[0] * 360)

    def create_list(self, name):
        if self.fixture:
            uid = "list-" + str(uuid.uuid4())
            self.data["lists"].append({"id": uid, "n": name.strip(), "h": 210, "ppl": [], "secs": []})
            return uid
        return self.repository.create_list(name)

    def update_list(self, uid, *, name=None, hue=None):
        if self.fixture:
            source = next(s for s in self.data["lists"] if s["id"] == uid)
            before = deepcopy(source)
            if name is not None: source["n"] = name.strip()
            if hue is not None: source["h"] = hue
            return lambda: source.update(before)
        color = None
        if hue is not None:
            rgb = colorsys.hsv_to_rgb(hue / 360, 0.72, 0.85)
            color = "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)
        self.repository.update_list(uid, name=name, color=color)

    def delete_list(self, uid, destination=None):
        if self.fixture:
            source = next(s for s in self.data['lists'] if s['id'] == uid)
            if destination == uid or destination and not any(s['id'] == destination for s in self.data['lists']):
                raise ValueError('Choose a different list.')
            self.data['lists'].remove(source)
            if destination:
                for task in self.data['tasks']:
                    if task['l'] == uid: task['l'] = destination; task['sec'] = 0
            else:
                self.data['tasks'] = [t for t in self.data['tasks'] if t['l'] != uid]
            return
        if not destination:
            return self.repository.delete_list(uid)
        # Keep local flags/comments with the moved task. Publish their private
        # metadata before remote removal, so a successful move cannot lose it.
        from .tasks_local import atomic_json
        with self.local.locked():
            before = self.local.read()
            after = deepcopy(before)
            for key, fields in before['tasks'].items():
                if not key.startswith(uid + '|'): continue
                new_key = destination + key[len(uid):]
                if new_key in after['tasks']:
                    raise ValueError('The destination already has local data for this task. Nothing was moved.')
                after['tasks'][new_key] = deepcopy(fields)
            atomic_json(self.local.path, after)
            try:
                return self.repository.delete_list(uid, destination)
            except Exception:
                atomic_json(self.local.path, before)
                raise

    def move(self, task_id, destination):
        if self.fixture:
            task = next(t for t in self.data['tasks'] if t['id'] == task_id)
            task['l'], task['sec'] = destination, 0
            return None
        from .tasks_local import atomic_json
        task = self.records[task_id]
        selected = {task.uid}
        while True:
            children = {record.uid for record in self.records.values()
                        if record.source_uid == task.source_uid and record.parent in selected}
            if children <= selected: break
            selected.update(children)
        selected_keys = {key for key, record in self.records.items()
                         if record.source_uid == task.source_uid and record.uid in selected}
        with self.local.locked():
            before = self.local.read(); after = deepcopy(before)
            for key, fields in before['tasks'].items():
                if not key.startswith(task.source_uid + '|'):
                    continue
                if key not in selected_keys:
                    continue
                target_key = destination + key[len(task.source_uid):]
                if target_key in after['tasks']:
                    raise ValueError('The destination already has local task data. Nothing was moved.')
                after['tasks'][target_key] = deepcopy(fields)
            atomic_json(self.local.path, after)
            try:
                return self.repository.move(task, destination)
            except Exception:
                atomic_json(self.local.path, before)
                raise

    def edit(self, task_id, changes):
        changes = dict(changes)
        if 'time' in changes:
            clock = parse_clock(changes['time']) if changes['time'].strip() else None
        if 'start_time' in changes:
            start_clock = parse_clock(changes['start_time']) if changes['start_time'].strip() else None
        if self.fixture:
            task = next(t for t in self.data["tasks"] if t["id"] == task_id)
            before = deepcopy(task)
            task.update(deepcopy(changes))
            if 'repeat' in changes: task['recurring'] = bool(changes['repeat'])
            if 'time' in changes:
                task['time'] = clock.strftime('%-I:%M %p') if clock else ''
                if clock and task.get('due') is None: task['due'] = 0
            if 'start_time' in changes:
                task['start_time'] = start_clock.strftime('%-I:%M %p') if start_clock else ''
                task['start_day'] = task.get('due') if start_clock else None
            return lambda: task.update(before)
        if set(changes) <= {"flag", "who"} and ("who" not in changes or not self.records[task_id].source_uid.startswith("luma-shared:")):
            return self.local.edit(task_id, changes)
        unsupported = set(changes) - {"t", "due", "time", "start_time", "repeat", "pri", "done", "who", "notes"}
        if unsupported:
            raise ValueError("The task store does not support editing " + ", ".join(sorted(unsupported)) + ".")
        if "done" in changes and len(changes) != 1:
            raise ValueError("Save task fields before changing completion.")
        if "who" in changes and len(changes["who"]) > 1:
            raise ValueError("This task store supports one assignee.")
        record = self.records[task_id]
        converted = {}
        for field, value in changes.items():
            if field == "t": converted["title"] = value
            elif field == "due":
                target = self.today + timedelta(days=value) if value is not None else None
                if target is not None and isinstance(record.due, datetime):
                    local = record.due.astimezone()
                    # The date picker edits the day; keep the clock time shown.
                    target = local.replace(tzinfo=None, year=target.year, month=target.month, day=target.day).astimezone()
                converted["due"] = target
            elif field == "pri": converted["priority"] = (0, 9, 5, 1)[value]
            elif field in ("time", "start_time"):
                pass  # Apply once after the day edit, regardless of field order.
            elif field == "done":
                undo, _ = self.repository.complete(record, value)
                return lambda: self.repository.undo(undo)
            elif field == "who":
                if len(value) > 1: raise ValueError("This task store supports one assignee.")
                if value == ["me"] and record.source_uid.startswith('luma-shared:'):
                    converted['assignee'] = self.repository.shared[record.source_uid]['account']
                else:
                    converted["assignee"] = self.repository.me if value == ["me"] else next(iter(value), "")
            elif field == "notes": converted["notes"] = value
            elif field == "repeat": converted["repeat"] = value
            else: raise ValueError(f"The task store does not support editing {field}.")
        if 'time' in changes:
            target_day = day(converted.get('due', record.due))
            converted['due'] = datetime.combine(target_day or self.today, clock).astimezone() if clock else target_day
        if 'start_time' in changes or 'due' in changes and record.start:
            anchor = day(converted.get('due', record.due)) or self.today
            if record.start and record.due:
                anchor -= day(record.due) - day(record.start)
            start_clock = start_clock if 'start_time' in changes else record.start.astimezone().time()
            converted['start'] = datetime.combine(anchor, start_clock).astimezone() if start_clock else None
        start = converted.get('start', record.start)
        end = converted.get('due', record.due)
        if isinstance(start, datetime) and isinstance(end, datetime) and start >= end:
            raise ValueError('The end time must be after the start time.')
        undo = self.repository.edit(record, converted)
        return lambda: self.repository.undo(undo)

    def add(self, parsed):
        if self.fixture:
            new = {"id": "new-" + str(uuid.uuid4()), "t": parsed.title, "l": parsed.source_uid,
                   "due": (day(parsed.due) - self.today).days if parsed.due else None,
                   "time": parsed.due.strftime("%-I:%M %p") if isinstance(parsed.due, datetime) else "",
                   "start_time": parsed.start.strftime("%-I:%M %p") if parsed.start else "",
                   "start_day": (day(parsed.start) - self.today).days if parsed.start else None,
                   "pri": 0 if not parsed.priority else 3 if parsed.priority <= 4 else 2 if parsed.priority <= 6 else 1,
                   "notes": "", "done": False, "subs": [], "who": [parsed.assignee] if parsed.assignee else [], "act": []}
            self.data["tasks"].insert(0, new)
            return new["id"]
        uid = self.repository.add(parsed.source_uid, {"title": parsed.title, "due": parsed.due, "start": parsed.start,
                            "priority": parsed.priority, "assignee": parsed.assignee})
        return "|".join((parsed.source_uid, uid, ""))

    def delete(self, task_id):
        if self.fixture:
            index = next(i for i, t in enumerate(self.data["tasks"]) if t["id"] == task_id)
            task = self.data["tasks"].pop(index)
            return lambda: self.data["tasks"].insert(index, task)
        undo = self.repository.delete(self.records[task_id])
        return lambda: self.repository.undo(undo)

    def step(self, task_id, index):
        if self.fixture:
            task = next(t for t in self.data["tasks"] if t["id"] == task_id)
            steps = deepcopy(task["subs"]); steps[index][1] = not steps[index][1]
            return self.edit(task_id, {"subs": steps})
        task = next(t for t in self.data["tasks"] if t["id"] == task_id)
        record = self.records["|".join(task["step_keys"][index])]
        undo, _ = self.repository.complete(record, not record.completed)
        return lambda: self.repository.undo(undo)

    def add_step(self, task_id, text):
        if self.fixture:
            task = next(t for t in self.data["tasks"] if t["id"] == task_id)
            return self.edit(task_id, {"subs": task["subs"] + [[text, False]]})
        record = self.records[task_id]
        self.repository.add(record.source_uid, {"title": text, "parent": record.uid})

    def comment(self, task_id, text):
        if self.fixture:
            task = next(t for t in self.data["tasks"] if t["id"] == task_id)
            return self.edit(task_id, {"act": task["act"] + [["me", "c", text, "Just now"]]})
        record = self.records[task_id]
        author = self.repository.me or os.environ.get("USER", "")
        if self.repository.comments_are_local(record.source_uid):
            return self.local.comment(task_id, text, author)
        undo = self.repository.comment(record, text, author)
        return (lambda: self.repository.undo(undo)) if undo is not None else None

    def close(self): self.repository.close()


def plan_tasks(tasks, limit=6):
    """v71 Plan my day: the open tasks not already on today (late, later or undated), in order."""
    return [t for t in tasks if not t["done"] and t.get("due") != 0][:limit]
