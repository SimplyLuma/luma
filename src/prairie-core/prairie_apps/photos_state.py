# SPDX-License-Identifier: Apache-2.0
"""Photos state and operations, independent of GTK and visual kit parts."""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import os
from pathlib import Path
from .photos_adjustments import AdjustmentStore, AUTO, changed_settings
from .photos_backend import PhotoLibrary
from .photos_fixture import FixtureSource
from .photos_data import groups_for, photo_labels

PAGE_SIZE = 200


@dataclass(frozen=True)
class Page:
    records: tuple
    total: int
    albums: tuple
    library_count: int
    groups: tuple
    now: datetime
    places: int = 0
    month: str = ""
    years: tuple = ()
    counts: dict = field(default_factory=dict)
    covers: dict = field(default_factory=dict)


class PhotosState:
    def __init__(self, source, *, now=None):
        self.source = source
        self.fixture = source if isinstance(source, FixtureSource) else None
        self.now = now or (self.fixture.now if self.fixture else datetime.now(timezone.utc))
        self.view = 'library'
        self.zoom = 'days'
        self.query = ''
        self.size = 168
        self.viewer = None
        self.info = False
        self.editing = False
        self.edit_mode = 'light'
        self.draft = {}
        self.baseline = {}
        self.adjustments = {}
        self.records = ()
        self.total = 0
        self.page_offset = 0
        self.albums = ()
        self.library_count = 0
        self.place_count = 0
        self.month = ""
        self.years = ()
        self.counts = {}
        self.covers = {}
        self.store = None if self.fixture else AdjustmentStore(source.database)
        self._undo = None

    def query_snapshot(self):
        return self.view,self.query,self.page_offset,self.zoom

    def read_page(self, *, cancelled=None, snapshot=None):
        """Run in a worker; filter in the catalog before the bounded page limit."""
        view,query,offset,zoom=snapshot or self.query_snapshot()
        album_id = None
        collection = {'fav':'favorites','deleted':'deleted'}.get(view,'all')
        albums = self.source.albums()
        if self.fixture:
            collection = view
        elif view not in ('library','fav','people','places','deleted'):
            album_id = next((a.id for a in albums if a.id == view or a.name.casefold() ==
                             {'walls':'wallpapers','launch':'launch'}.get(view,'')), None)
            if album_id is None:
                return Page((),0,albums,self.source.collection_counts()['all'],(),self.now)
        summary={'include_years':zoom=='years'}
        metadata={} if self.fixture else {'summary':summary}
        records,total = self.source.page(query=query,collection=collection,album_id=album_id,
                                         offset=offset,limit=PAGE_SIZE,cancelled=cancelled,**metadata)
        counts = self.source.collection_counts()
        places=len({p.place for p in records if p.place}) if self.fixture else summary.get('places',0)
        latest=summary.get('latest')
        month=self.now.strftime('%B %Y') if self.fixture else datetime.fromisoformat(latest).strftime('%B %Y') if latest else ''
        years=tuple(self.fixture.document['year_cards']) if self.fixture else summary.get('years',())
        if cancelled and cancelled():
            return Page(records,total,albums,counts['all'],(),self.now,places,month,years,counts)
        covers=self._covers(albums)
        return Page(records,total,albums,counts['all'],groups_for(records,zoom,self.now,self.fixture),self.now,places,month,years,
                    counts,covers)

    def _covers(self,albums):
        """Run in a worker: the first photo of each collection the phone's
        Collections panel lists with a cover (v71: Albums with cover thumbnails)."""
        covers={}
        chosen=dict(self.fixture.document.get('covers',{})) if self.fixture else {}
        wanted=[('fav','favorites',None)]
        if self.fixture:
            wanted+=[(a.id,a.id,None) for a in albums]+[('walls','walls',None)]
        else:
            wanted+=[(a.id,'all',a.id) for a in albums]
        for key,collection,album_id in wanted:
            if key in chosen:
                covers[key]=self.source.asset(str(chosen[key])).path
                continue
            rows,_total=self.source.page(collection=collection,album_id=album_id,offset=0,limit=1)
            if rows:covers[key]=rows[0].path
        return covers

    def apply_page(self,page):
        self.records,self.total,self.albums,self.library_count = page.records,page.total,page.albums,page.library_count
        self.place_count,self.month,self.years=page.places,page.month,page.years
        self.counts,self.covers=dict(page.counts),dict(page.covers)
        if self.viewer is not None and self.viewer not in {r.id for r in self.records}:
            self.close_viewer()

    def labels(self,record):
        return photo_labels(record,self.now,self.fixture)

    @property
    def current(self):
        return next((p for p in self.records if p.id == self.viewer),None)

    def switch_view(self,view):
        self.view = view
        self.page_offset = 0
        self.close_viewer()

    def open_photo(self,identifier):
        if identifier not in {p.id for p in self.records}:
            raise KeyError(identifier)
        self.viewer = identifier
        self.editing = False

    def close_viewer(self):
        self.viewer = None
        self.info = False
        self.editing = False
        self.draft = {}

    def step_destination(self,delta):
        ids=[p.id for p in self.records]
        if self.viewer not in ids:return None
        index=self.page_offset+ids.index(self.viewer)+delta
        if not 0 <= index < self.total:return None
        offset=(index//PAGE_SIZE)*PAGE_SIZE
        return offset,index-offset

    def step(self,delta):
        ids = [p.id for p in self.records]
        if self.viewer not in ids:
            return False
        index = ids.index(self.viewer) + delta
        if not 0 <= index < len(ids):
            return False
        self.viewer = ids[index]
        self.editing = False
        return True

    def load_adjustments(self,identifier):
        values = self.fixture.adjustments(identifier) if self.fixture else self.store.load(identifier)
        self.adjustments[identifier] = values
        return dict(values)

    def begin_edit(self):
        if self.current is None:
            return False
        self.baseline = dict(self.adjustments.get(self.viewer, {}))
        self.draft = dict(self.baseline)
        self.editing = True
        return True

    def auto_adjust(self):
        if not self.editing:
            raise ValueError('No photo is being edited')
        self.draft.update(AUTO)

    def revert_edit(self):
        if not self.editing:
            raise ValueError('No photo is being edited')
        # Only the supported adjustment fields are edited. Future metadata is
        # retained by changed_settings and the catalog read-modify-write save.
        from .photos_adjustments import KEYS
        self.draft={key:value for key,value in self.draft.items() if key not in KEYS}

    def cancel_edit(self):
        self.draft = dict(self.baseline)
        self.editing = False

    def save_edit(self,identifier,before,after):
        """Run in a worker, applying only changes made by this editor."""
        changes = changed_settings(before,after)
        values = (self.fixture.update_adjustments(identifier,changes) if self.fixture
                  else self.store.update(identifier,changes))
        return values

    def set_favourite(self,identifier,active):
        self.source.set_favorite(identifier,active)
        self.records = tuple(replace(p,favorite=bool(active)) if p.id==identifier else p for p in self.records)

    def trash(self,identifier):
        record = self.source.asset(identifier)
        copies = tuple(copy.id for copy in record.copies if not copy.trashed)
        result = self.source.trash_copies(copies)
        # Retain only copies moved by this operation, including partial success.
        moved=tuple(copy.id for copy in self.source.asset(identifier).copies if copy.id in copies and copy.trashed)
        self._undo = (identifier,moved) if moved else None
        return result

    def undo_trash(self):
        if self._undo is None:
            return None
        identifier,copies = self._undo
        result = self.source.restore_copies(copies)
        remaining=tuple(copy.id for copy in self.source.asset(identifier).copies if copy.id in copies and copy.trashed)
        self._undo = (identifier,remaining) if remaining else None
        if result.errors:
            raise OSError('; '.join(result.errors))
        return identifier


def source_from_environment():
    """Call in a worker. Fixture path bypasses every real catalog/cache path."""
    fixture = os.environ.get('LUMA_PHOTOS_FIXTURE')
    return FixtureSource(Path(fixture)) if fixture else PhotoLibrary()
