# SPDX-License-Identifier: Apache-2.0
"""Connect-owned, bounded collaboration transport for approved app sandboxes.

The host device token never crosses D-Bus. App identity uses the same maintained
UID/PID/Flatpak-instance verifier as the app-data migration owner. A caller can
select a document operation, never a host path, URL, header, or bearer token.
"""
import json
import os
from pathlib import Path
import re
import threading

APPS = {'org.projectluma.Notes': {'note'}, 'org.projectluma.Tasks': {'task', 'list'},
        'org.projectluma.Contacts': set()}
PREFIX = '/api/hub/sync/collaboration'
DOCUMENT = re.compile(r'^/documents/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/(invite|accept|revoke|content|comments|remove|restore|transfer))?$')


class Refused(ValueError): pass


def route(app, method, path, body):
    if app not in APPS or method not in ('GET', 'POST') or not isinstance(path, str):
        raise Refused('This application has no approved collaboration operation.')
    if '#' in path or '?' in path or '%' in path or len(path) > 240:
        raise Refused('Invalid collaboration route.')
    if not isinstance(body, dict) or len(json.dumps(body, allow_nan=False).encode()) > 1048576:
        raise Refused('Invalid collaboration request.')
    if method == 'GET' and body:
        raise Refused('Read operations cannot have a payload.')
    if method == 'GET' and re.fullmatch(r'/api/hub/sync/people/[a-z0-9][a-z0-9_.-]{0,63}', path):
        return None
    if method == 'GET' and path == '/api/hub/sync/chat/accepted': return None
    if method == 'POST' and path == '/api/hub/sync/identity/discover' and app == 'org.projectluma.Contacts': return None
    if not APPS[app] or not path.startswith(PREFIX):
        raise Refused('This application cannot use this route.')
    suffix = path[len(PREFIX):]
    if suffix == '/documents':
        if method == 'POST' and body.get('kind') not in APPS[app]:
            raise Refused('This document type belongs to another application.')
        return None
    match = DOCUMENT.fullmatch(suffix)
    if not match or (method == 'GET') != (match[2] is None):
        raise Refused('Invalid collaboration operation.')
    return match[1]


class CollaborationBroker:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(4)

    def identity(self):
        from prairie_apps.connect_sync import load_identity, device_file, ConnectError
        path = device_file()
        if path.is_symlink() or (path.exists() and path.stat().st_uid != os.getuid()):
            raise ConnectError('The host Connect registration is not owned by this user.')
        identity = load_identity()
        if identity is None: raise ConnectError('Sign in to Luma Connect to collaborate.')
        return identity

    @staticmethod
    def authenticate(connection, sender):
        from luma_installer.app_data_broker import authenticate
        app = authenticate(connection, sender)
        if app not in APPS: raise Refused('This application has no approved collaboration identity.')
        return app

    def context(self, app):
        if app not in APPS: raise Refused('Unknown application.')
        identity = self.identity()
        from prairie_apps.connect_sync import HubClient, hub_url
        # A stale registration file is not current authorization. This runs in
        # the bounded transport worker, never on Connect's main context.
        HubClient(timeout=12).get_json(hub_url(identity.hub) + '/api/hub/sync/account', token=identity.token)
        return {'device_id': identity.device_id, 'hub': identity.hub,
                'name': identity.name, 'registered_at': identity.registered_at}

    def favorites(self, app):
        # The sandbox gets only recipient hints, never the host address book
        # or its phone/email fields. Current account authorization still applies.
        self.context(app)
        from prairie_apps.eds_backend import load_contacts
        return {'handles': sorted({record.handle.casefold() for record in load_contacts()
                                   if record.favourite and record.handle})[:128]}

    def request(self, app, method, path, body):
        from prairie_apps.connect_sync import HubClient, hub_url
        document = route(app, method, path, body)
        try:
            identity = self.identity(); http = HubClient(timeout=12); address = hub_url(identity.hub)
            if document:
                # Permission and type are reread before every operation. The Hub
                # independently checks membership/CAS again within its transaction.
                if path.endswith('/accept'):
                    metadata = http.get_json(address + PREFIX + '/documents', token=identity.token)
                    snapshot = next((item for item in metadata.get('documents', []) if item.get('id') == document), {})
                elif path.endswith('/restore'):
                    snapshot = http.get_json(address + PREFIX + '/documents/' + document + '/removed', token=identity.token)
                else:
                    snapshot = http.get_json(address + PREFIX + '/documents/' + document, token=identity.token)
                if snapshot.get('kind') not in APPS[app]: raise Refused('This document belongs to another application.')
            result = (http.get_json(address + path, token=identity.token) if method == 'GET'
                      else http.post_json(address + path, body, token=identity.token))
            if path == PREFIX + '/documents' and method == 'GET':
                result['documents'] = [item for item in result.get('documents', []) if item.get('kind') in APPS[app]]
            if len(json.dumps(result, allow_nan=False).encode()) > 2097152:
                raise Refused('The collaboration response is too large.')
            return result
        finally: pass

    def dispatch(self, connection, sender, method, values, invocation, GLib):
        try:
            app = self.authenticate(connection, sender)
            context = method == 'GetCollaborationContext'
            favorites = method == 'GetCollaborationFavorites'
            if context or favorites:
                if values: raise Refused('Context has no input.')
            else:
                if method != 'CollaborationRequest': raise Refused('Unknown operation.')
                verb, path, raw = values
                if len(raw.encode()) > 1048576: raise Refused('The request is too large.')
                body = json.loads(raw)
                route(app, verb, path, body)
        except Exception:
            invocation.return_dbus_error('org.projectluma.Connect1.Error.Refused', 'This collaboration request was refused.'); return
        if not self.slots.acquire(blocking=False):
            invocation.return_dbus_error('org.projectluma.Connect1.Error.Busy', 'Connect is busy. Try again shortly.'); return
        def work():
            from prairie_apps.connect_sync import HubResponseError
            try:
                reply = self.context(app) if context else (self.favorites(app) if favorites else {'result': self.request(app, verb, path, body)})
            except HubResponseError as error:
                reply = {'error': {'status': error.status, 'detail': error.detail,
                                   'retry_after': error.retry_after, 'code': error.code}}
            except Exception: reply = {'error': {'status': 503, 'detail': 'Connect collaboration is unavailable.'}}
            finally: self.slots.release()
            GLib.idle_add(finish, reply)
        def finish(reply):
            invocation.return_value(GLib.Variant('(s)', (json.dumps(reply, allow_nan=False),))); return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=False, name='connect-collaboration').start()
