# SPDX-License-Identifier: Apache-2.0
"""GTK-free view data and geometry for Photos' v70 composition."""
from dataclasses import dataclass
from datetime import datetime
from math import floor
from .photos_backend import PhotoRecord

NAVIGATION = (('library','Library','image'),('fav','Favorites','heart'),('people','People','users'),
              ('places','Places','map-pin'),('launch','Launch','star'),('walls','Wallpapers','grid-2x2'),
              ('deleted','Deleted','trash-2'))


@dataclass(frozen=True)
class PhotoGroup:
    title: str
    subtitle: str
    records: tuple[PhotoRecord, ...]


def tile_layout(width: float, minimum_side: float, count: int, gap: float = 3) -> tuple[int, float, float]:
    """CSS auto-fill minmax: empty cells retain the full grid's column widths."""
    columns = max(1, floor((max(0, width) + gap) / (minimum_side + gap)))
    side = max(0, (width - gap * (columns - 1)) / columns)
    rows = (count + columns - 1) // columns
    return columns, side, rows * side + max(0, rows - 1) * gap


def outer_corners(index: int, columns: int, count: int) -> tuple[bool, bool, bool, bool]:
    """v70 #8: tl, tr, br, bl from actual neighbours, including a short last row."""
    if columns < 1 or not 0 <= index < count:
        raise ValueError('Invalid photo grid position')
    left = index % columns > 0
    right = index % columns < columns - 1 and index + 1 < count
    above = index >= columns
    below = index + columns < count
    return not above and not left, not above and not right, not below and not right, not below and not left


def photo_labels(record: PhotoRecord, now: datetime, fixture=None) -> dict[str, str]:
    if fixture is not None:
        return fixture.labels(record.id)
    moment = record.captured or record.modified
    day = 'Today' if moment.date() == now.date() else moment.strftime('%a') if 0 < (now.date()-moment.date()).days < 7 else moment.strftime('%b %-d')
    return {'day':day, 'day_subtitle':moment.strftime('%A, %B %-d'),
            'time':moment.strftime('%-I:%M %p'), 'date':moment.date().isoformat(),
            'stored': 'This computer' if any(c.reachable for c in record.copies) else 'Out of reach'}


def groups_for(records, zoom: str, now: datetime, fixture=None) -> tuple[PhotoGroup, ...]:
    records = tuple(records)
    if zoom == 'all':
        return (PhotoGroup('', '', records),) if records else ()
    groups = {}
    for record in records:
        labels = photo_labels(record, now, fixture)
        key = labels['date'][:7] if zoom == 'months' else labels['date']
        groups.setdefault(key, []).append(record)
    result = []
    for key, items in groups.items():
        labels = photo_labels(items[0], now, fixture)
        if zoom == 'months':
            title = datetime.fromisoformat(key + '-01').strftime('%B')
            subtitle = f'{len(items)} photos'
        else:
            title = labels['day']
            places = list(dict.fromkeys(p.place for p in items if p.place))[:2]
            subtitle = labels['day_subtitle'] + (' · ' + ' and '.join(places) if places else '')
        result.append(PhotoGroup(title, subtitle, tuple(items)))
    return tuple(result)


@dataclass(frozen=True)
class Memory:
    """A few photos worth seeing again, made from where and when they were taken (v71 Photos: Memories)."""
    title: str
    when: str
    place: str
    records: tuple[PhotoRecord, ...]


def _relative_day(day, today) -> str:
    days = (today - day).days
    if days <= 0:
        return 'Today'
    if days == 1:
        return 'Yesterday'
    if days == 2:
        return 'Two days ago'
    if days < 7:
        return f'{days} days ago'
    return day.strftime('%b %-d')


def memories_for(records, now: datetime, fixture=None, *, limit: int = 4) -> tuple[Memory, ...]:
    """The Memories row: the fixture names v71's four; a real library groups
    its photos by place and day (two or more photos, newest first)."""
    records = tuple(p for p in records if not p.deleted)
    if fixture is not None:
        result = []
        for raw in fixture.document.get('memories', []):
            chosen = tuple(p for p in records if p.place == raw['place'])
            if chosen:
                result.append(Memory(raw['title'], raw['when'], raw['place'], chosen))
        return tuple(result[:limit])
    groups: dict = {}
    for record in records:
        if not record.place:
            continue
        moment = record.captured or record.modified
        groups.setdefault((record.place, moment.date()), []).append(record)
    result = []
    today = now.date()
    for (place, day), items in sorted(groups.items(), key=lambda item: item[0][1], reverse=True):
        if len(items) < 2:
            continue
        days = (today - day).days
        title = f'{day.strftime("%A")} in {place}' if 1 < days < 7 else place
        result.append(Memory(title, _relative_day(day, today), place, tuple(items)))
        if len(result) == limit:
            break
    return tuple(result)
