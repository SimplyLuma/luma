# SPDX-License-Identifier: Apache-2.0
"""GPU activity from DRM fdinfo engine counters.

Every DRM client (an open render or card node) reports cumulative busy time
per engine in /proc/<pid>/fdinfo/<fd>: ``drm-engine-<engine>: <ns> ns``
(i915, amdgpu, msm, panfrost, panthor, v3d) or a cycle pair
``drm-cycles-<engine>`` / ``drm-total-cycles-<engine>`` (xe). A device's
utilisation is the busiest engine class summed over its clients; an app's is
the same over the clients its processes hold. amdgpu's device-wide
``gpu_busy_percent`` and VRAM counters are used when sysfs has them.

A GPU whose driver reports no engine counters (virtio-gpu, older kernels) is
present but unavailable: its utilisation is None, never zero. A machine with
no render node has no GPU to show.
"""
from __future__ import annotations
import os
from pathlib import Path

NAMES = {'i915': 'Intel graphics', 'xe': 'Intel graphics', 'amdgpu': 'AMD graphics', 'radeon': 'AMD graphics',
         'msm': 'Adreno', 'panfrost': 'Mali', 'panthor': 'Mali', 'lima': 'Mali', 'v3d': 'VideoCore',
         'nouveau': 'NVIDIA graphics', 'nvidia': 'NVIDIA graphics', 'nvidia-drm': 'NVIDIA graphics',
         'msm_dpu': 'Adreno', 'msm_mdp': 'Adreno', 'adreno': 'Adreno',
         'virtio_gpu': 'Virtual GPU', 'virtio-pci': 'Virtual GPU', 'vmwgfx': 'Virtual GPU', 'asahi': 'Apple graphics', 'powervr': 'PowerVR',
         'etnaviv': 'Vivante'}
ENGINES = {'render': 'Render', 'gfx': 'Render', 'rcs': 'Render', 'gpu': 'GPU', 'fragment': 'Fragment',
           'vertex-tiler': 'Vertex', 'copy': 'Copy', 'bcs': 'Copy', 'dma': 'Copy', 'video': 'Video',
           'vcs': 'Video', 'dec': 'Video decode', 'enc': 'Video encode', 'jpeg': 'JPEG',
           'video-enhance': 'Video enhance', 'vecs': 'Video enhance', 'compute': 'Compute', 'ccs': 'Compute'}
UNITS = {'': 1, 'B': 1, 'KiB': 1024, 'MiB': 1024 ** 2, 'GiB': 1024 ** 3}


def engine_name(engine):
    return ENGINES.get(engine, engine.replace('_', ' ').replace('-', ' ').capitalize())


def _size(value):
    parts = value.split()
    try:
        return int(parts[0]) * UNITS[parts[1] if len(parts) > 1 else '']
    except (IndexError, KeyError, ValueError):
        return None


def parse_fdinfo(text):
    """One DRM client's counters, or None when the fd is not a DRM client."""
    client = {'driver': None, 'id': None, 'pdev': None, 'busy': {}, 'cycles': {}, 'total_cycles': {},
              'capacity': {}, 'memory': None}
    resident = memory = None
    for line in text.splitlines():
        key, separator, value = line.partition(':')
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        try:
            if key == 'drm-driver':
                client['driver'] = value
            elif key == 'drm-client-id':
                client['id'] = int(value)
            elif key == 'drm-pdev':
                client['pdev'] = value
            elif key.startswith('drm-engine-capacity-'):
                client['capacity'][key[20:]] = max(1, int(value))
            elif key.startswith('drm-engine-'):
                client['busy'][key[11:]] = int(value.split()[0])
            elif key.startswith('drm-total-cycles-'):
                client['total_cycles'][key[17:]] = int(value.split()[0])
            elif key.startswith('drm-cycles-'):
                client['cycles'][key[11:]] = int(value.split()[0])
            elif key.startswith('drm-resident-'):
                size = _size(value)
                if size is not None:
                    resident = (resident or 0) + size
            elif key.startswith('drm-memory-'):
                # amdgpu before drm-resident-*: resident buffers by region.
                size = _size(value)
                if size is not None:
                    memory = (memory or 0) + size
        except (IndexError, ValueError):
            continue
    if client['driver'] is None or client['id'] is None:
        return None
    client['memory'] = resident if resident is not None else memory
    return client


def _engine_shares(client, before, elapsed_ns):
    """Percent of each engine class this client kept busy since ``before``."""
    shares = {}
    if before is None or elapsed_ns <= 0:
        return shares
    for engine, busy in client['busy'].items():
        previous = before['busy'].get(engine)
        if previous is None or busy < previous:
            continue
        shares[engine] = 100 * (busy - previous) / elapsed_ns / client['capacity'].get(engine, 1)
    for engine, cycles in client['cycles'].items():
        total, previous, previous_total = (client['total_cycles'].get(engine), before['cycles'].get(engine),
                                           before['total_cycles'].get(engine))
        if None in (total, previous, previous_total) or cycles < previous or total <= previous_total:
            continue
        shares[engine] = 100 * (cycles - previous) / (total - previous_total) / client['capacity'].get(engine, 1)
    return {engine: min(100.0, max(0.0, share)) for engine, share in shares.items()}


