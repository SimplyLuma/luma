# SPDX-License-Identifier: Apache-2.0
"""Read-only v70 data for the measured Tasks port; never opens EDS.

The original lists, multiple assignees, flags, steps and activity stay in
``data`` for the LumaUI composition. ``load`` also supplies the existing Tasks
model while that composition is being ported. No fixture operation writes to
the file or falls back to the real repository.
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path

from .tasks_backend import Comment, Task, TaskList


class FixtureRepository:
    def __init__(self, path):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.today = date.fromisoformat(self.data["today"])
        self.me = "me"
        self.errors = ()
        self.sources = tuple(TaskList(
            entry["id"], entry["n"], writable=False, default=entry["id"] == "personal",
            sections=tuple(entry.get("secs", ())),
            members=tuple((key, self.data["people"][key]["n"]) for key in entry["ppl"]),
            shared=bool(entry["ppl"]),
        ) for entry in self.data["lists"])
        lists = {source.uid: source for source in self.sources}
        records = []
        for entry in self.data["tasks"]:
            due = None
            if entry.get("due") is not None:
                due = self.today + timedelta(days=entry["due"])
                if entry.get("time"):
                    clock = datetime.strptime(entry["time"], "%I:%M %p").time()
                    due = datetime.combine(due, clock, tzinfo=timezone.utc)
            source = lists[entry["l"]]
            who = next(iter(entry["who"]), "")
            records.append(Task(
                entry["id"], source.uid, entry["t"], due=due, completed=entry["done"],
                notes=entry["notes"], priority={0: 0, 1: 9, 2: 5, 3: 1}[entry["pri"]],
                section=source.sections[entry.get("sec", 0)] if source.sections else "",
                assignee=who, assignee_name=self.data["people"][who]["n"] if who else "",
                comments=tuple(Comment(a[2], self.data["people"][a[0]]["n"], a[3])
                               for a in entry["act"] if a[1] == "c"),
                raw=json.dumps(entry, ensure_ascii=False),
            ))
            for index, (title, completed) in enumerate(entry["subs"]):
                records.append(Task(f'{entry["id"]}-step-{index}', source.uid, title,
                                    completed=completed, parent=entry["id"]))
        self.records = tuple(records)

    def load(self):
        return self.sources, self.records

    def close(self):
        pass

    def _readonly(self, *args, **kwargs):
        raise RuntimeError("The Tasks fixture is read-only.")

    add = edit = delete = complete = comment = undo = create_list = configure = _readonly


def source_from_environment(changed=None):
    # A bad fixture is an error, never a reason to open Nick's real task lists.
    if "LUMA_TASKS_FIXTURE" in os.environ:
        return FixtureRepository(os.environ["LUMA_TASKS_FIXTURE"])
    from .tasks_backend import TasksRepository
    from .tasks_collaboration import CollaborativeTasksRepository
    return CollaborativeTasksRepository(TasksRepository(changed), changed)
