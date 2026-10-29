# SPDX-License-Identifier: Apache-2.0
"""Operate published Linux accessibility controls with short-lived targets.

No synthetic keyboard/mouse input, shell commands or application-file edits.
The agent owns read/action permissions and approval of each change.
"""
from __future__ import annotations

import hashlib
import logging
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


def inspect_application(arguments):
    Atspi = api()
    app = _application(arguments['application'])
    for key, row in list(_targets.items()):
        if row['application'] == arguments['application']:
            del _targets[key]
    deadline = time.monotonic()+8
    pending = [(app['object'], (), 0)]
    rows = []
    while pending and len(rows) < 300 and time.monotonic() < deadline:
        node, path, depth = pending.pop()
        try:
            node.clear_cache()
            if not _safe(node, Atspi):
                continue
            role = node.get_role()
            states = node.get_state_set()
            name = (node.get_name() or '')[:300]
            if role in (Atspi.Role.DIALOG, Atspi.Role.FRAME) and any(
                    word in name.casefold() for word in ('authentication required', 'unlock keyring', 'authenticate')):
                continue
            # GTK layout containers can omit SHOWING even when their children
            # are visible. Traverse them; only expose visible controls.
            if depth < 48:
                children = min(node.get_child_count(), 100)
                for index in reversed(range(children)):
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
            token = secrets.token_urlsafe(18)
            _targets[token] = dict(application=arguments['application'], object=node, path=path,
                                  name=name, role=role, text_digest=text_digest, expires=time.monotonic()+120)
            rows.append({'target':token, 'role':role.value_nick, 'name':name, 'text':text,
                         'actions':action_names, 'editable':editable,
                         'enabled':states.contains(Atspi.StateType.SENSITIVE)})
        except Exception as error:
            logging.getLogger(__name__).debug("Accessibility control unavailable: %s", error)
            # One application may remove a widget while the snapshot is read.
            continue
    return {'ok':True, 'summary':f'Read controls in {app["name"]}.',
            'data':{'application':arguments['application'], 'name':app['name'], 'controls':rows,
                    'truncated':bool(pending)}, 'untrusted':True}


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
        return {'ok':True, 'preview':summary, 'detail':detail, 'application_name':app['name'], 'requested_name':app['query']}
    succeeded = (interface.set_text_contents(value) if operation == 'set_text'
                 else interface.do_action(names.index(action)))
    for key, target in list(_targets.items()):
        if target['application'] == arguments['application']:
            del _targets[key]
    completed = ('Entered text'+summary[len('Enter text'):] if operation=='set_text'
                 else 'Activated'+summary[len('Activate'):])
    return {'ok':bool(succeeded), 'summary':completed+'.' if succeeded else 'The application refused that control action.',
            'data':{'application':arguments['application'],'verify':'Inspect the application again to verify the requested outcome.'}}


def schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
STRING={'type':'string'}
TOOLS={
 'desktop_applications':('Find an open application by the friendly name the person used (for example Weather), using Linux accessibility. Use this fallback when an app has no broker actions. Returned IDs are short-lived and cannot be guessed.',schema({'name':STRING},['name']),desktop_applications),
 'inspect_application':('Read an open application’s visible accessibility controls, current text and actual action names. Protected fields are excluded. Inspect again after every change; targets are short-lived.',schema({'application':STRING},['application']),inspect_application),
 'act_in_application':('Operate a visible application control using an exact target from inspect_application. Use set_text to replace an editable field, or action with an advertised action name to activate a button/result. Ari asks before changes. Inspect afterward and verify the requested outcome; activation alone does not mean the whole task succeeded.',schema({'application':STRING,'target':STRING,'operation':{'type':'string','enum':['set_text','action']},'text':STRING,'action':STRING,'preview':{'type':'boolean'}},['application','target','operation']),act_in_application),
}
if __name__=='__main__':
    serve('ari-accessibility',TOOLS)
