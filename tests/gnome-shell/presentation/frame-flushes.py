# SPDX-License-Identifier: Apache-2.0
"""Per-press paint cost from a Shell Sysprof capture: for each press time (CLOCK_MONOTONIC
microseconds, one per line in TIMES), the first Clutter::Stage::paint_view() that starts
after it, its duration and the Cogl journal flushes inside it. Prints JSON.
frame-flushes.py CAPTURE TIMES"""
import json
import struct
import sys


def marks(path):
    data = open(path, 'rb').read()
    if len(data) < 256 or struct.unpack_from('<I', data, 0)[0] != 0xFDCA975E:
        return []
    offset, found = 256, []
    while offset + 24 <= len(data):
        length, _cpu, _pid, time, kind = struct.unpack_from('<HhiqI', data, offset)
        if length == 0:
            break
        if kind & 0xff == 10:
            duration, = struct.unpack_from('<q', data, offset + 24)
            name = data[offset + 56:offset + 96].split(b'\0')[0].decode(errors='replace')
            found.append((time, duration, name))
        offset += length
    return found


capture, times = sys.argv[1], sys.argv[2]
all_marks = marks(capture)
paints = sorted((t, d) for t, d, n in all_marks if n.startswith('Clutter::Stage::paint_view'))
flushes = sorted(t for t, d, n in all_marks if n.startswith('Cogl::Journal::flush'))
result = []
for line in open(times):
    press = int(line) * 1000
    paint = next(((t, d) for t, d in paints if t >= press), None)
    if not paint:
        continue
    start, duration = paint
    count = sum(1 for t in flushes if start <= t < start + duration)
    result.append({'flushes': count, 'paint_ms': duration / 1e6})
print(json.dumps({'presses': result, 'marks': len(all_marks)}))
