# SPDX-License-Identifier: Apache-2.0
"""LUMA_PHOTOS_FIXTURE=<json>: isolated, in-memory Photos sample data.

Only the named JSON is read. Image paths resolve against
LUMA_PHOTOS_FIXTURE_ASSETS (Studio's root), or the JSON's parent. This class
never instantiates PhotoLibrary, creates caches, scans folders, writes files,
or opens another app's store. Changes last only for this process.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path

from .photos_adjustments import apply_changes
from .photos_backend import AlbumRecord, MediaCopy, PhotoRecord, SourceRecord, TransferResult


class FixtureSource:
    read_only = True

    def __init__(self, path: Path, *, assets: Path | None = None) -> None:
        self.path = Path(path)
        self.document = json.loads(self.path.read_text(encoding='utf-8'))
        root = Path(assets or os.environ.get('LUMA_PHOTOS_FIXTURE_ASSETS') or self.path.parent)
        self.asset_root = root.resolve()
        self.now = datetime.fromisoformat(self.document['now'])
        self._records = {}
        self._labels = {}
        self._adjustments = {}
        self._album_ids = {}
        self._album_names = {}
        self._new_ids = itertools.count(1)
        for raw in self.document['photos']:
            identifier = str(raw['id'])
            if identifier in self._records:
                raise ValueError(f'Duplicate fixture photo: {identifier}')
            path = (self.asset_root / raw['image']).resolve()
            if not path.is_relative_to(self.asset_root):
                raise ValueError('Fixture image must be within its asset root')
            moment = datetime.strptime(raw['date'] + ' ' + raw['time'], '%Y-%m-%d %I:%M %p').replace(tzinfo=timezone.utc)
            copy = MediaCopy(f'fixture-copy-{identifier}', identifier, 'fixture', path, path.as_uri(),
                             True, 0, moment, 'image/png' if raw['wallpaper'] else 'image/webp')
            record = PhotoRecord(path, moment, 0, id=identifier, display_name=raw['title'],
                                 mime_type=copy.mime_type, captured=moment, favorite=raw['favourite'],
                                 place=raw['place'], copies=(copy,))
            self._records[identifier] = record
            self._labels[identifier] = dict(raw)
        for album in self.document.get('albums', []):
            self._album_names[album['id']] = album['name']
            members = set(map(str, album['photos']))
            if not members <= self._records.keys():
                raise ValueError('Fixture album names an unknown photo')
            self._album_ids[album['id']] = members
        self.sync = self.document.get('sync', '')

    def labels(self, identifier: str) -> dict:
        return dict(self._labels[identifier])

    def rows(self, view: str = 'library', query: str = '') -> tuple[PhotoRecord, ...]:
        rows = [p for p in self._records.values() if p.deleted == (view == 'deleted')]
        if view == 'fav':
            rows = [p for p in rows if p.favorite]
        elif view == 'walls':
            rows = [p for p in rows if self._labels[p.id]['wallpaper']]
        elif view == 'launch' or view.startswith('al_'):
            members = self._album_ids.get(view, set())
            rows = [p for p in rows if p.id in members]
        if query:
            folded = query.casefold()
            rows = [p for p in rows if folded in (p.display_name + ' ' + p.place).casefold()]
        return tuple(rows)

    def assets(self, *, query='', source_id=None, album_id=None, deleted=False, asset_ids=None):
        view = 'deleted' if deleted else album_id or 'library'
        rows = self.rows(view, query)
        return tuple(p for p in rows if asset_ids is None or p.id in asset_ids)

    def asset(self, asset_id):
        return self._records[asset_id]

    def page(self, *, query='', collection='all', source_id=None, album_id=None,
             offset=0, limit=200, cancelled=None):
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError('Invalid photo page bounds')
        if cancelled and cancelled():
            return (), 0
        view = {'all':'library', 'favorites':'fav', 'deleted':'deleted'}.get(collection, collection)
        rows = self.rows(album_id or view, query)
        if collection == 'videos':
            rows = tuple(p for p in rows if p.is_video)
        elif collection == 'edited':
            rows = tuple(p for p in rows if p.edited)
        return rows[offset:offset + limit], len(rows)

    def collection_counts(self):
        records = self.rows()
        return {'all':len(records),'favorites':len(self.rows('fav')), 'deleted':len(self.rows('deleted')),
                'videos':0,'edited':sum(p.edited for p in records),'recent':len(records)}

    def page_offset_for_path(self, path, limit=200):
        return next(((i // limit) * limit for i, p in enumerate(self.rows()) if p.path == Path(path)), None)

    def scan_all(self, cancelled=None):
        return ()  # No filesystem scan, including the fixture's image directory.

    def sources(self):
        # Non-reachable source prevents a live directory monitor. Photo copies
        # resolve individually to fixture images for display.
        return (SourceRecord('fixture','This computer',self.asset_root,'fixture',False,self.now,len(self.rows())),)

    def albums(self):
        return tuple(AlbumRecord(key,name,self.now,sum(not self._records[i].deleted for i in self._album_ids[key]))
                     for key,name in self._album_names.items())

    def create_album(self, name):
        name = name.strip()
        if not name:
            raise ValueError('Enter an album name')
        key = f'al_fixture_{next(self._new_ids)}'
        self._album_names[key] = name
        self._album_ids[key] = set()
        return AlbumRecord(key, name, self.now, 0)

    def add_to_album(self, album_id, asset_ids):
        members = set(asset_ids)
        if not members <= self._records.keys():
            raise KeyError('Unknown fixture photo')
        self._album_ids[album_id].update(members)

    def set_favorite(self, asset_id, favorite):
        self._records[asset_id] = replace(self.asset(asset_id), favorite=bool(favorite))

    def set_metadata(self, asset_id, *, caption=None, place=None):
        record = self.asset(asset_id)
        self._records[asset_id] = replace(record, caption=record.caption if caption is None else caption,
                                          place=record.place if place is None else place)

    def adjustments(self, asset_id):
        self.asset(asset_id)
        return dict(self._adjustments.get(asset_id, {}))

    def update_adjustments(self, asset_id, changes):
        values = apply_changes(self.adjustments(asset_id), changes)
        self._adjustments[asset_id] = values
        self._records[asset_id] = replace(self.asset(asset_id), edited=bool(values))
        return dict(values)

    def trash_copies(self, copy_ids):
        ids = set(copy_ids)
        changed = []
        for identifier, photo in self._records.items():
            if any(copy.id in ids for copy in photo.copies):
                copies = tuple(replace(copy, trashed=True, deleted_at=self.now) if copy.id in ids else copy
                               for copy in photo.copies)
                self._records[identifier] = replace(photo, deleted=True, copies=copies)
                changed.append(photo.path)
        return TransferResult(tuple(changed))

    def restore_copies(self, copy_ids):
        ids = set(copy_ids)
        changed = []
        for identifier, photo in self._records.items():
            if any(copy.id in ids for copy in photo.copies):
                copies = tuple(replace(copy, trashed=False, deleted_at=None) if copy.id in ids else copy
                               for copy in photo.copies)
                self._records[identifier] = replace(photo, deleted=False, copies=copies)
                changed.append(photo.path)
        return TransferResult(tuple(changed))

    def import_files(self, *args, **kwargs):
        raise PermissionError('Fixture mode cannot import files')

    def export_assets(self, *args, **kwargs):
        raise PermissionError('Fixture mode cannot export files')

    def add_source(self, *args, **kwargs):
        raise PermissionError('Fixture mode cannot add a real source')

    def delete_copies_permanently(self, *args, **kwargs):
        raise PermissionError('Fixture mode cannot delete files')


class FixtureThumbnailCache:
    """Existing fixture files only; no cache directory or thumbnail writes."""
    @staticmethod
    def lookup(copy):
        return copy.path