def _reports_engines(client):
    return bool(client['busy'] or (client['cycles'] and client['total_cycles']))


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return None


def _number(path):
    try:
        return int(_read(path))
    except (TypeError, ValueError):
        return None


def devices(sys='/sys'):
    """Render-capable DRM devices by sysfs device path. Display-only devices
    (simpledrm, bochs, display controllers) have no render node and no GPU."""
    found = {}
    nodes = {}
    for node in sorted((Path(sys) / 'class/drm').glob('*')):
        if not (node.name.startswith('renderD') or (node.name.startswith('card') and node.name[4:].isdigit())):
            continue
        try:
            device = (node / 'device').resolve(strict=True)
        except OSError:
            continue
        nodes[node.name] = str(device)
        if node.name.startswith('renderD'):
            driver = None
            try:
                driver = (device / 'driver').resolve(strict=True).name
            except OSError:
                pass
            found[str(device)] = {'id': str(device), 'driver': driver, 'node': node.name,
                                  'name': NAMES.get(driver, driver or 'GPU'), 'sysfs': device}
    for name, device in nodes.items():
        if name.startswith('card') and device in found:
            found[device]['card'] = name
    return found, nodes


class GpuSampler:
    """Per-device and per-process GPU use between two calls of ``sample``."""

    def __init__(self, proc='/proc', sys='/sys'):
        self.proc, self.sys = Path(proc), Path(sys)
        self.previous = {}
        self.time = None

    def reset(self):
        self.previous = {}
        self.time = None

    def clients(self, pid):
        """DRM clients this process holds: [(device path, fdinfo text)], or None when unreadable."""
        directory = self.proc / str(pid)
        try:
            fds = os.listdir(directory / 'fd')
        except OSError:
            return None
        result = []
        for fd in fds:
            try:
                target = os.readlink(directory / 'fd' / fd)
            except OSError:
                continue
            if not target.startswith('/dev/dri/'):
                continue
            text = _read(directory / 'fdinfo' / fd)
            if text:
                result.append((target[9:], text))
        return result

    def sample(self, pids, now_ns):
        """``pids`` is an iterable of process IDs; returns (devices, per-pid engine shares).

        Each device: id, name, driver, available, busy (percent or None),
        engines {name: percent}, memory and memory_total (bytes or None).
        """
        found, nodes = devices(self.sys)
        first = self.time is None
        elapsed = 0 if first else now_ns - self.time
        current, by_pid, unreadable = {}, {}, set()
        for pid in sorted(pids):
            held = self.clients(pid)
            if held is None:
                unreadable.add(pid)
                continue
            for node, text in held:
                device = nodes.get(node)
                client = parse_fdinfo(text) if device in found else None
                if client is None:
                    continue
                key = (device, client['id'])
                if key in current:
                    continue  # the same client through a dup()ed or inherited fd
                current[key] = client
                shares = _engine_shares(client, self.previous.get(key), elapsed)
                mine = by_pid.setdefault(pid, {})
                for engine, share in shares.items():
                    mine[(device, engine)] = mine.get((device, engine), 0) + share
        result = []
        for device, info in found.items():
            clients = [c for (d, _), c in current.items() if d == device]
            available = any(_reports_engines(c) for c in clients)
            engines = {}
            for pid_shares in by_pid.values():
                for (d, engine), share in pid_shares.items():
                    if d == device:
                        engines[engine] = min(100.0, engines.get(engine, 0) + share)
            if available and not first:
                for client in clients:
                    for engine in list(client['busy']) + list(client['cycles']):
                        engines.setdefault(engine, 0.0)
            busy = max(engines.values()) if engines and available and not first else None
            # The DRM driver's own name when a client reports it: sysfs names
            # the bus driver (virtio-pci, msm_dpu) for some devices.
            driver = next((c['driver'] for c in clients if c['driver']), info['driver'])
            sysfs = info['sysfs']
            hardware = _number(sysfs / 'gpu_busy_percent')
            if hardware is not None:
                busy, available = float(hardware), True
            memory = _number(sysfs / 'mem_info_vram_used')
            total = _number(sysfs / 'mem_info_vram_total')
            if memory is None:
                sizes = [c['memory'] for c in clients if c['memory'] is not None]
                memory = sum(sizes) if sizes else None
            result.append({'id': device, 'name': NAMES.get(driver, info['name']), 'driver': driver, 'available': available,
                           'busy': busy if available else None,
                           'engines': {engine_name(e): v for e, v in sorted(engines.items())} if available else {},
                           'memory': memory, 'memory_total': total})
        self.previous, self.time = current, now_ns
        # A first reading has no interval: unmeasured, not idle.
        return result, {pid: None if first or pid in unreadable else by_pid.get(pid, {}) for pid in pids}


def app_share(shares, pids):
    """An app's GPU use: its busiest engine class summed over its processes."""
    engines = {}
    for pid in pids:
        mine = shares.get(pid)
        if mine is None:
            return None
        for key, share in mine.items():
            engines[key] = engines.get(key, 0) + share
    return min(100.0, max(engines.values())) if engines else 0.0
