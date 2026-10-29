# SPDX-License-Identifier: Apache-2.0
"""Operate published Linux accessibility controls with short-lived targets.

No synthetic keyboard/mouse input, shell commands or application-file edits.
The agent owns read/action permissions and approval of each change.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from pathlib import Path
import secrets
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ari.mcp import serve

_apps = {}
_targets = {}
_initialized = False


def api():
    global _initialized
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    if not _initialized:
        if Atspi.init() != 0:
            raise RuntimeError("Linux application accessibility is unavailable.")
        _initialized = True
    Atspi.set_timeout(500, 1500)
    return Atspi


def _start(pid):
    # /proc's start-time disambiguates PID reuse without reading its command or environment.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def _prune():
    now = time.monotonic()
    for collection in (_apps, _targets):
        for key, row in list(collection.items()):
            if now > row['expires']:
                del collection[key]
    if len(_apps) > 64:
        _apps.clear()
    if len(_targets) > 2000:
        _targets.clear()


def desktop_applications(arguments):
    Atspi = api()
    _prune()
    desktop = Atspi.get_desktop(0)
    query = arguments.get('name', '').strip().casefold()
    if not query:
        raise ValueError('Name the application the person asked to use.')
    rows = []
    for index in range(min(desktop.get_child_count(), 200)):
        app = desktop.get_child_at_index(index)
        if app is None:
            continue
        application_name = app.get_name() or ''
        windows = []
        for child_index in range(min(app.get_child_count(), 20)):
            child = app.get_child_at_index(child_index)
            if child is not None and child.get_role() in (Atspi.Role.FRAME, Atspi.Role.WINDOW):
                title = child.get_name()
                if title:
                    windows.append(title[:300])
        name = windows[0] if windows else application_name
        if query and query not in (application_name+' '+' '.join(windows)).casefold():
            continue
        try:
            pid = app.get_process_id()
            started = _start(pid)
        except (OSError, ValueError):
            continue
        token = secrets.token_urlsafe(18)
        _apps[token] = dict(object=app, name=name, query=query, pid=pid, started=started,
                            expires=time.monotonic()+300)
        rows.append({'application':token, 'name':name})
    if len(rows) == 1:
        rows[0]['snapshot'] = inspect_application({'application':rows[0]['application']})['data']
    return {'ok':True, 'summary':f'Found {len(rows)} accessible applications.',
            'data':rows, 'untrusted':True}


def _application(token):
    _prune()
    row = _apps.get(token)
    if row is None or _start(row['pid']) != row['started']:
        raise ValueError('The application changed. Find it again with desktop_applications.')
    return row


def _safe(node, Atspi):
    role = node.get_role()
    states = node.get_state_set()
    if role == Atspi.Role.PASSWORD_TEXT or states.contains(Atspi.StateType.DEFUNCT):
        return False
    return True


# GApplication exports the app's real commands on org.gtk.Actions. This is
# a shared native protocol, not a per-app file writer or synthetic input path.
# Targets bind discovery to the same process and unique bus owner as AT-SPI.
def _session_bus():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    return Gio, GLib, Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _dbus_call(bus, destination, path, interface, method, parameters=None, timeout=750):
    Gio, _, _ = _session_bus()
    return bus.call_sync(destination, path, interface, method, parameters, None,
                         Gio.DBusCallFlags.NO_AUTO_START, timeout, None)


def _command_owner(bus, name):
    _, GLib, _ = _session_bus()
    return _dbus_call(bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (name,))).unpack()[0]


def _command_pid(bus, name):
    _, GLib, _ = _session_bus()
    return _dbus_call(bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'GetConnectionUnixProcessID', GLib.Variant('(s)', (name,))).unpack()[0]


def _command_endpoints(app):
    _, _, bus = _session_bus()
    deadline = time.monotonic() + 1.5
    names = _dbus_call(bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'ListNames').unpack()[0]
    for name in names[:512]:
        if time.monotonic() > deadline:
            break
        if name.startswith(':'):
            continue
        try:
            owner = _command_owner(bus, name)
            if _command_pid(bus, owner) != app['pid']:
                continue
            path = '/' + name.replace('.', '/').replace('-', '_')
            commands = _dbus_call(bus, owner, path, 'org.gtk.Actions', 'DescribeAll').unpack()[0]
            yield bus, owner, path, commands
        except Exception:
            continue  # An accessibility app need not be a GApplication.


def _commands(token, app, query=''):
    result = []
    try:
        context = _command_context(app, api())
        for _bus, owner, path, commands in _command_endpoints(app):
            for name, (enabled, parameter_type, state) in sorted(commands.items()):
                # Bounded scalar arguments cover native app commands without
                # inventing schemas for complex app-private variant types.
                if parameter_type not in ('', 's', 'b', 'i', 'u', 'd') or not enabled:
                    continue
                label = name.replace('-', ' ')
                if query and query not in label.casefold():
                    continue
                target = secrets.token_urlsafe(18)
                _targets[target] = dict(application=token, command=name, owner=owner,
                    bus_path=path, parameter_type=parameter_type, context=context,
                    expires=time.monotonic()+120)
                result.append({'target':target, 'name':name, 'parameter_type':parameter_type,
                    'state':state, 'operation':'command'})
                if len(result) >= 16 or len(json.dumps(result, ensure_ascii=False)) > 2300:
                    return result
    except Exception as error:
        logging.getLogger(__name__).debug('Native commands unavailable: %s', error)
    return result


def _command_context(app, Atspi):
    """Bind a menu command to the current document/selection, including approvals."""
    pending = deque([(app['object'], 0)])
    context, visited = [], 0
    deadline = time.monotonic()+2
    while pending and visited < 2000 and time.monotonic() < deadline:
        node, depth = pending.popleft()
        visited += 1
        node.clear_cache()
        role, name = node.get_role(), node.get_name() or ''
        if role in (Atspi.Role.DIALOG, Atspi.Role.FRAME) and any(word in name.casefold()
                for word in ('authentication required', 'unlock keyring', 'authenticate')):
            raise ValueError('Authentication belongs to the person, not an application command.')
        if not _safe(node, Atspi):
            continue
        states = node.get_state_set()
        if states.contains(Atspi.StateType.EDITABLE) and node.get_editable_text_iface() is not None:
            interface = node.get_text_iface()
            if interface is None or interface.get_character_count() > 8000:
                raise ValueError('The current application text cannot be verified safely.')
            value = Atspi.Text.get_text(interface, 0, interface.get_character_count()) or ''
            context.append((depth, 'editable', hashlib.sha256(value.encode()).hexdigest()))
        selected = getattr(Atspi.StateType, 'SELECTED', None)
        if role in (Atspi.Role.FRAME, Atspi.Role.WINDOW) or (selected is not None and states.contains(selected)):
            if not name:
                # GTK list rows often name their label child, not the row.
                labels, children = [], deque([(node, 0)])
                while children and len(labels)<6:
                    child, child_depth = children.popleft()
                    if not _safe(child, Atspi):
                        continue
                    label = child.get_name() or ''
                    if label:
                        labels.append(label[:100])
                    if child_depth < 3:
                        for index in range(min(child.get_child_count(), 16)):
                            value = child.get_child_at_index(index)
                            if value is not None:
                                children.append((value, child_depth+1))
                name = ' · '.join(labels)
            context.append((depth, role.value_nick, name[:300]))
        if depth < 48:
            for index in range(min(node.get_child_count(), 2000, max(0, 4000-len(pending)))):
                child = node.get_child_at_index(index)
                if child is not None:
                    pending.append((child, depth+1))
    if pending:
        raise ValueError('The application selection could not be verified in time.')
    return context


def _native_command(arguments, Atspi):
    app = _application(arguments['application'])
    row = _targets.get(arguments['target'])
    if row is None or row.get('application') != arguments['application'] or 'command' not in row:
        raise ValueError('That native command expired. Inspect the application again.')
    _, GLib, bus = _session_bus()
    if _command_pid(bus, row['owner']) != app['pid'] or _start(app['pid']) != app['started']:
        raise ValueError('The application process changed. Find it again before acting.')
    actual = _dbus_call(bus, row['owner'], row['bus_path'], 'org.gtk.Actions', 'DescribeAll').unpack()[0]
    command = actual.get(row['command'])
    if command is None or not command[0] or command[1] != row['parameter_type']:
        raise ValueError('That native command changed or is disabled. Inspect again.')
    if _command_context(app, Atspi) != row['context']:
        raise ValueError('The active document or selection changed. Inspect again before acting.')
    value = arguments.get('parameter')
    kind = row['parameter_type']
    if kind == '':
        valid = value is None
    elif kind == 's':
        valid = isinstance(value, str) and len(value) <= 8000 and '\0' not in value
    elif kind == 'b':
        valid = type(value) is bool
    elif kind in ('i','u'):
        valid = type(value) is int and (-2**31 <= value < 2**31 if kind == 'i' else 0 <= value < 2**32)
    else:
        valid = type(value) in (int, float) and abs(value) < 1e100
    if not valid:
        raise ValueError('The parameter must match the native command’s advertised type.')
    summary = f'Run {row["command"].replace("-", " ")} in {app["name"]}'
    if arguments.get('preview'):
        return {'ok':True, 'preview':summary, 'detail':summary+'\nCurrent document/selection: '+
                '; '.join(item[2] for item in row['context'] if item[1]!='editable'),
            'application_name':app['name'], 'requested_name':app['query'],
            'application_scope':f'{app["pid"]}:{app["started"]}:{app["query"]}'}
    parameters = [] if not kind else [GLib.Variant(kind, value)]
    _dbus_call(bus, row['owner'], row['bus_path'], 'org.gtk.Actions', 'Activate',
        GLib.Variant('(sava{sv})', (row['command'], parameters, {})), timeout=2000)
    _invalidate(arguments['application'])
    result = {'ok':True, 'summary':f'Requested {row["command"].replace("-", " ")} in {app["name"]}.',
        'completed':False, 'verification_required':True}
    if arguments.get('inspect_after', True):
        time.sleep(.15)
        try:
            observed = inspect_application({'application':arguments['application']})
            result.update(data=observed['data'], untrusted=True)
        except Exception:
            result['observation_unavailable'] = True
    return result


def _invalidate(application):
    for key, row in list(_targets.items()):
        if row['application'] == application:
            del _targets[key]


def inspect_application(arguments):
    Atspi = api()
    app = _application(arguments['application'])
    _invalidate(arguments['application'])
    deadline = time.monotonic()+4
    pending = deque([(app['object'], (), 0)])
    rows, context = [], []
    visited = 0
    omitted_children = False
    protected = False
    query = str(arguments.get('query', '')).strip().casefold()[:200]
    offset = arguments.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 2000:
        raise ValueError('Use a control offset between 0 and 2000.')
    while pending and visited < 2000 and time.monotonic() < deadline:
        node, path, depth = pending.popleft()
        visited += 1
        try:
            node.clear_cache()
            if not _safe(node, Atspi):
                continue
            role = node.get_role()
            states = node.get_state_set()
            name = (node.get_name() or '')[:300]
            if role in (Atspi.Role.DIALOG, Atspi.Role.FRAME) and any(
                    word in name.casefold() for word in ('authentication required', 'unlock keyring', 'authenticate')):
                protected = True
                continue
            # GTK layout containers can omit SHOWING even when their children
            # are visible. Traverse them; only expose visible controls.
            if depth < 48:
                count = node.get_child_count()
                children = min(count, 2000, max(0,4000-len(pending)))
                omitted_children = omitted_children or children < count
                for index in range(children):
                    child = node.get_child_at_index(index)
                    if child is not None:
                        pending.append((child, (*path,index), depth+1))
            if depth > 1 and not states.contains(Atspi.StateType.SHOWING):
                continue
            actions = node.get_action_iface()
            action_names = ([actions.get_action_name(i) for i in range(min(actions.get_n_actions(), 12))]
                            if actions is not None else [])
            editable = (states.contains(Atspi.StateType.EDITABLE)
                        and node.get_editable_text_iface() is not None)
            text = ''
            text_digest = None
            text_iface = node.get_text_iface()
            if text_iface is not None:
                count = text_iface.get_character_count()
                full = Atspi.Text.get_text(text_iface, 0, min(count,8001)) or ''
                text = full[:1200]
                if editable and count <= 8000:
                    text_digest = hashlib.sha256(full.encode()).hexdigest()
            if not action_names and not editable:
                if name and len(context) < 12 and (not query or query in name.casefold()):
                    context.append(name[:100])
                continue
            if query and query not in (name+' '+text).casefold():
                continue
            token = secrets.token_urlsafe(18)
            _targets[token] = dict(application=arguments['application'], object=node, path=path,
                                  name=name, role=role, text_digest=text_digest, expires=time.monotonic()+120)
            rows.append({'target':token, 'role':role.value_nick, 'name':name[:160], 'text':text[:200],
                         'actions':action_names, 'editable':editable,
                         'enabled':states.contains(Atspi.StateType.SENSITIVE)})
        except Exception as error:
            logging.getLogger(__name__).debug("Accessibility control unavailable: %s", error)
            # One application may remove a widget while the snapshot is read.
            continue
    # Layout nodes and forecast prose must not crowd interactive suggestions
    # out of the model's result budget. Preserve whole JSON and paginate controls.
    commands = [] if protected else _commands(arguments['application'], app, query)
    selection = ([item for item in _targets[commands[0]['target']]['context'] if item[1]!='editable']
                 if commands else [])
    chosen, used = [], 0
    for row in rows[offset:offset+40]:
        size = len(json.dumps(row, ensure_ascii=False))
        if used+size > (4800 if commands else 7000):
            break
        chosen.append(row); used += size
    more = offset+len(chosen) < len(rows)
    return {'ok':True, 'summary':f'Read {len(chosen)} controls in {app["name"]}.',
            'data':{'application':arguments['application'], 'name':app['name'], 'controls':chosen, 'commands':commands,
                    'visible_text':context, 'selection':selection,
                    'truncated':bool(pending) or more or omitted_children,
                    'scanned_nodes':visited,
                    'next_offset':offset+len(chosen) if more else None}, 'untrusted':True}


def _target(arguments, Atspi):
    app = _application(arguments['application'])
    row = _targets.get(arguments['target'])
    if row is None or row['application'] != arguments['application']:
        raise ValueError('That control expired or belongs to another application. Inspect the application again.')
    node = app['object']
    for index in row['path']:
        node.clear_cache()
        if index >= node.get_child_count():
            raise ValueError('The application layout changed. Inspect it again before acting.')
        node = node.get_child_at_index(index)
    node.clear_cache()
    states = node.get_state_set()
    if (node != row['object'] or (node.get_name() or '') != row['name'] or node.get_role() != row['role']
            or not _safe(node,Atspi) or not states.contains(Atspi.StateType.SHOWING)
            or not states.contains(Atspi.StateType.SENSITIVE)):
        raise ValueError('That control changed or is no longer available. Inspect the application again.')
    return app, row, node


def act_in_application(arguments):
    Atspi = api()
    if arguments['operation'] == 'command':
        return _native_command(arguments, Atspi)
    app, row, node = _target(arguments, Atspi)
    operation = arguments['operation']
    value = arguments.get('text','')
    if operation == 'set_text':
        interface = node.get_editable_text_iface()
        if interface is None or not node.get_state_set().contains(Atspi.StateType.EDITABLE):
            raise ValueError('This control does not expose editable text.')
        current = node.get_text_iface()
        if (row['text_digest'] is None or current is None or current.get_character_count()>8000
                or hashlib.sha256((Atspi.Text.get_text(current,0,current.get_character_count()) or '').encode()).hexdigest()!=row['text_digest']):
            raise ValueError('The text changed or is too large to replace safely. Inspect the application again.')
        if not isinstance(value,str) or len(value)>8000 or '\0' in value:
            raise ValueError('Use at most 8,000 characters of text.')
        summary = f'Enter text in {row["name"] or "the text field"} in {app["name"]}'
        detail = value[:1000] + ('…' if len(value)>1000 else '')
    elif operation == 'action':
        interface = node.get_action_iface()
        names = ([interface.get_action_name(i) for i in range(min(interface.get_n_actions(),12))]
                 if interface is not None else [])
        action = arguments.get('action','')
        if action not in names:
            raise ValueError('Choose an action actually listed by this control.')
        summary = f'Activate {row["name"] or row["role"].value_nick} in {app["name"]}'
        detail = f'{action}: {row["name"] or row["role"].value_nick}'
    else:
        raise ValueError('Choose set_text or action.')
    if arguments.get('preview'):
        return {'ok':True, 'preview':summary, 'detail':detail, 'application_name':app['name'],
                'requested_name':app['query'], 'application_scope':f'{app["pid"]}:{app["started"]}:{app["query"]}'}
    succeeded = (interface.set_text_contents(value) if operation == 'set_text'
                 else interface.do_action(names.index(action)))
    _invalidate(arguments['application'])
    completed = ('Entered text'+summary[len('Enter text'):] if operation=='set_text'
                 else 'Activated'+summary[len('Activate'):])
    result = {'ok':bool(succeeded), 'summary':completed+'.' if succeeded else 'The application refused that control action.'}
    if succeeded and arguments.get('inspect_after',True):
        # Return observed controls with the mutation, saving another model round
        # trip. Allow the app's debounced search to publish its result first.
        time.sleep(.35)
        observed = inspect_application({'application':arguments['application']})
        result.update(data=observed['data'], untrusted=True)
    return result


def schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
STRING={'type':'string'}
TOOLS={
 'desktop_applications':('Find an open application by the friendly name the person used (for example Weather), using Linux accessibility. Use this fallback when an app has no broker actions. A unique match includes its initial snapshot and native backend commands, saving another inspection. Returned IDs are short-lived and cannot be guessed.',schema({'name':STRING},['name']),desktop_applications),
 'inspect_application':('Read an open application’s visible controls and actual action names. Use query to find a named control/result (such as Chicago); use next_offset to page if truncated. Layout containers are omitted. Protected fields are excluded; targets are short-lived. Also returns native backend commands when exported. Prefer a matching command over menu navigation. Changes already return a fresh snapshot; inspect again only if needed.',schema({'application':STRING,'query':STRING,'offset':{'type':'integer','minimum':0,'maximum':2000}},['application']),inspect_application),
 'act_in_application':('Operate a visible application control using an exact inspected target. Use set_text to replace an editable field, or action with an advertised action name to activate a button/result. Use operation command with an exact commands target to invoke an app’s own backend command directly; parameter must match its advertised scalar type. A command acknowledgement is not completion. Returns fresh observed controls after the change, when read permission allows it. Verify the requested outcome; activation alone does not mean the whole task succeeded.',schema({'application':STRING,'target':STRING,'operation':{'type':'string','enum':['set_text','action','command']},'text':STRING,'action':STRING,'parameter':{},'preview':{'type':'boolean'},'inspect_after':{'type':'boolean'}},['application','target','operation']),act_in_application),
}
if __name__=='__main__':
    serve('ari-accessibility',TOOLS)
