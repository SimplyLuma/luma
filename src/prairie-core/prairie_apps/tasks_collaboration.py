# SPDX-License-Identifier: Apache-2.0
"""Hub task lists behind the existing native Tasks repository interface.

EDS remains the personal provider. Shared records use Hub revision checks and
permissions; they are never silently copied back over the EDS recovery source.
"""
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
import json
import uuid
from .collaboration import CollaborationCache, CollaborationClient, public_name
from .connect_sync import ConnectError, HubResponseError
from .tasks_backend import Comment, Task, TaskList

SOURCE_PREFIX = 'luma-shared:'
def shared_items(snapshot):
    content = snapshot['content']
    return [content['task'], *content.get('steps', []), *content.get('history', [])] if snapshot['kind'] == 'task' else content['tasks']


FIELDS = frozenset(('uid', 'title', 'due', 'completed', 'notes', 'priority', 'section', 'parent', 'waiting', 'assignee', 'assignee_name', 'repeat', 'order', 'completed_at', 'recurrence_id', 'start'))


def task_content(task):
    result = {field: getattr(task, field) for field in FIELDS}
    # An ICalendar round trip stores Hub accounts as valid CAL-ADDRESS URNs.
    # Keep the collaboration record's account identity, including recurrence
    # successors, rather than treating that URI as a different assignee.
    if result['assignee'].startswith('urn:luma:account:'):
        from urllib.parse import unquote
        result['assignee'] = unquote(result['assignee'].removeprefix('urn:luma:account:'))
    for field in ('due', 'start', 'completed_at'):
        result[field] = result[field].isoformat() if result[field] else None
    if task.raw:
        component = task_component(task)
        result['ical'] = component.as_ical_string()
    return result


def task_component(task):
    from .tasks_backend import _modules, task_from_ical, patch_task
    _, _, ical = _modules()
    raw = task.raw
    if raw.startswith('{'):
        raw = json.loads(raw).get('ical', '')
    component = ical.Component.new_from_string(raw) if raw else ical.Component.new_from_string('BEGIN:VTODO\r\nUID:' + task.uid + '\r\nEND:VTODO\r\n')
    original = task_from_ical(component.as_ical_string(), task.source_uid)
    changed = {name: getattr(task, name) for name in ('title', 'notes', 'priority', 'due', 'start', 'completed', 'section', 'parent', 'waiting', 'repeat', 'order') if getattr(original, name) != getattr(task, name)}
    if changed:
        component = patch_task(component, changed)
    if task.assignee != original.assignee or task.assignee_name != original.assignee_name:
        from urllib.parse import quote
        from .tasks_backend import properties, _name
        for prop in properties(component):
            if _name(prop) == 'ATTENDEE' and (prop.get_value_as_string() or '').removeprefix('mailto:') == original.assignee:
                component.remove_property(prop)
        if task.assignee:
            address = ('mailto:' + task.assignee) if '@' in task.assignee else ('urn:luma:account:' + quote(task.assignee, safe=''))
            prop = ical.Property.new_from_string('ATTENDEE:' + address)
            if task.assignee_name: prop.set_parameter_from_string('CN', task.assignee_name)
            component.add_property(prop)
    from .tasks_backend import _escape
    existing = {(comment.text, comment.author) for comment in original.comments}
    for comment in task.comments:
        if (comment.text, comment.author) not in existing:
            prop = ical.Property.new_from_string('COMMENT:' + _escape(comment.text))
            prop.set_parameter_from_string('X-LUMA-AUTHOR', comment.author or 'Me')
            component.add_property(prop)
    return component


def read_task_content(value, source, comments=(), account=""):
    if not isinstance(value, dict) or set(value) - {'ical'} != FIELDS:
        raise ValueError('Invalid shared task fields.')
    fields = dict(value)
    ical_text = fields.pop('ical', '')
    for name in ('due', 'start', 'completed_at'):
        raw = fields[name]
        fields[name] = (datetime.fromisoformat(raw) if 'T' in raw else date.fromisoformat(raw)) if raw else None
    if not fields['uid'] or not isinstance(fields['title'], str) or len(fields['title']) > 4096:
        raise ValueError('Invalid shared task identity or title.')
    if not isinstance(fields['completed'], bool) or fields['priority'] not in range(10):
        raise ValueError('Invalid shared task status.')
    for name in FIELDS - {'due', 'start', 'completed_at', 'completed', 'priority'}:
        if not isinstance(fields[name], str):
            raise ValueError('Invalid shared task text.')
    imported = ()
    if ical_text:
        from .tasks_backend import task_from_ical
        parsed = task_from_ical(ical_text, source)
        if parsed.uid != fields['uid']:
            raise ValueError('Shared task calendar identity differs from its record.')
        imported = parsed.comments
    fields['comments'] = imported + tuple(Comment(c['body'], '' if c['account'] == account else c['account'], str(c['created_at'])) for c in comments if c.get('target', '') in ('', fields['uid']))
    return Task(source_uid=source, raw=json.dumps(value, sort_keys=True), **fields)


