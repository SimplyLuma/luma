#!/usr/bin/env python3
"""Extract Settings' plain data literals from the approved, read-only Studio file.

This deliberately accepts literals only; it cannot execute JavaScript.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

TOKEN = re.compile(r'''\s*(?:(?P<string>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(?P<number>-?(?:\d+(?:\.\d+)?|\.\d+))|(?P<word>[A-Za-z_$][\w$]*)|(?P<punct>[{}\[\],:]))''')

class Literal:
    def __init__(self, text):
        self.text, self.pos = text, 0

    def token(self):
        match = TOKEN.match(self.text, self.pos)
        if not match:
            raise ValueError(f'Nonliteral JavaScript at {self.text[self.pos:self.pos+50]!r}')
        self.pos = match.end()
        return match.lastgroup, match.group(match.lastgroup)

    def value(self, token=None):
        kind, token = token or self.token()
        if kind == 'string':
            return ast.literal_eval(token)
        if kind == 'number':
            return float(token) if '.' in token else int(token)
        if kind == 'word':
            return {'true': True, 'false': False, 'null': None}[token]
        if token == '[':
            result = []
            next_token = self.token()
            while next_token[1] != ']':
                result.append(self.value(next_token))
                next_token = self.token()
                if next_token[1] == ']':
                    break
                assert next_token[1] == ','
                next_token = self.token()
            return result
        if token == '{':
            result = {}
            key = self.token()
            while key[1] != '}':
                assert key[0] in ('string', 'word')
                name = ast.literal_eval(key[1]) if key[0] == 'string' else key[1]
                assert self.token()[1] == ':'
                result[name] = self.value()
                key = self.token()
                if key[1] == '}':
                    break
                assert key[1] == ','
                key = self.token()
            return result
        raise ValueError(token)

def share_code(text):
    """Fixture artwork from v70 cfQR; this is not a network credentials encoder."""
    seed = 0
    for char in text:
        point = ord(char)
        unit = point if point <= 0xffff else 0xd800 + ((point - 0x10000) >> 10)
        seed = (seed * 31 + unit) & 0xffffffff
    size, cells = 25, []
    finders = ((0, 0), (18, 0), (0, 18))
    for y in range(size):
        for x in range(size):
            if any(a <= x < a + 7 and b <= y < b + 7 for a, b in finders):
                continue
            # JS Number multiplies in binary64 before its unsigned conversion.
            seed = int(float(seed) * 1103515245 + 12345) & 0xffffffff
            if (seed >> 16) & 1:
                cells.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    for a, b in finders:
        cells.append(f'<rect x="{a}" y="{b}" width="7" height="7"/><rect x="{a+1}" y="{b+1}" width="5" height="5" fill="#fff"/><rect x="{a+2}" y="{b+2}" width="3" height="3"/>')
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 29 29"><rect x="-2" y="-2" width="29" height="29" fill="#fff"/><g fill="#111">' + ''.join(cells) + '</g></svg>'

def extract(path):
    raw = path.read_bytes()
    source = raw.decode()
    section = source[source.index('  const CF = {'):source.index('  /* ══ Windows', source.index('  const CF = {'))]
    constants = {}
    for name in ('CF', 'CFNETS', 'CFKNOWN', 'CFBT', 'CFNEAR', 'CFOUT', 'CFDISP', 'CFACC', 'CFWALL', 'CFPRE', 'CFFMT', 'CFLANG', 'CFZONE', 'CFAPPS', 'CFSZ', 'CFPERMS', 'CFPERM', 'CFSHORT', 'CFKB', 'CFWEEK', 'CFGRPS', 'CFMINS'):
        match = re.search(r'\bconst '+name+r'\s*=\s*', section)
        assert match, name
        constants[name] = Literal(section[match.end():]).value()
    pages_source = section[section.index('  const CFP = {'):section.index('  const cfGB')]
    starts = list(re.finditer(r"^    (?:'([^']+)'|([a-z][\w-]*)): \{", pages_source, re.M))
    pages, page_texts = [], {}
    for i, match in enumerate(starts):
        text = pages_source[match.end():starts[i+1].start() if i+1 < len(starts) else len(pages_source)]
        name = re.search(r"\bn: '([^']+)'", text)
        parent = re.search(r"\bup: '([^']+)'", text)
        page_texts[match[1] or match[2]] = text
        pages.append({'id': match[1] or match[2], 'title': name[1] if name else 'Studio North', 'parent': parent[1] if parent else None})
    assert constants['CF']['net'] == 'Studio North'
    assert len(constants['CFNETS']) == 6 and len(constants['CFKNOWN']) == 6
    assert len(pages) > 40
    password = re.search(r'qr:.*?<span class="mono">([^<]+)</span>', section)
    assert password, 'Wi-Fi share password'
    # The dock supplies cfName/cfIco in the spec. Accept only literal markup.
    app_info = {}
    dock = source[source.index('    <div class="apps">'):source.index('    <div class="apps">') + 12000]
    pattern = r'<button class="dk"[^>]*data-app="([^"<>]+)"[^>]*aria-label="([^"<>]+)"[^>]*><img src="([^"<>]+)"'
    for app, title, icon in re.findall(pattern, dock):
        assert app not in app_info, app
        app_info[app] = {'name': title, 'spec_artwork': Path(icon).name}
    required = set(constants['CFAPPS']) | set(constants['CF']['na']) | {x[0] for x in constants['CF']['sa']}
    assert required <= app_info.keys(), required - app_info.keys()
    constants['CFAPPINFO'] = app_info
    # cfCat reads Depot's first four literal da() arguments, with these two
    # explicit aliases. Parse only those strings: never execute the catalog.
    categories = {}
    for match in re.finditer(r'^  da\(', source, re.M):
        literal = Literal(source[match.end():])
        arguments = []
        for index in range(4):
            value = literal.value()
            assert isinstance(value, str), 'Nonliteral Depot category argument'
            arguments.append(value)
            assert literal.token()[1] == ',', 'Malformed Depot category prefix'
        app, _, _, category = arguments
        assert app not in categories and category in ('create', 'work', 'media', 'play', 'tools')
        categories[app] = category
    assert categories, 'Missing Depot catalog'
    aliases = {'term': 'terminal', 'memos': 'memo'}
    constants['CFAPPCATS'] = {app: categories[aliases.get(app, app)]
        for app in constants['CFAPPS'] if aliases.get(app, app) in categories}
    sample = re.search(r'class="cfcardn".*?<div><b>([^<]+)</b><em>([^<]+)</em></div><span>([^<]+)</span>', pages_source)
    badge = re.search(r"bell-off'\).*?<b>(\d+)</b>", pages_source)
    assert sample and badge, 'Notification preview sample'
    constants['CFNOTIFY'] = {'from': sample[1], 'text': sample[2], 'when': sample[3], 'count': int(badge[1])}
    # Power's sample facts live in literal page markup, rather than CF.
    # Preserve that provenance: these must never be a live battery fallback.
    power = page_texts['power']
    status = re.search(r"meta:\s*\(\)\s*=>\s*'([^']+)'", power)
    battery = re.search(r'class="cfbatt"><b>(\d+)%</b><em>([^<]+)</em>', power)
    history_source = section[section.index('  function cfBatDay()'):section.index('  function cfPoint()')]
    history_start = re.search(r'\bconst L\s*=\s*', history_source)
    assert status and battery and history_start, 'Power fixture literals'
    history_literal = Literal(history_source[history_start.end():])
    history = history_literal.value()
    assert history_literal.text[history_literal.pos:].lstrip().startswith(', now = L.length;'), 'Nonliteral battery history'
    assert isinstance(history, list) and all(type(x) is int and 0 <= x <= 100 for x in history)
    devices = re.findall(r"rVl\('([^']+)', '(\d+)%', \{ i: '([^']+)' \}\)", power)
    assert devices, 'Connected-device fixture literals'
    constants['CFPOWER'] = {'status': status[1], 'percentage': int(battery[1]), 'remaining': battery[2],
        'history': history, 'devices': [{'name': name, 'percentage': int(level), 'icon': icon} for name, level, icon in devices]}
    constants['CFWIFI'] = {
        'share_password': password[1],
        'share_codes': {n['n']: share_code(n['n'] + 'wpa3') for n in constants['CFNETS']},
    }
    return {'version': 1, 'spec_sha256': hashlib.sha256(raw).hexdigest(), 'selected': 'wifi', 'settings': constants.pop('CF'), 'data': constants, 'pages': pages}

if __name__ == '__main__':
    print(json.dumps(extract(Path(sys.argv[1])), ensure_ascii=False, indent=2))
