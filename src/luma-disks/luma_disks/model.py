# SPDX-License-Identifier: MPL-2.0
"""GTK-free data presented by Disks. Fixture data never opens the system bus."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .backend import Disk, Volume, size_text


GB = 1_000_000_000


def display_size(value: int | None) -> str:
    """v70 Disks' intentionally coarse GB/TB labels."""
    if value is None:
        return 'Not measured'
    gb = max(0, value) / GB
    if gb >= 1000:
        tb = gb / 1000
        return f'{tb:.1f}'.removesuffix('.0') + ' TB'
    if gb >= 1:
        return f'{math.floor(gb + .5)} GB'
    return f'{math.floor(gb * 1000 + .5)} MB'


@dataclass(frozen=True)
class VolumeView:
    key: str
    name: str
    tone: str
    size: int
    used: int | None
    free: int | None
    format: str
    mount: str | None
    device: str
    uuid: str
    role: str
    locked: bool = False
    protected: bool = False
    read_only: bool = False
    source: Volume | None = None
    encrypted: bool = False
    free_offset: int | None = None


@dataclass(frozen=True)
class DriveView:
    key: str
    name: str
    group: str
    kind: str
    model: str
    device: str
    size: int
    scheme: str
    health_state: str
    health_title: str
    health_rows: tuple[tuple[str, str, bool], ...]
    volumes: tuple[VolumeView, ...]
    speed_read: int | None = None
    speed_write: int | None = None
    source: Disk | None = None
    free_areas: tuple[tuple[int, int], ...] = ()

    @property
    def unallocated(self) -> int:
        if self.source is not None:
            return sum(size for _offset, size in self.free_areas)
        return max(0, self.size - sum(v.size for v in self.volumes))

    @property
    def used(self) -> int | None:
        values = [v.used for v in self.volumes if not v.locked]
        return sum(values) if all(x is not None for x in values) else None


def fixture_drives(path: str | Path) -> tuple[DriveView, ...]:
    """Read exactly v70's three sample drives, without creating a UDisks client."""
    data = json.loads(Path(path).read_text())
    result = []
    for drive in data['drives']:
        volumes = tuple(VolumeView(
            key=f"{drive['id']}:{i}", name=v['n'], tone=v['c'], size=round(v['size'] * GB),
            used=round(v['used'] * GB) if not v.get('locked') else None,
            free=round((v['size'] - v['used']) * GB) if not v.get('locked') else None,
            format=v['fs'], mount=v['mount'], device=drive['dev'] + v['part'], uuid=v['uuid'],
            role=v['role'], locked=v.get('locked', False), protected=v.get('sys', False),
            read_only=v['fs'] == 'ISO 9660', encrypted=v.get('luks', False)) for i, v in enumerate(drive['vols']))
        rows = tuple((str(a), str(b), len(row) > 2 and row[2] == 'warn')
                     for row in drive['health'][2] for a, b in [row[:2]])
        result.append(DriveView(
            key=drive['id'], name=drive['n'], group=drive['grp'], kind=drive['kind'],
            model=drive['model'], device=drive['dev'], size=round(drive['size'] * GB),
            scheme=drive['scheme'], health_state=drive['health'][0],
            health_title=drive['health'][1], health_rows=rows, volumes=volumes,
            speed_read=drive['speed'][0], speed_write=drive['speed'][1]))
    return tuple(result)


def live_drives(disks: tuple[Disk, ...]) -> tuple[DriveView, ...]:
    """Use only facts UDisks and statvfs actually supplied; no sample values leak."""
    result = []
    tones = ('blue', 'violet', 'amber', 'green')
    for disk in disks:
        mib = 1024 * 1024
        cursor = mib
        free_areas = []
        if disk.scheme != 'No partition table':
            for volume in sorted(disk.volumes, key=lambda item: item.offset):
                stop = volume.offset // mib * mib
                if stop > cursor:
                    free_areas.append((cursor, stop - cursor))
                cursor = (volume.offset + volume.size + mib - 1) // mib * mib
            end = max(cursor, (disk.size - mib) // mib * mib)
            if end > cursor:
                free_areas.append((cursor, end - cursor))
        volumes = tuple(VolumeView(
            key=v.path, name=v.name, tone=tones[i % len(tones)], size=v.size,
            used=v.used, free=v.free, format=v.format, mount=v.mounts[0] if v.mounts else None,
            device=v.device, uuid=v.uuid, role=v.role, protected=v.protected,
            read_only=v.read_only, source=v, encrypted=v.encrypted,
            locked=v.locked) for i, v in enumerate(disk.volumes))
        health = disk.health
        rows = []
        for title, value in (
            ('Powered on', f'{health.powered_hours:,} hours' if health.powered_hours is not None else None),
            ('Temperature', f'{health.temperature:.0f} °C' if health.temperature is not None else None),
            ('Written', size_text(health.written) if health.written is not None else None),
            ('Spare blocks', f'{health.spare}%' if health.spare is not None else None),
        ):
            if value is not None:
                rows.append((title, value, False))
        result.append(DriveView(
            key=disk.block, name=disk.name, group=disk.group,
            kind='iso' if disk.group == 'Disk images' else 'usb' if disk.group == 'Plugged in' else 'ssd',
            model=disk.model, device=disk.device, size=disk.size,
            scheme={'GUID partition table': 'GPT', 'Master boot record': 'MBR'}.get(
                disk.scheme, disk.scheme),
            health_state=health.state, health_title=health.title, health_rows=tuple(rows),
            volumes=volumes, source=disk, free_areas=tuple(free_areas)))
    return tuple(result)


def volume_rows(drive: DriveView) -> tuple[VolumeView, ...]:
    """v70 appends significant free space to the readable volume list."""
    if drive.source is not None:
        rows = list(drive.volumes)
        for offset, size in drive.free_areas:
            if size > GB // 2:
                rows.append(VolumeView(
                    key=f'{drive.key}:free:{offset}', name='Free space', tone='free',
                    size=size, used=None, free=size, format='', mount=None,
                    device=drive.device, uuid='', role='Unallocated space on this drive.',
                    free_offset=offset))
        return tuple(sorted(rows, key=lambda view: view.free_offset if view.free_offset is not None
                            else view.source.offset if view.source else 0))
    remaining = drive.unallocated
    if remaining <= GB // 2:
        return drive.volumes
    return drive.volumes + (VolumeView(
        key=f'{drive.key}:free', name='Free space', tone='free', size=remaining,
        used=None, free=remaining, format='', mount=None, device=drive.device, uuid='',
        role='Unallocated space on this drive.'),)


def map_weights(volumes: tuple[VolumeView, ...], total: int) -> tuple[float, ...]:
    """v70 keeps every volume at least 4% wide, then normalizes to one track."""
    if not volumes:
        return ()
    weights = [max(v.size / max(1, total), .04) for v in volumes]
    used = sum(weights)
    return tuple(weight / used for weight in weights)
