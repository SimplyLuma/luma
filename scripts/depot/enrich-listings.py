#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Build Depot's rich listings: icons, screenshots, descriptions, releases.

The catalogue inputs (depot-catalog.json, depot-first-party.json,
depot-verified.json) say *what* Depot lists and where each app installs from;
depot-channels.json says which official channel replaces a "get it from the
publisher's website" listing. This job fills in everything a person reads on
a listing page, from the publisher's own published metadata:

* Flathub apps: Flathub's AppStream API (name, summary, description,
  developer, licence, links, releases, content rating, screenshots, icon),
  its summary API (download and installed size, sandbox metadata, from which
  the permissions are computed exactly as scripts/depot/flatpak-permissions.py
  computes them for the Luma remote) and its verification API.
* Snap Store apps: the Snap Store's info API (title, summary, description,
  publisher and its validation, licence, links, media, version, size,
  confinement).
* Luma's own apps: the icon from this repository, screenshots rendered from
  the packaged build (``--luma-shots``), and the words in depot-channels.json.
* Publisher repositories without store metadata: the publisher's official
  icon and screenshots named in depot-channels.json, used nominatively.

Every image is downloaded, checked, resized and re-encoded here -- icons as
square PNG (at least 256 px, 512 px when the source has it), screenshots as
WebP no wider than 1600 px -- and written content-addressed under
``--media-dir`` as media/listings/<id>/<kind>-<sha256[:16]>.<ext>. The
listing records the URL on dl.simplyluma.com and the SHA-256 Depot checks the
bytes against. Nothing is hot-linked: a device only ever fetches Luma's copy.

Output: src/luma-installer/data/depot-listings.json, which
generate-depot-seed.py merges into the schema 4 catalogue. It is deterministic
for the same upstream data, so a re-run with nothing new upstream changes
nothing, and a changed image is a new file rather than a replaced one.

    enrich-listings.py --media-dir SITE [--luma-shots DIR] [--only ID,...]
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import html
import importlib.util
import io
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'src/luma-installer/data'
OUTPUT = DATA / 'depot-listings.json'
PUBLIC_BASE = 'https://dl.simplyluma.com/media/listings'
USER_AGENT = 'Luma-Depot-Catalog/1 (+https://simplyluma.com)'
FLATHUB = 'https://flathub.org/api/v2'
SNAP = 'https://api.snapcraft.io/v2/snaps/info/'
SNAP_FIELDS = ('title,summary,description,media,license,publisher,contact,website,links,'
               'categories,store-url,version,download,confinement,revision,created-at')

ICON_MIN = 256
ICON_MAX = 512
SHOT_MAX_WIDTH = 1600
SHOTS_WANTED = 5
SHOT_QUALITY = 82
MAX_DOWNLOAD = 24 * 1024 * 1024

#: Flathub categories onto the catalogue's own (depot_catalog: [a-z][a-z0-9-]*).
CATEGORIES = {
    'AudioVideo': 'media', 'Audio': 'media', 'Video': 'media', 'Game': 'games',
    'Graphics': 'create', 'Development': 'development', 'Education': 'education',
    'Network': 'internet', 'Office': 'office', 'Science': 'science',
    'System': 'utilities', 'Utility': 'utilities', 'Chat': 'communication',
    'InstantMessaging': 'communication', 'Email': 'communication',
    'WebBrowser': 'internet', 'Security': 'utilities', 'Finance': 'finance',
}
SNAP_CATEGORIES = {
    'security': 'utilities', 'utilities': 'utilities', 'development': 'development',
    'productivity': 'work', 'social': 'communication', 'entertainment': 'media',
    'music-and-audio': 'media', 'photo-and-video': 'media', 'games': 'games',
    'art-and-design': 'create', 'education': 'education', 'finance': 'finance',
    'server-and-cloud': 'development', 'devices-and-iot': 'utilities',
}


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ── Fetching ─────────────────────────────────────────────────────────────

