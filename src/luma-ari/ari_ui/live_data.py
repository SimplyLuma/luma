# SPDX-License-Identifier: Apache-2.0
"""GTK-free stored-record conversion and read-only host capacity."""
import os
import shutil
import time
from pathlib import Path
from .fixture import audit_timestamp


def stored_receipt(step, conversation, day):
    state = step.get('state', 'done')
    undone = state in ('undone', 'reverted')
    timestamp = audit_timestamp(step.get('created'))
    return {'t': step['summary'], 'day': day,
            'at': time.strftime('%H:%M', time.localtime(timestamp)) if timestamp is not None else '',
            'cats': ['Settings'], 'conv': conversation,
            'undo': 'undone' if undone else 'can' if step.get('undo') else 'none',
            'kept': state == 'kept',
            'nodes': [{'k': 'setting', 'n': step['summary'], 'm': step['tool'],
                       'st': 'undone' if undone else 'done'}]}


def stored_messages(record):
    """Keep each receipt beside the reply whose recorded metadata owns it.

    Old records without metadata retain every step at the end. No model,
    execution time or benchmark is fabricated when the daemon didn't store it.
    """
    steps = {step['id'] for step in record.get('steps', [])}
    attached, result = set(), []
    for message in record.get('messages', []):
        entry = {'u' if message['role'] == 'user' else 'a': message['content']}
        metadata = message.get('meta') or {}
        model = metadata.get('model_info')
        if message['role'] != 'user' and isinstance(model, dict) and model.get('id') and isinstance(metadata.get('elapsed'), (int, float)):
            entry.update(m=model['id'], model_info=model, s=metadata['elapsed'])
        result.append(entry)
        for identity in metadata.get('steps', []):
            if identity in steps and identity not in attached:
                result.append({'ch': identity})
                attached.add(identity)
    for step in record.get('steps', []):
        if step['id'] not in attached:
            result.append({'ch': step['id']})
            attached.add(step['id'])
    return result


def live_capacity(*, meminfo=Path("/proc/meminfo"), storage=None,
                  product=Path("/sys/class/dmi/id/product_version")):
    """Read host capacity without creating a store or inventing measurements.

    Free uses the kernel's MemAvailable estimate; no model allocation is
    guessed from file size or a selected model. See
    https://docs.kernel.org/filesystems/proc.html#meminfo
    """
    result = {}
    try:
        fields = {}
        for line in meminfo.read_text().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] in ("MemTotal:", "MemAvailable:") and parts[2] == "kB":
                fields[parts[0][:-1]] = int(parts[1]) / 1024 ** 2
        total, available = fields.get("MemTotal"), fields.get("MemAvailable")
        if total is not None and total > 0:
            result["mem"] = total
            if available is not None and 0 <= available <= total:
                result.update(sys=total - available, free=available)
    except (OSError, ValueError, OverflowError):
        pass
    target = Path(storage) if storage is not None else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "luma/ari/models"
    try:
        while not target.exists() and target != target.parent:
            target = target.parent
        result["disk"] = shutil.disk_usage(target).free / 1e9
    except OSError:
        pass
    try:
        name = " ".join(product.read_text().split())
        if name:
            result["n"] = name
    except (OSError, ValueError, OverflowError):
        pass
    return result
