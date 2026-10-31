# SPDX-License-Identifier: Apache-2.0
"""Owner-side music commands. No client receives source URLs or credentials.

The session-bus interface searches this application's library and uses its
existing playback controller. Targets expire, belong to the querying bus
connection, and stop working if the selected track's metadata changes.
"""
from dataclasses import asdict
import hashlib
import json
import secrets
import time

INTERFACE = 'org.projectluma.MediaLibrary1'
PATH = '/org/projectluma/MediaLibrary'
XML = '''<node><interface name="org.projectluma.MediaLibrary1">
<method name="Search"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="Play"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="Status"><arg type="s" direction="out"/></method>
</interface></node>'''


class CommandError(ValueError):
    """An intentionally public command validation message."""


def _record(track):
    return {key: getattr(track, key) for key in ('id', 'title', 'artist', 'album')}


def _revision(track):
    return hashlib.sha256(json.dumps(asdict(track), sort_keys=True).encode()).hexdigest()


class MediaLibrary:
    def __init__(self, connection, store, controller):
        from gi.repository import Gio, GLib
        self.GLib = GLib
        self.connection, self.store, self.controller = connection, store, controller
        self.targets = {}
        self.registration = connection.register_object(PATH,
            Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)

    def search(self, sender, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise CommandError('Give a track title of 1–200 characters.')
        now = time.monotonic()
        self.targets = {key: row for key, row in self.targets.items() if row['expires'] > now}
        if len(self.targets) > 512:
            raise CommandError('Too many outstanding searches. Try again shortly.')
        tracks = self.store.tracks(query=query.strip(), limit=51)
        records = []
        for track in tracks[:50]:
            token = secrets.token_urlsafe(24)
            self.targets[token] = dict(sender=sender, track=track.id, revision=_revision(track), expires=now+120)
            records.append({**_record(track), 'target': token})
        return {'ok': True, 'tracks': records, 'truncated': len(tracks) > 50}

    def play(self, sender, target):
        row = self.targets.pop(target, None)
        if row is None or row['sender'] != sender or row['expires'] <= time.monotonic():
            raise CommandError('Search for this track again before playing it.')
        track = self.store.track(row['track'])
        if _revision(track) != row['revision']:
            raise CommandError('The track changed. Search again before playing it.')
        # This is the same controller/queue used by the UI, MPRIS and audio
        # engine. It resolves any source credentials within the owning app.
        self.controller.replace_queue([track.id], start=0, play=True)
        return self.status()

    def status(self):
        snapshot = self.controller.snapshot
        return {'ok': True, 'state': snapshot.state.value,
                'track': _record(snapshot.track) if snapshot.track else None}

    def _call(self, _connection, sender, _path, _interface, method, parameters, invocation):
        try:
            if method == 'Search': result = self.search(sender, parameters.unpack()[0])
            elif method == 'Play': result = self.play(sender, parameters.unpack()[0])
            elif method == 'Status': result = self.status()
            else: raise CommandError('Unsupported media command.')
        except CommandError as error:
            result = {'ok': False, 'summary': str(error)}
        except Exception:
            result = {'ok': False, 'summary': 'The selected track is unavailable or changed. Search again.'}
        invocation.return_value(self.GLib.Variant('(s)', (json.dumps(result),)))

    def close(self):
        self.targets.clear()
        self.connection.unregister_object(self.registration)