def fetch(url: str, *, headers: dict | None = None, limit: int = MAX_DOWNLOAD, tries: int = 3) -> bytes:
    if not url.startswith('https://'):
        raise ValueError(f'refusing a non-https source: {url}')
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:  # noqa: S310 (https only)
                content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError(f'{url}: larger than {limit} bytes')
            return content
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                raise
            last = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_json(url: str, **kwargs):
    return json.loads(fetch(url, **kwargs))


# ── Text ─────────────────────────────────────────────────────────────────

class _AppStreamText(HTMLParser):
    """AppStream description markup (<p>, <ul>/<ol>/<li>, <em>, <code>) as plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.list_depth = 0
        self.ordered: list[int] = []

    def _flush(self, prefix: str = '') -> None:
        text = re.sub(r'\s+', ' ', ''.join(self.current)).strip()
        if text:
            self.blocks.append(prefix + text)
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'li'):
            self._flush()
        if tag in ('ul', 'ol'):
            self._flush()
            self.list_depth += 1
            self.ordered.append(0 if tag == 'ul' else 1)

    def handle_endtag(self, tag):
        if tag == 'li':
            if self.ordered and self.ordered[-1]:
                prefix = f'{self.ordered[-1]}. '
                self.ordered[-1] += 1
            else:
                prefix = '• '
            self._flush(prefix)
        elif tag == 'p':
            self._flush()
        elif tag in ('ul', 'ol'):
            self._flush()
            self.list_depth = max(0, self.list_depth - 1)
            if self.ordered:
                self.ordered.pop()

    def handle_data(self, data):
        self.current.append(data)

    def text(self) -> str:
        self._flush()
        out: list[str] = []
        for block in self.blocks:
            bullet = block.startswith('• ') or re.match(r'\d+\. ', block)
            if out and not (bullet and (out[-1].startswith('• ') or re.match(r'\d+\. ', out[-1]))):
                out.append('')
            out.append(block)
        return '\n'.join(out).strip()


def appstream_text(markup: str | None) -> str:
    if not markup:
        return ''
    parser = _AppStreamText()
    parser.feed(markup)
    parser.close()
    return parser.text()


def markdown_text(text: str | None) -> str:
    """Snap Store descriptions are light Markdown: keep paragraphs and bullets."""
    if not text:
        return ''
    lines = []
    for raw in text.replace('\r\n', '\n').split('\n'):
        line = raw.rstrip()
        line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
        line = re.sub(r'__(.+?)__', r'\1', line)
        line = re.sub(r'(?<!\w)[*_](\S.*?\S|\S)[*_](?!\w)', r'\1', line)
        line = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'\1', line)
        line = re.sub(r'^\s*[*+-]\s+', '• ', line)
        line = re.sub(r'^\s*#+\s*', '', line)
        line = re.sub(r'(?<=\S) {2,}', ' ', line)
        lines.append(line.strip())
    # A blank line between two bullets is Markdown's loose list: one list.
    kept = []
    for index, line in enumerate(lines):
        if (not line and kept and kept[-1].startswith('• ')
                and index + 1 < len(lines) and lines[index + 1].startswith('• ')):
            continue
        kept.append(line)
    text = '\n'.join(kept)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return html.unescape(text).strip()


def clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit('\n', 1)[0] if '\n' in text[:limit] else text[:limit].rsplit(' ', 1)[0]
    return cut.rstrip(' ,;:') + '…'


def sentence(text: str | None, limit: int = 160) -> str:
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    if text and text[-1] not in '.!?…':
        text += '.'
    return clip(text, limit)


# ── Images ───────────────────────────────────────────────────────────────

def _pillow():
    try:
        from PIL import Image  # noqa: WPS433
    except ImportError as error:  # pragma: no cover - the tools container carries python3-pillow
        raise SystemExit('python3-pillow is required') from error
    return Image


def rasterize_svg(content: bytes, size: int) -> bytes:
    """An SVG as a PNG of ``size`` pixels, with rsvg-convert (librsvg)."""
    for command in (['rsvg-convert', '-w', str(size), '-h', str(size), '-a'],):
        try:
            result = subprocess.run(command, input=content, capture_output=True, check=True, timeout=60)
            return result.stdout
        except (OSError, subprocess.CalledProcessError):
            continue
    try:
        import cairosvg  # noqa: WPS433 - a developer machine without librsvg
        return cairosvg.svg2png(bytestring=content, output_width=size, output_height=size)
    except (ImportError, OSError):
        pass
    raise RuntimeError('no SVG rasterizer (rsvg-convert or cairosvg) is available')


def _looks_svg(content: bytes) -> bool:
    head = content[:512].lstrip().lower()
    return head.startswith(b'<svg') or (head.startswith(b'<?xml') and b'<svg' in content[:4096].lower())


def rounded_if_full_bleed(image):
    """A square icon painted to its corners (store artwork meant to be masked by
    the store, as the App Store does) gets the rounded corners every other
    icon in Depot has. Icons that already shape themselves are left alone."""
    Image = _pillow()
    from PIL import ImageDraw  # noqa: WPS433
    side = image.size[0]
    corners = [(0, 0), (side - 1, 0), (0, side - 1), (side - 1, side - 1)]
    if any(image.getpixel(point)[3] < 250 for point in corners):
        return image
    scale = 4
    mask = Image.new('L', (side * scale, side * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side * scale - 1, side * scale - 1),
                                           radius=round(side * scale * 0.2237), fill=255)
    mask = mask.resize((side, side), Image.LANCZOS)
    alpha = image.split()[3]
    from PIL import ImageChops  # noqa: WPS433
    image.putalpha(ImageChops.multiply(alpha, mask))
    return image


class Media:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self.written: list[Path] = []

    def _store(self, identifier: str, kind: str, suffix: str, content: bytes) -> dict:
        digest = hashlib.sha256(content).hexdigest()
        name = f'{kind}-{digest[:16]}.{suffix}'
        if self.directory is not None:
            target = self.directory / 'media/listings' / identifier / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                temporary = target.with_name(f'.{name}.tmp')
                temporary.write_bytes(content)
                temporary.replace(target)
            self.written.append(target)
        return {'url': f'{PUBLIC_BASE}/{identifier}/{name}', 'sha256': digest}

    def icon(self, identifier: str, content: bytes) -> dict:
        Image = _pillow()
        if _looks_svg(content):
            content = rasterize_svg(content, ICON_MAX)
        image = Image.open(io.BytesIO(content))
        image.load()
        if image.mode not in ('RGBA', 'LA'):
            image = image.convert('RGBA')
        width, height = image.size
        if abs(width - height) > 2:
            side = max(width, height)
            square = Image.new('RGBA', (side, side), (0, 0, 0, 0))
            square.paste(image, ((side - width) // 2, (side - height) // 2))
            image, width, height = square, side, side
        side = min(width, ICON_MAX)
        if side < ICON_MIN:
            raise ValueError(f'icon is only {width}x{height}; at least {ICON_MIN} px is required')
        if side != width:
            image = image.resize((side, side), Image.LANCZOS)
        image = rounded_if_full_bleed(image.convert('RGBA'))
        out = io.BytesIO()
        image.convert('RGBA').save(out, 'PNG', optimize=True)
        entry = self._store(identifier, f'icon-{side}', 'png', out.getvalue())
        entry.update({'width': side, 'height': side})
        return entry

    def screenshot(self, identifier: str, content: bytes, caption: str = '') -> dict:
        Image = _pillow()
        image = Image.open(io.BytesIO(content))
        image.load()
        image = image.convert('RGBA') if image.mode in ('P', 'LA') else image
        if image.mode == 'RGBA':
            backdrop = Image.new('RGB', image.size, (255, 255, 255))
            backdrop.paste(image, mask=image.split()[3])
            image = backdrop
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        width, height = image.size
        if width < 480 or height < 270:
            raise ValueError(f'screenshot is only {width}x{height}')
        if width > SHOT_MAX_WIDTH:
            height = round(height * SHOT_MAX_WIDTH / width)
            width = SHOT_MAX_WIDTH
            image = image.resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, 'WEBP', quality=SHOT_QUALITY, method=6)
        entry = self._store(identifier, 'shot', 'webp', out.getvalue())
        entry.update({'width': width, 'height': height})
        if caption:
            entry['caption'] = clip(re.sub(r'\s+', ' ', caption).strip(), 200)
        return entry


# ── Permissions ──────────────────────────────────────────────────────────

def _permission_tool():
    spec = importlib.util.spec_from_file_location('flatpak_permissions', ROOT / 'scripts/depot/flatpak-permissions.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flatpak_permissions(app_id: str, permissions: dict) -> list[dict]:
    """Flathub's summary ``permissions`` (the metadata keyfile as JSON), mapped
    through the same computation the Luma remote uses."""
    tool = _permission_tool()
    parser = configparser.ConfigParser(delimiters=('=',), interpolation=None, strict=False)
    parser.optionxform = str
    parser['Application'] = {'name': app_id}
    context = {}
    for key in ('shared', 'sockets', 'devices', 'features', 'filesystems', 'persistent'):
        values = permissions.get(key) or []
        if values:
            context[key] = ';'.join(values) + ';'
    parser['Context'] = context
    for section, key in (('Session Bus Policy', 'session-bus'), ('System Bus Policy', 'system-bus')):
        policy = permissions.get(key) or {}
        names = {}
        for verb in ('talk', 'own', 'see', 'call'):
            for name in policy.get(verb, []) or []:
                names[name] = verb
        if names:
            parser[section] = names
    return tool.compute(parser)


# ── Sources ──────────────────────────────────────────────────────────────

def age_rating(details: dict | None) -> str:
    if not isinstance(details, dict) or not details:
        return ''
    rating = next(iter(details.values()))
    categories = rating.get('categories') or []
    if categories and all(item.get('level') == 'none' for item in categories):
        return 'oars-1.1:none'
    age = rating.get('minimumAge')
    return f'age:{int(age)}' if isinstance(age, int) and age > 0 else ''


class FlathubCommits:
    """The icons an app exports, read from its signed commit on Flathub.

    Flathub's media service carries icons of at most 128 px (256 px at scale
    2) and many apps only publish the smaller one. The app itself exports its
    full-size icon in ``/export/share/icons``; pulling just that subpath of the
    GPG-verified commit (tens of kilobytes, no static deltas) gets the icon the
    app ships, bit for bit. Needs the ``ostree`` tool; without it, nothing.
    """

    SIZES = ('512x512', 'scalable', '256x256')

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self.repo = None

    def _ready(self) -> bool:
        if self.directory is None:
            return False
        if self.repo is not None:
            return True
        repo = self.directory / 'flathub-icons'
        try:
            if not (repo / 'config').is_file():
                repo.mkdir(parents=True, exist_ok=True)
                subprocess.run(['ostree', f'--repo={repo}', 'init', '--mode=bare-user-only'], check=True,
                               capture_output=True)
                key = self.directory / 'flathub.gpg'
                key.write_bytes(fetch('https://dl.flathub.org/repo/flathub.gpg'))
                subprocess.run(['ostree', f'--repo={repo}', 'remote', 'add', f'--gpg-import={key}',
                                'flathub', 'https://dl.flathub.org/repo/'], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError, urllib.error.URLError) as error:
            log(f'  no ostree icon source: {error}')
            return False
        self.repo = repo
        return True

    def icon(self, app_id: str, arch: str = 'x86_64') -> bytes | None:
        if not self._ready():
            return None
        ref = f'app/{app_id}/{arch}/stable'
        pulled = subprocess.run(['ostree', f'--repo={self.repo}', 'pull', '--disable-static-deltas',
                                 '--http-header=User-Agent=flatpak/1.16.1', '--subpath=/export/share/icons',
                                 'flathub', ref], capture_output=True, text=True, timeout=600)
        if pulled.returncode != 0:
            log(f'  {app_id}: ostree pull: {pulled.stderr.strip()[-200:]}')
            return None
        listing = subprocess.run(['ostree', f'--repo={self.repo}', 'ls', '-R', ref, '/export/share/icons'],
                                 capture_output=True, text=True, timeout=120).stdout
        paths = [line.split()[-1] for line in listing.splitlines()
                 if line.startswith('-') and '/apps/' in line and '/hicolor/' in line
                 and '-symbolic' not in line and line.split()[-1].rsplit('/', 1)[-1].startswith(app_id)]
        for size in self.SIZES:
            chosen = next((path for path in paths if f'/hicolor/{size}/apps/' in path), None)
            if chosen:
                return subprocess.run(['ostree', f'--repo={self.repo}', 'cat', ref, chosen],
                                      capture_output=True, check=True, timeout=120).stdout
        return None


COMMITS = FlathubCommits(None)


def flathub(identifier: str, app_id: str, media: Media, fallback_icon: str = '') -> dict:
    stream = fetch_json(f'{FLATHUB}/appstream/{app_id}')
    summary = fetch_json(f'{FLATHUB}/summary/{app_id}')
    try:
        verification = fetch_json(f'{FLATHUB}/verification/{app_id}/status')
    except (urllib.error.HTTPError, ValueError):
        verification = {}
    listing: dict = {'metadata_source': 'flathub'}
    listing['name'] = stream.get('name') or ''
    listing['summary'] = sentence(stream.get('summary'), 200)
    listing['description'] = clip(appstream_text(stream.get('description')), 4000)
    developer = (stream.get('developer_name') or '').strip()
    if developer:
        listing['developer_name'] = developer
    if verification.get('verified'):
        listing['publisher_verified'] = 'flathub'
    if stream.get('project_license'):
        licence = stream['project_license']
        # "LicenseRef-proprietary=https://..." is how AppStream says proprietary.
        listing['license'] = 'Proprietary' if licence.startswith('LicenseRef-proprietary') else licence[:120]
    urls = stream.get('urls') or {}
    for field, keys in (('homepage', ('homepage',)), ('support_url', ('help', 'bugtracker', 'contact', 'faq')),
                        ('source_url', ('vcs_browser',))):
        value = next((urls[key] for key in keys if isinstance(urls.get(key), str)
                      and urls[key].startswith('https://')), '')
        if value:
            listing[field] = value
    categories = []
    for category in stream.get('categories') or []:
        mapped = CATEGORIES.get(category)
        if mapped and mapped not in categories:
            categories.append(mapped)
    if categories:
        listing['categories'] = categories[:4]
    releases = [item for item in stream.get('releases') or [] if item.get('type', 'stable') == 'stable']
    release = {}
    if releases:
        latest = releases[0]
        release['version'] = str(latest.get('version') or '')[:64]
        if latest.get('timestamp'):
            release['date'] = time.strftime('%Y-%m-%d', time.gmtime(int(latest['timestamp'])))
        notes = appstream_text(latest.get('description'))
        if notes:
            release['notes'] = clip(notes, 1200)
    if isinstance(summary, dict):
        if summary.get('download_size'):
            release['download_bytes'] = int(summary['download_size'])
        if summary.get('installed_size'):
            release['installed_bytes'] = int(summary['installed_size'])
        metadata = summary.get('metadata') or {}
        if isinstance(metadata.get('permissions'), dict):
            listing['permissions'] = flatpak_permissions(app_id, metadata['permissions'])
        arches = [arch for arch in summary.get('arches') or [] if arch in ('x86_64', 'aarch64')]
        if arches:
            listing['architectures'] = arches
    if release.get('version'):
        listing['release'] = release
    rating = age_rating(stream.get('content_rating_details'))
    if rating:
        listing['age_rating'] = rating
    listing['sandbox'] = 'flatpak'

    icons = sorted(stream.get('icons') or [], key=lambda item: (item.get('width') or 0) * int(item.get('scale') or 1),
                   reverse=True)
    for candidate in icons:
        try:
            listing['icon'] = media.icon(identifier, fetch(candidate['url']))
            break
        except (ValueError, urllib.error.URLError, OSError) as error:
            log(f'  {identifier}: icon {candidate.get("url")}: {error}')
    if 'icon' not in listing:
        exported = COMMITS.icon(app_id)
        if exported:
            try:
                listing['icon'] = media.icon(identifier, exported)
                listing['icon_source'] = 'flathub-commit'
            except ValueError as error:
                log(f'  {identifier}: exported icon: {error}')
    if 'icon' not in listing and fallback_icon:
        # The publisher's own artwork, named in depot-channels.json, when
        # neither Flathub nor the app itself carries a large enough icon.
        try:
            listing['icon'] = media.icon(identifier, fetch(fallback_icon))
            listing['icon_source'] = 'publisher'
        except (ValueError, urllib.error.URLError, OSError) as error:
            log(f'  {identifier}: publisher icon {fallback_icon}: {error}')
    shots = []
    for shot in stream.get('screenshots') or []:
        sizes = [size for size in shot.get('sizes') or [] if str(size.get('src', '')).startswith('https://')]
        if not sizes:
            continue
        # The largest rendition no wider than twice Depot's frame, else the largest.
        sizes.sort(key=lambda size: int(size.get('width') or 0))
        fitting = [size for size in sizes if int(size.get('width') or 0) <= SHOT_MAX_WIDTH]
        chosen = fitting[-1] if fitting and int(fitting[-1].get('width') or 0) >= 1000 else sizes[-1]
        try:
            shots.append(media.screenshot(identifier, fetch(chosen['src']), shot.get('caption') or ''))
        except (ValueError, urllib.error.URLError, OSError) as error:
            log(f'  {identifier}: screenshot {chosen.get("src")}: {error}')
        if len(shots) >= SHOTS_WANTED:
            break
    if shots:
        listing['screenshots'] = shots
    return listing


def snap_store(identifier: str, name: str, media: Media) -> dict:
    info = fetch_json(SNAP + name + '?fields=' + SNAP_FIELDS, headers={'Snap-Device-Series': '16'})
    snap = info['snap']
    stable = [item for item in info.get('channel-map') or []
              if item['channel']['track'] == 'latest' and item['channel']['risk'] == 'stable']
    listing: dict = {'metadata_source': 'snap'}
    listing['name'] = snap.get('title') or name
    listing['summary'] = sentence(snap.get('summary'), 200)
    listing['description'] = clip(markdown_text(snap.get('description')), 4000)
    publisher = snap.get('publisher') or {}
    listing['developer_name'] = publisher.get('display-name') or ''
    listing['snap_publisher'] = {'username': publisher.get('username', ''),
                                 'validation': publisher.get('validation', '')}
    if publisher.get('validation') in ('verified', 'starred'):
        listing['publisher_verified'] = 'snapcraft'
    if snap.get('license') and snap['license'] not in ('unset', 'Other'):
        listing['license'] = snap['license']
    links = snap.get('links') or {}
    website = snap.get('website') or next(iter(links.get('website') or []), '')
    if website.startswith('https://'):
        listing['homepage'] = website
    source = next(iter(links.get('source') or []), '')
    if source.startswith('https://'):
        listing['source_url'] = source
    categories = []
    for category in snap.get('categories') or []:
        mapped = SNAP_CATEGORIES.get(category.get('name'))
        if mapped and mapped not in categories:
            categories.append(mapped)
    if categories:
        listing['categories'] = categories[:4]
    arches = sorted({{'amd64': 'x86_64', 'arm64': 'aarch64'}.get(item['channel']['architecture'], '')
                     for item in stable} - {''})
    if arches:
        listing['architectures'] = arches
    amd = next((item for item in stable if item['channel']['architecture'] == 'amd64'), stable[0] if stable else None)
    if amd:
        listing['release'] = {'version': str(amd.get('version', ''))[:64],
                              'date': str(amd['channel'].get('released-at', ''))[:10],
                              'download_bytes': int((amd.get('download') or {}).get('size') or 0)}
        listing['snap_confinement'] = amd.get('confinement', '')
    listing['sandbox'] = 'snap-strict' if listing.get('snap_confinement') == 'strict' else 'snap-classic'
    icons = [item for item in snap.get('media') or [] if item.get('type') == 'icon']
    for candidate in sorted(icons, key=lambda item: item.get('width') or 0, reverse=True):
        try:
            listing['icon'] = media.icon(identifier, fetch(candidate['url']))
            break
        except (ValueError, urllib.error.URLError, OSError) as error:
            log(f'  {identifier}: icon {candidate.get("url")}: {error}')
    shots = []
    for item in snap.get('media') or []:
        if item.get('type') != 'screenshot':
            continue
        try:
            shots.append(media.screenshot(identifier, fetch(item['url'])))
        except (ValueError, urllib.error.URLError, OSError) as error:
            log(f'  {identifier}: screenshot {item.get("url")}: {error}')
        if len(shots) >= SHOTS_WANTED:
            break
    if shots:
        listing['screenshots'] = shots
    return listing


def file_from_rpm(rpm_dir: Path, package: str, member: str) -> bytes:
    """One file from the newest ``package`` RPM in ``rpm_dir`` (a Luma package pool)."""
    candidates = sorted(path for path in rpm_dir.rglob(f'{package}-[0-9]*.rpm')
                        if not path.name.endswith('.src.rpm'))
    if not candidates:
        raise ValueError(f'no {package} RPM in {rpm_dir}')
    payload = subprocess.run(['rpm2cpio', str(candidates[-1])], capture_output=True, check=True).stdout
    result = subprocess.run(['cpio', '-i', '--quiet', '--to-stdout', './' + member.lstrip('./')],
                            input=payload, capture_output=True, check=True)
    if not result.stdout:
        raise ValueError(f'{member} is not in {candidates[-1].name}')
    return result.stdout


def authored(identifier: str, spec: dict, media: Media, luma_shots: Path | None,
             rpm_dir: Path | None = None) -> dict:
    """Luma's own apps and publisher-repository apps: words from depot-channels.json,
    the icon from this repository or the publisher's official artwork."""
    listing: dict = {'metadata_source': spec.get('kind', 'authored')}
    for field in ('summary', 'description', 'developer_name', 'license', 'homepage', 'support_url',
                  'source_url', 'privacy_url', 'categories', 'age_rating', 'sandbox', 'publisher_verified'):
        if spec.get(field):
            listing[field] = spec[field]
    if spec.get('release'):
        listing['release'] = spec['release']
    icon = spec.get('icon')
    if spec.get('icon_rpm') and rpm_dir is not None:
        listing['icon'] = media.icon(identifier, file_from_rpm(rpm_dir, spec['icon_rpm']['package'],
                                                               spec['icon_rpm']['path']))
    elif icon:
        content = (ROOT / icon).read_bytes() if not icon.startswith('https://') else fetch(icon)
        listing['icon'] = media.icon(identifier, content)
    shots = []
    for shot in spec.get('screenshots') or []:
        url, caption = (shot, '') if isinstance(shot, str) else (shot['url'], shot.get('caption', ''))
        try:
            shots.append(media.screenshot(identifier, fetch(url), caption))
        except (ValueError, urllib.error.URLError, OSError) as error:
            log(f'  {identifier}: screenshot {url}: {error}')
    if luma_shots is not None and spec.get('rendered_screenshots', True):
        folder = luma_shots / identifier
        captions = []
        if (folder / 'captions.json').is_file():
            captions = json.loads((folder / 'captions.json').read_text())
        for index, path in enumerate(sorted(folder.glob('[0-9].png'))):
            caption = captions[index] if index < len(captions) else ''
            shots.append(media.screenshot(identifier, path.read_bytes(), caption))
    if shots:
        listing['screenshots'] = shots[:SHOTS_WANTED]
    return listing


