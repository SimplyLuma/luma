# SPDX-License-Identifier: Apache-2.0
"""Bounded, read-only serialization of Monitor's existing native sampler."""
from dataclasses import asdict
import json
from .model import Process, Identity

BUS = 'org.projectluma.MonitorHost1'
OBJECT = '/org/projectluma/MonitorHost1'
MAX_BYTES = 16 * 1024 * 1024

def encode(snapshot, users):
    result = dict(snapshot)
    result['processes'] = [asdict(p) for p in snapshot['processes']]
    result['rows'] = [dict(row, members=[p.key for p in row['members']]) for row in snapshot['rows']]
    result['users'] = users
    text = json.dumps(result, separators=(',', ':'), allow_nan=False)
    if len(text.encode()) > MAX_BYTES: raise ValueError('The host activity sample exceeds its safe size.')
    return text

def decode(text):
    if len(text.encode()) > MAX_BYTES: raise ValueError('The host activity sample exceeds its safe size.')
    result = json.loads(text)
    processes = []
    for item in result['processes']:
        item = dict(item)
        if item['identity'] is not None: item['identity'] = Identity(**item['identity'])
        processes.append(Process(**item))
    by_key = {p.key: p for p in processes}
    if len(by_key) != len(processes): raise ValueError('Duplicate host process identity.')
    result['processes'] = processes
    result['rows'] = [dict(row, members=[by_key[key] for key in row['members']]) for row in result['rows']]
    result['users'] = {int(key): value for key, value in result['users'].items()}
    return result