@dataclass(frozen=True)
class SharedUndo:
    document: str
    revision: int
    content: dict


@dataclass(frozen=True)
class SharedRemovalUndo:
    document: str
    revision: int


class CollaborativeTasksRepository:
    def __init__(self, personal, changed=None):
        self.personal = personal
        self.changed = changed or (lambda: None)
        self.shared = {}
        self.monitor = None
        try:
            from gi.repository import Gio
            cache = CollaborationCache()
            path = cache.path
            cache.close()
            self.monitor = Gio.File.new_for_path(str(path.parent)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.monitor.set_rate_limit(100)
            self.monitor.connect('changed', lambda _monitor, file, _other, _event: self.changed() if file.get_basename() in (path.name, path.name + '-wal') and _event in (Gio.FileMonitorEvent.CHANGED, Gio.FileMonitorEvent.CHANGES_DONE_HINT) else None)
        except (ImportError, RuntimeError, OSError):
            pass

    def __getattr__(self, name):
        return getattr(self.personal, name)

    def load(self):
        sources, records = self.personal.load()
        self.shared = {}
        try:
            client, cache = CollaborationClient(), CollaborationCache(read_only=True)
        except ConnectError:
            return sources, records
        try:
            hidden_sources, hidden_tasks = set(), set()
            added_sources, added_records = [], []
            for mapping in cache.mappings(client):
                if mapping['kind'] not in ('task', 'list') or mapping['role'] == 'revoked':
                    continue
                row = cache.db.execute('SELECT snapshot FROM snapshots WHERE id=?', (mapping['id'],)).fetchone()
                if row is None:
                    continue
                snapshot = json.loads(row[0])
                uid = SOURCE_PREFIX + mapping['id']
                self.shared[uid] = snapshot
                members = tuple((p['account'], public_name(p)) for p in snapshot['members'] if p['accepted'])
                added_sources.append(TaskList(uid, snapshot['content']['title'], 'Luma Hub', snapshot['role'] in ('owner', 'edit'), members=members, shared=True,
                                               commentable=snapshot['role'] in ('owner', 'edit', 'comment')))
                tasks = shared_items(snapshot)
                added_records.extend(read_task_content(t, uid, snapshot['comments'], snapshot.get('account', '')) for t in tasks)
                if snapshot['role'] == 'owner':
                    if mapping['kind'] == 'list':
                        hidden_sources.add(mapping['local_id'])
                    else:
                        hidden_tasks.add(mapping['local_id'])
            return tuple(s for s in sources if s.uid not in hidden_sources) + tuple(added_sources), tuple(t for t in records if t.source_uid not in hidden_sources and '|'.join(t.key) not in hidden_tasks) + tuple(added_records)
        finally:
            cache.close()

    def _snapshot(self, source):
        if source not in self.shared:
            raise ValueError('Reload this shared list before changing it.')
        return self.shared[source]

    def _commit(self, source, content):
        snapshot = self._snapshot(source)
        before = snapshot['content']
        current = CollaborationClient()
        result = current.operation(snapshot['id'], 'content', {'revision': snapshot['revision'], 'content': content})
        cache = CollaborationCache()
        try:
            mapping = next(r for r in cache.mappings(current) if r['id'] == snapshot['id'])
            cache.remember(current, result, mapping['local_id'])
        finally:
            cache.close()
        self.shared[source] = result
        self.changed()
        return SharedUndo(snapshot['id'], result['revision'], before)

    def edit(self, task, changes):
        if not task.source_uid.startswith(SOURCE_PREFIX):
            return self.personal.edit(task, changes)
        if set(changes) - FIELDS:
            raise ValueError('Unsupported shared task change.')
        snapshot = self._snapshot(task.source_uid)
        if changes.get('assignee'):
            person = next((member for member in snapshot['members'] if member['accepted'] and member['account'] == changes['assignee']), None)
            if person is None:
                raise ValueError('Assign this shared task to a person who has accepted its invitation.')
            changes = dict(changes, assignee_name=public_name(person))
        content = json.loads(json.dumps(snapshot['content']))
        items = [content['task'], *content.get('steps', []), *content.get('history', [])] if snapshot['kind'] == 'task' else content['tasks']
        current = next((raw for raw in items if raw['uid'] == task.uid and raw['recurrence_id'] == task.recurrence_id), None)
        if current is None or json.dumps(current, sort_keys=True) != task.raw:
            raise RuntimeError('This task changed. Reload before saving; your edit was not sent.')
        edited = task_content(replace(task, **changes))
        current.update(edited)
        if snapshot['kind'] == 'task' and content['task']['uid'] == task.uid:
            content['title'] = edited['title']
        return self._commit(task.source_uid, content)

    def add(self, source, changes):
        if not source.startswith(SOURCE_PREFIX):
            return self.personal.add(source, changes)
        snapshot = self._snapshot(source)
        if snapshot['kind'] == 'task' and changes.get('parent') != snapshot['content']['task']['uid']:
            raise ValueError('Add a step to this shared task, or add a task to a shared list.')
        content = json.loads(json.dumps(snapshot['content']))
        uid = str(uuid.uuid4())
        content.setdefault('steps' if snapshot['kind'] == 'task' else 'tasks', []).append(task_content(Task(uid, source, changes.get('title', ''), **{key: value for key, value in changes.items() if key != 'title'})))
        self._commit(source, content)
        return uid

    def complete(self, task, completed):
        if not task.source_uid.startswith(SOURCE_PREFIX):
            return self.personal.complete(task, completed)
        if task.recurring:
            if not completed or task.recurrence_id:
                return self.edit(task, {'completed': completed}), 'Completed occurrence' if completed else 'Reopened'
            from .tasks_backend import next_recurring_due, patch_task, replace_property, read_task, _time, due_words
            before = task_component(task)
            next_due, anchor = next_recurring_due(before)
            if next_due is None:
                return self.edit(task, {'completed': True}), 'Completed · repeat finished'
            snapshot = self._snapshot(task.source_uid)
            content = json.loads(json.dumps(snapshot['content']))
            changed = {'due': _time(next_due), 'completed': False}
            if isinstance(task.due, datetime) and isinstance(task.start, datetime):
                changed['start'] = _time(next_due) - (task.due - task.start)
            after = patch_task(before, changed)
            replace_property(after, 'X-LUMA-REPEAT-ANCHOR', ['X-LUMA-REPEAT-ANCHOR:' + anchor.as_ical_string()])
            after.set_due(next_due)
            from .tasks_backend import _modules, value
            _, _, ical = _modules()
            original_due = before.get_first_property(ical.PropertyKind.DUE_PROPERTY)
            tzid = original_due.get_parameter_as_string('TZID')
            if tzid:
                after.get_first_property(ical.PropertyKind.DUE_PROPERTY).set_parameter_from_string('TZID', tzid)
            copy = patch_task(before, {'completed': True})
            copy.set_uid(str(uuid.uuid4()))
            for field in ('RRULE', 'RDATE', 'EXDATE', 'RECURRENCE-ID', 'X-LUMA-REPEAT-ANCHOR'):
                replace_property(copy, field, [])
            successor = task_content(read_task(after, task.source_uid))
            archived = task_content(read_task(copy, task.source_uid))
            items = content.get('steps', []) if snapshot['kind'] == 'task' else content['tasks']
            children, pending = [], {task.uid}
            while pending:
                generation = [item for item in items if item['parent'] in pending]
                children.extend(generation)
                pending = {item['uid'] for item in generation}
                if len(children) > len(items):
                    raise ValueError('This task has cyclic steps.')
            identifiers = {task.uid: archived['uid']}
            archived_children = []
            for child in children:
                component = task_component(read_task_content(child, task.source_uid))
                component.set_uid(str(uuid.uuid4()))
                identifiers[child['uid']] = component.get_uid()
                component = patch_task(component, {'parent': identifiers[child['parent']]})
                archived_children.append(task_content(read_task(component, task.source_uid)))
                child.update(task_content(read_task(patch_task(task_component(read_task_content(child, task.source_uid)), {'completed': False}), task.source_uid)))
            if snapshot['kind'] == 'task':
                if content['task']['uid'] != task.uid:
                    raise ValueError('Complete a historical occurrence without advancing the active series.')
                content['task'] = successor
                content.setdefault('history', []).extend([archived, *archived_children])
            else:
                content['tasks'] = [successor if item['uid'] == task.uid else item for item in content['tasks']]
                content['tasks'].extend([archived, *archived_children])
            return self._commit(task.source_uid, content), 'Done. The next one is ' + due_words(_time(next_due)) + '.'
        return self.edit(task, {'completed': completed}), 'Completed' if completed else 'Reopened'

    def delete(self, task):
        if not task.source_uid.startswith(SOURCE_PREFIX):
            return self.personal.delete(task)
        snapshot = self._snapshot(task.source_uid)
        if snapshot['kind'] == 'task' and snapshot['content']['task']['uid'] == task.uid:
            return self.delete_list(task.source_uid)
        content = json.loads(json.dumps(snapshot['content']))
        field = ('history' if any(t['uid'] == task.uid for t in content.get('history', [])) else 'steps') if snapshot['kind'] == 'task' else 'tasks'
        removed = {task.uid}
        for _ in range(len(content[field])):
            removed.update(t['uid'] for t in content[field] if t['parent'] in removed)
        content[field] = [t for t in content[field] if t['uid'] not in removed]
        return self._commit(task.source_uid, content)

    def comments_are_local(self, source):
        return False if source.startswith(SOURCE_PREFIX) else self.personal.comments_are_local(source)

    def comment(self, task, text, author):
        if not task.source_uid.startswith(SOURCE_PREFIX):
            return self.personal.comment(task, text, author)
        snapshot = self._snapshot(task.source_uid)
        client = CollaborationClient()
        client.operation(snapshot['id'], 'comments', {'id': str(uuid.uuid4()), 'body': text, 'target': task.uid})
        result = client.read(snapshot['id'])
        cache = CollaborationCache()
        try:
            mapping = next(r for r in cache.mappings(client) if r['id'] == result['id'])
            cache.remember(client, result, mapping['local_id'])
        finally:
            cache.close()
        self.shared[task.source_uid] = result
        self.changed()
        return None  # Append-only comments have no dishonest local undo.

    def undo(self, action):
        if action is None:
            raise ValueError('This shared comment cannot be undone locally.')
        if isinstance(action, SharedRemovalUndo):
            client, cache = CollaborationClient(), CollaborationCache()
            try:
                snapshot = client.operation(action.document, 'restore', {'revision': action.revision})
                mapping = next(r for r in cache.mappings(client) if r['id'] == action.document)
                cache.remember(client, snapshot, mapping['local_id'])
                self.shared[SOURCE_PREFIX + action.document] = snapshot
            finally:
                cache.close()
            self.changed()
            return None
        if not isinstance(action, SharedUndo):
            return self.personal.undo(action)
        source = SOURCE_PREFIX + action.document
        if self._snapshot(source)['revision'] != action.revision:
            raise RuntimeError('This shared document changed; undo would overwrite another edit.')
        return self._commit(source, action.content)

    def update_list(self, source_uid, *, name=None, color=None):
        if not source_uid.startswith(SOURCE_PREFIX):
            return self.personal.update_list(source_uid, name=name, color=color)
        snapshot = self._snapshot(source_uid)
        content = json.loads(json.dumps(snapshot['content']))
        if color is not None:
            if snapshot['kind'] != 'list':
                raise ValueError('Change the color of a shared list, rather than a single shared task.')
            content['color'] = color
        if name is not None:
            content['title'] = name.strip()
        return self._commit(source_uid, content)

    def delete_list(self, source_uid, destination=None):
        if not source_uid.startswith(SOURCE_PREFIX):
            if destination and destination.startswith(SOURCE_PREFIX):
                return self._move_personal_list(source_uid, destination)
            return self.personal.delete_list(source_uid, destination)
        if destination:
            uids = list(dict.fromkeys(t['uid'] for t in shared_items(self._snapshot(source_uid))))
            if uids:
                return self._transfer(source_uid, destination, uids, remove_source=True)
        snapshot = self._snapshot(source_uid)
        client = CollaborationClient()
        result = client.operation(snapshot['id'], 'remove', {'revision': snapshot['revision']})
        cache = CollaborationCache()
        try:
            with cache.db:
                cache.db.execute('UPDATE documents SET role=? WHERE id=?', ('revoked', snapshot['id']))
        finally:
            cache.close()
        self.changed()
        return SharedRemovalUndo(snapshot['id'], result['revision'])

    def _transfer(self, source, destination, uids, *, remove_source=False):
        if not destination.startswith(SOURCE_PREFIX):
            return self._transfer_to_personal(source, destination, uids, remove_source=remove_source)
        before, target = self._snapshot(source), self._snapshot(destination)
        client = CollaborationClient()
        result = client.operation(before['id'], 'transfer', {'revision': before['revision'], 'destination': target['id'], 'destination_revision': target['revision'], 'uids': uids, 'remove_source': remove_source})
        cache = CollaborationCache()
        try:
            mappings = {r['id']: r for r in cache.mappings(client)}
            for name in ('source', 'destination'):
                if name in result:
                    snapshot = result[name]
                    cache.remember(client, snapshot, mappings[snapshot['id']]['local_id'])
                    self.shared[SOURCE_PREFIX + snapshot['id']] = snapshot
            if result['source_deleted']:
                with cache.db:
                    cache.db.execute('UPDATE documents SET role=? WHERE id=?', ('revoked', before['id']))
        finally:
            cache.close()
        self.changed()
        return None

    def move(self, task, destination):
        if task.source_uid == destination:
            raise ValueError('Choose a different list.')
        if task.source_uid.startswith(SOURCE_PREFIX):
            snapshot = self._snapshot(task.source_uid)
            if snapshot['kind'] == 'task' and task.uid == snapshot['content']['task']['uid']:
                uids = [t['uid'] for t in shared_items(snapshot)]
            else:
                uids = [task.uid]
            return self._transfer(task.source_uid, destination, uids)
        if not destination.startswith(SOURCE_PREFIX):
            return self.personal.move(task, destination)
        return self._transfer_from_personal(task, destination)

    def _transfer_to_personal(self, source, destination, uids, *, remove_source=False):
        from .tasks_backend import patch_task
        snapshot = self._snapshot(source)
        items = shared_items(snapshot)
        selected = set(uids)
        for _ in items:
            selected.update(t['uid'] for t in items if t['parent'] in selected)
        if snapshot['kind'] == 'task' and snapshot['content']['task']['uid'] in selected:
            selected = {t['uid'] for t in items}; remove_source = True
        moved = [t for t in items if t['uid'] in selected]
        if not moved or snapshot['role'] not in ('owner', 'edit') or remove_source and snapshot['role'] != 'owner':
            raise ValueError('You do not have permission to move these tasks.')
        from .tasks_local import atomic_json, data_root
        atomic_json(data_root() / 'task-transfers' / (str(uuid.uuid4()) + '.json'), {'source': source, 'destination': destination, 'snapshot': snapshot, 'uids': list(selected)})
        components = []
        for raw in moved:
            record = read_task_content(raw, source, snapshot['comments'], snapshot.get('account', ''))
            component = task_component(record)
            component = patch_task(component, {'parent': record.parent if record.parent in selected else '', 'section': ''})
            components.append(component)
        created = self.personal.import_components(destination, components)
        try:
            if remove_source:
                self.delete_list(source)
            else:
                content = json.loads(json.dumps(snapshot['content']))
                field = 'tasks' if snapshot['kind'] == 'list' else 'history' if any(t['uid'] in selected for t in content.get('history', [])) else 'steps'
                content[field] = [t for t in content[field] if t['uid'] not in selected]
                self._commit(source, content)
        except Exception:
            for uid in reversed(created):
                self.personal._delete(destination, uid)
            raise
        self.changed()
        return None

    def _move_personal_list(self, source, destination):
        from .tasks_backend import read_task, value
        target = self._snapshot(destination)
        if target['kind'] != 'list':
            raise ValueError('Choose a shared list.')
        original = self.personal.registry.ref_source(source)
        if original is None or not original.get_removable():
            raise ValueError('This provider does not allow removing this list.')
        ok, components = self.personal.clients[source].get_object_list_sync('#t', None)
        if not ok:
            raise RuntimeError('Could not read the source list. Nothing was moved.')
        records = [task_content(replace(read_task(c, source), section='')) for c in components or () if value(c, 'X-LUMA-KIND') != 'PROJECT']
        content = json.loads(json.dumps(target['content']))
        if any(t['uid'] == existing['uid'] for t in records for existing in content['tasks']):
            raise ValueError('The destination already contains a task identity. Nothing was moved.')
        content['tasks'].extend(records)
        undo = self._commit(destination, content)
        try:
            ok, current = self.personal.clients[source].get_object_list_sync('#t', None)
            if not ok or sorted(c.as_ical_string() for c in current or ()) != sorted(c.as_ical_string() for c in components or ()):
                raise RuntimeError('The original list changed during transfer. It was kept.')
            self.personal.delete_list(source)
        except Exception as failure:
            try:
                self.undo(undo)
            except Exception as rollback:
                raise RuntimeError('The original list was kept. A shared copy also remains because another collaborator changed it; review both lists.') from rollback
            raise failure
        self.changed()
        return None

    def _transfer_from_personal(self, task, destination):
        from .tasks_backend import read_task, value
        snapshot = self._snapshot(destination)
        if snapshot['kind'] != 'list':
            raise ValueError('Choose a shared list, rather than a single shared task.')
        if task.recurrence_id:
            raise ValueError('Move the full recurring task rather than a detached occurrence.')
        before = self.personal._get(*task.key)
        if before.as_ical_string() != task.raw:
            raise RuntimeError('This task changed. Reload before moving.')
        components = [before, *self.personal._children(task)]
        ok, others = self.personal.clients[task.source_uid].get_object_list_sync('#t', None)
        if not ok:
            raise RuntimeError('Could not read recurrence exceptions. Nothing was moved.')
        components.extend(c for c in others if c.get_uid() == task.uid and value(c, 'RECURRENCE-ID'))
        content = json.loads(json.dumps(snapshot['content']))
        identifiers = {t['uid'] for t in content['tasks']}
        if any(c.get_uid() in identifiers for c in components):
            raise ValueError('The destination already contains this task identity. Nothing was moved.')
        for component in components:
            content['tasks'].append(task_content(replace(read_task(component, task.source_uid), section='')))
        undo = self._commit(destination, content)
        try:
            self.personal.delete(task)
        except Exception as failure:
            try:
                self.undo(undo)
            except Exception as rollback:
                raise RuntimeError('The original task was kept. A copy remains in the shared list because another collaborator changed it; review both lists.') from rollback
            raise failure
        return None

    def source_color(self, source):
        return self._snapshot(source)['content'].get('color', '#3d91d9')

    def close(self):
        if self.monitor:
            self.monitor.cancel()
        self.personal.close()


def sync_tasks_collaboration(*, environment=None, http=None):
    from .collaboration_ownership import profile_ready
    if not profile_ready('org.projectluma.Tasks', environment):
        return 0
    from .app_installs import TASKS, app_environment
    client = CollaborationClient(environment, http)
    cache = CollaborationCache(app_environment(TASKS, environment))
    updated = 0
    try:
        try:
            documents = client.documents()
        except HubResponseError as error:
            if error.status == 404:
                return 0
            raise
        with cache.db:
            cache.db.execute("DELETE FROM invitations WHERE hub=? AND device=? AND kind IN ('task','list')", (client.address, client.identity.device_id))
            for document in documents:
                if document['kind'] in ('task', 'list') and not document['accepted']:
                    cache.db.execute('INSERT OR REPLACE INTO invitations VALUES(?,?,?,?,?)', (document['id'], client.address, client.identity.device_id, document['kind'], document['owner']))
        mappings = {r['id']: r for r in cache.mappings(client) if r['kind'] in ('task', 'list')}
        active = set()
        for document in documents:
            if document['kind'] not in ('task', 'list') or not document['accepted']:
                continue
            snapshot = cache.unchanged_snapshot(client, document) or client.read(document['id'])
            source = SOURCE_PREFIX + document['id']
            for raw in shared_items(snapshot):
                read_task_content(raw, source, snapshot['comments'])
            active.add(document['id'])
            mapping = mappings.get(document['id'])
            cache.remember(client, snapshot, mapping['local_id'] if mapping else source)
            updated += 1
        with cache.db:
            for id in mappings.keys() - active:
                cache.db.execute('UPDATE documents SET role=? WHERE id=?', ('revoked', id))
        return updated
    finally:
        cache.close()