# ── Driver ───────────────────────────────────────────────────────────────

def inputs() -> tuple[dict, dict]:
    """Every catalogue entry by id (as the seed will list it) and the channel overrides."""
    sys.path.insert(0, str(ROOT / 'src/luma-installer/tools'))
    spec = importlib.util.spec_from_file_location('generate_depot_seed', ROOT / 'src/luma-installer/tools/generate-depot-seed.py')
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    document = json.loads(seed.generate(listings_path=None))
    channels = json.loads((DATA / 'depot-channels.json').read_text())
    return {entry['id']: entry for entry in document['applications']}, channels


def plan(entry: dict, channel: dict) -> tuple[str, str]:
    """(metadata kind, key) for one entry."""
    metadata = channel.get('metadata') or {}
    if 'snap' in metadata:
        return 'snap', metadata['snap']
    if 'flathub' in metadata:
        return 'flathub', metadata['flathub']
    if 'authored' in metadata:
        return 'authored', ''
    if entry.get('backend') == 'flatpak' and entry.get('repository') == 'flathub':
        return 'flathub', entry['source_id']
    return 'authored', ''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--media-dir', type=Path, help='site directory media/listings/ is written under')
    parser.add_argument('--luma-shots', type=Path, help='rendered first-party screenshots, <id>/N.png')
    parser.add_argument('--rpm-dir', type=Path, help='a Luma package pool, for icons shipped only in an RPM')
    parser.add_argument('--only', default='', help='comma-separated ids to refresh; others are kept')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--work-dir', type=Path,
                        help='scratch space for reading icons from signed Flathub commits (needs ostree)')
    args = parser.parse_args(argv)
    COMMITS.directory = args.work_dir

    entries, channels = inputs()
    previous = json.loads(args.output.read_text()) if args.output.is_file() else {'listings': {}}
    listings = dict(previous.get('listings', {}))
    only = {item for item in args.only.split(',') if item}
    media = Media(args.media_dir)
    failures = []
    for identifier in sorted(entries):
        if only and identifier not in only:
            continue
        entry = entries[identifier]
        channel = channels.get('applications', {}).get(identifier, {})
        if entry.get('visibility') not in ('public', 'unlisted'):
            continue
        kind, key = plan(entry, channel)
        log(f'{identifier}: {kind} {key}')
        try:
            if kind == 'flathub':
                listing = flathub(identifier, key, media, (channel.get('metadata') or {}).get('icon', ''))
            elif kind == 'snap':
                listing = snap_store(identifier, key, media)
            else:
                listing = authored(identifier, (channel.get('metadata') or {}).get('authored', {}), media,
                                   args.luma_shots, args.rpm_dir)
            # Words written for Luma win over what a store says.
            for field, value in ((channel.get('metadata') or {}).get('override') or {}).items():
                listing[field] = value
        except (urllib.error.URLError, ValueError, KeyError, OSError, RuntimeError) as error:
            failures.append(f'{identifier}: {error}')
            log(f'  FAILED: {error}')
            continue
        listings[identifier] = listing
    document = {
        'comment': ('Generated by scripts/depot/enrich-listings.py from each publisher\'s own store '
                    'metadata; do not edit by hand. Words written for Luma live in depot-channels.json.'),
        'media_base': PUBLIC_BASE,
        'listings': {key: listings[key] for key in sorted(listings)},
    }
    args.output.write_text(json.dumps(document, indent=1, ensure_ascii=False) + '\n')
    log(f'wrote {args.output} ({len(listings)} listings); media files: {len(media.written)}')
    for failure in failures:
        log(f'failed: {failure}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
