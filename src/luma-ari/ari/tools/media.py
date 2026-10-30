# SPDX-License-Identifier: Apache-2.0
"""Direct owner-side library playback, without navigating application controls."""
import json
from pathlib import Path
import sys
import time
import unicodedata

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gi.repository import Gio, GLib
from ari.mcp import serve

# Concrete adapters only: an arbitrary application name cannot become a bus
# destination. Other players retain the existing MPRIS/accessibility paths.
OWNERS = {'tide': 'org.projectluma.Tide'}
INTERFACE = 'org.projectluma.MediaLibrary1'
PATH = '/org/projectluma/MediaLibrary'
_prepared = {}


def _normal(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _call(bus, owner, method, value=None):
    reply = bus.call_sync(owner, PATH, INTERFACE, method,
        GLib.Variant('(s)', (value,)) if value is not None else None,
        GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NO_AUTO_START, 2500, None).unpack()[0]
    if len(reply) > 100_000:
        raise ValueError('The media application returned too much data.')
    result = json.loads(reply)
    if not isinstance(result, dict): raise ValueError('Invalid media application reply.')
    return result


def _search(bus, owner, title):
    # GApplication owns its bus name before do_startup finishes exporting
    # optional interfaces. Retry only this read, never the Play mutation.
    deadline = time.monotonic()+1.5
    while True:
        try:
            return _call(bus, owner, 'Search', title)
        except GLib.Error as error:
            if (Gio.DBusError.get_remote_error(error) not in (
                    'org.freedesktop.DBus.Error.UnknownObject', 'org.freedesktop.DBus.Error.UnknownInterface',
                    'org.freedesktop.DBus.Error.UnknownMethod') or time.monotonic() >= deadline):
                raise
            time.sleep(.08)


def play_track(arguments):
    app = str(arguments.get('application', '')).casefold()
    title = str(arguments.get('title', '')).strip()
    artist = str(arguments.get('artist', '')).strip()
    album = str(arguments.get('album', '')).strip()
    if app not in OWNERS or not title or max(map(len, (title, artist, album))) > 200:
        return {'ok': False, 'summary': 'Name a track title and a supported player (Tide).'}
    key = (app, title, artist, album)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        owner = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'GetNameOwner', GLib.Variant('(s)', (OWNERS[app],)), GLib.VariantType.new('(s)'),
            Gio.DBusCallFlags.NO_AUTO_START, 1000, None).unpack()[0]
    except GLib.Error:
        return {'ok': False, 'needs_open': True, 'summary': 'Tide is not running.'}
    try:
        now = time.monotonic()
        for old in list(_prepared):
            if _prepared[old]['expires'] <= now: del _prepared[old]
        prepared = _prepared.get(key)
        if arguments.get('preview') or prepared is None:
            response = _search(bus, owner, title)
            if not response.get('ok'):
                return {'ok': False, 'summary': response.get('summary', 'Tide could not search its library.')}
            matches = [row for row in response.get('tracks', [])
                       if _normal(row['title']) == _normal(title)
                       and (not artist or _normal(row['artist']) == _normal(artist))
                       and (not album or _normal(row['album']) == _normal(album))]
            if response.get('truncated') or len(matches) != 1:
                return {'ok': False, 'untrusted': True,
                        'summary': ('Several recordings match. Specify the artist and album.' if matches else
                                    'No exact track match in Tide. Check the title, artist or album.'),
                        'data': {'matches': [{k: row[k] for k in ('title', 'artist', 'album')} for row in matches[:10]]}}
            # Retain the querying connection through the approval boundary.
            # Owner targets are bound to its unique sender, not just our PID.
            prepared = dict(owner=owner, connection=bus, track=matches[0], expires=now+120)
            if len(_prepared) >= 128: _prepared.pop(next(iter(_prepared)))
            _prepared[key] = prepared
        if prepared['owner'] != owner:
            _prepared.pop(key, None)
            return {'ok': False, 'summary': 'Tide restarted after the search. Ask me to play the track again.'}
        track = prepared['track']
        if arguments.get('preview'):
            return {'ok': True, 'preview': f'Play {track["title"]} by {track["artist"]} in Tide',
                    'detail': f'Album: {track["album"]}. Use Tide’s library and existing audio controller.',
                    'application_name': 'Tide', 'requested_name': 'tide', 'application_scope': 'media:'+owner}
        _prepared.pop(key, None)
        result = _call(bus, owner, 'Play', track['target'])
        deadline = time.monotonic()+2
        while result.get('ok') and result.get('state') not in ('playing', 'error') and time.monotonic() < deadline:
            time.sleep(.08)
            result = _call(bus, owner, 'Status')
        actual = result.get('track') or {}
        if not result.get('ok') or actual.get('id') != track['id'] or result.get('state') == 'error':
            return {'ok': False, 'summary': result.get('summary') or 'Tide did not confirm playback of the selected track.'}
        playing = result.get('state') == 'playing'
        return {'ok': True, 'verified': playing, 'completed': playing, 'verification_required': not playing,
                'summary': (f'Playing “{track["title"]}” by {track["artist"]} in Tide.' if playing else
                            f'Requested “{track["title"]}” in Tide; playback is not yet confirmed.'),
                'untrusted': True, 'data': {'state': result.get('state'),
                    'track': {k: track[k] for k in ('title', 'artist', 'album')}}}
    except GLib.Error:
        return {'ok': False, 'summary': 'This Tide instance does not provide the direct music interface, or did not respond. No UI steps were attempted.'}
    except (ValueError, TypeError, KeyError):
        return {'ok': False, 'summary': 'Tide returned an invalid music result. Playback is not confirmed.'}


TOOLS = {'play_track': ('Play an exact named track using the application’s own library and audio controller, without UI navigation. Currently supports Tide. Optional artist/album disambiguate recordings. Never picks the first ambiguous result. Reports actual playback state.',
    {'type': 'object', 'properties': {'application': {'type': 'string', 'enum': ['tide']},
       **{key: {'type': 'string'} for key in ('title', 'artist', 'album')}, 'preview': {'type': 'boolean'}},
     'required': ['application', 'title'], 'additionalProperties': False}, play_track)}
if __name__ == '__main__': serve('ari-media', TOOLS)
