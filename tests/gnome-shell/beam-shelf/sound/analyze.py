"""Read a sound oracle run: which actions were followed by sound on the events output, and which event ids reached PipeWire."""
import json, re, sys, wave, struct, math
out = sys.argv[1]
start = float(open(f'{out}/record-start').read())
rate = 48000; frames = open(f'{out}/events.raw', 'rb').read(); frames = frames[:len(frames) // 2 * 2]
samples = struct.unpack('<%dh' % (len(frames) // 2), frames)
def rms(t0, t1):
    a = max(0, int((t0 - start) * rate)); b = min(len(samples), int((t1 - start) * rate))
    if b <= a: return None
    chunk = samples[a:b]
    return math.sqrt(sum(s * s for s in chunk) / len(chunk))
lines = [l for l in open(f'{out}/shell.log', errors='replace') if '[snd]' in l]
events = []
for l in lines:
    m = re.search(r'\[snd\] (\w+) (\{.*\})', l)
    if m: events.append((m.group(1), json.loads(m.group(2))))
actions = [(e['t'], e['name']) for k, e in events if k == 'action']
mon_lines = open(f'{out}/pw-mon.log', errors='replace').read().splitlines()
event_hits = []
for l in mon_lines:
    m = re.match(r'(\d+\.\d+) .*event\.id = "([^"]+)"', l)
    if m: event_hits.append((float(m.group(1)), m.group(2)))
report = []
for i, (t, name) in enumerate(actions):
    end = actions[i + 1][0] if i + 1 < len(actions) else t + 2.5
    level = rms(t, min(end, t + 1.8))
    ids = sorted({i for (ts, i) in event_hits if t <= ts < min(end, t + 1.8)})
    report.append({'action': name, 'rms': None if level is None else round(level, 1),
                   'sound': level is not None and level > 1.0, 'event_ids': ids})
ids = [i for (_, i) in event_hits]
summary = {'run': out, 'actions': report, 'event_ids_seen_by_pipewire': sorted(set(ids)),
           'audio_volume_change_nodes': sum(1 for i in ids if i == 'audio-volume-change'),
           'osd': [e for k, e in events if k in ('osd', 'after', 'start', 'error')]}
print(json.dumps(summary, indent=1))
