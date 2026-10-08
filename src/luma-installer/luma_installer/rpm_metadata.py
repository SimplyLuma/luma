"""Bounded, read-only RPM artwork inspection through the system libarchive.

Runs in a short-lived worker; never extracts package paths into the filesystem.
"""
import base64
import configparser
import json
from pathlib import PurePosixPath
import re
import shlex
import posixpath
import sys

MAX_MEMBER = 16 * 1024 * 1024
MAX_ARTWORK = 32 * 1024 * 1024


def read_identity(path, desktop):
    import libarchive
    images = {}
    total = 0
    entries = {}
    links = {}
    desktops = {desktop} if isinstance(desktop, str) else set(desktop)
    with libarchive.file_reader(path) as archive:
        for member in archive:
            name = '/' + member.pathname.removeprefix('./').lstrip('/')
            parts = PurePosixPath(name).parts
            if '..' in parts:
                continue
            if getattr(member, 'issym', False):
                links[name] = member.linkpath
            if not member.isfile:
                continue
            is_desktop = name in desktops
            is_icon = (name.startswith(('/usr/share/icons/', '/usr/share/pixmaps/', '/usr/local/share/icons/'))
                       and PurePosixPath(name).suffix.lower() in {'.png', '.svg', '.xpm'})
            is_icon = is_icon or (name.startswith('/opt/') and
                                 re.fullmatch(r'product_logo_\d+\.png', PurePosixPath(name).name) is not None)
            if not is_desktop and not is_icon:
                continue
            size = member.size
            if size is None or not 0 < size <= (1024 * 1024 if is_desktop else MAX_MEMBER):
                raise ValueError('Packaged metadata exceeds its size limit.')
            total += size
            if total > MAX_ARTWORK:
                raise ValueError('Packaged artwork exceeds its total size limit.')
            content = bytearray()
            for block in member.get_blocks():
                content.extend(block)
                if len(content) > size:
                    raise ValueError('Packaged metadata exceeds its declared size.')
            if len(content) != size:
                raise ValueError('Packaged metadata is truncated.')
            if is_desktop:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.read_string(content.decode('utf-8'))
                entries[name] = dict(parser['Desktop Entry'])
            else:
                images[name] = bytes(content)
    visible = {p: e for p, e in entries.items()
               if e.get('hidden', '').lower() != 'true' and e.get('nodisplay', '').lower() != 'true'}
    identities = {(e.get('name'), e.get('exec'), e.get('icon')) for e in visible.values()}
    selected_desktop = min(visible) if len(identities) == 1 else None
    entry = visible[selected_desktop] if selected_desktop else {}
    icon = entry.get('icon', '')
    choices = [p for p in images if p == icon or PurePosixPath(p).stem == icon or PurePosixPath(p).name == icon]
    # Some browser RPMs register their theme icons in a maintainer script.
    # Follow only archive-owned executable symlinks to locate that app's logo;
    # never execute scripts or resolve against files on the inspecting host.
    if not choices and entry.get('exec'):
        executable = shlex.split(entry['exec'])[0]
        seen = set()
        while executable in links and executable not in seen and len(seen) < 16:
            seen.add(executable)
            target = links[executable]
            executable = posixpath.normpath(target if target.startswith('/') else
                                            posixpath.join(posixpath.dirname(executable), target))
        parent = PurePosixPath(executable).parent
        choices = [p for p in images if PurePosixPath(p).parent == parent and
                   re.fullmatch(r'product_logo_\d+\.png', PurePosixPath(p).name)]
    def rank(p):
        dimensions = re.search(r'/(\d+)x\d+/', p)
        product = re.search(r'product_logo_(\d+)\.png$', p)
        size = int(dimensions[1]) if dimensions else int(product[1]) if product else 0
        return (not p.endswith('.svg'), -size, p)
    selected = min(choices, key=rank) if choices else None
    return {'entry': entry, 'desktop': selected_desktop, 'suffix': PurePosixPath(selected).suffix if selected else '',
            'artwork': base64.b64encode(images[selected]).decode('ascii') if selected else ''}


if __name__ == '__main__':
    try:
        print(json.dumps(read_identity(sys.argv[1], json.loads(sys.argv[2]))))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
