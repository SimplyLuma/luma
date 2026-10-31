"""Bounded static APK identity; independent of Android runtime availability."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile


def _text(value, limit=256):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        return ''
    return value.strip()


def _package(value):
    value = _text(value)
    return value if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*', value) else ''


def raster_extension(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return '.webp'
    return ''


@contextmanager
def identity_archive(path):
    """Read the base APK of a split archive without extracting other payloads."""
    with zipfile.ZipFile(path) as outer:
        if 'AndroidManifest.xml' in outer.namelist():
            yield outer
            return
        members = outer.infolist()
        if len(members) > 100_000:
            raise ValueError('Android package has too many entries')
        apks = [m for m in members if not m.is_dir() and m.filename.lower().endswith('.apk')]
        bases = [m for m in apks if PurePosixPath(m.filename).name.lower() == 'base.apk']
        if not bases:
            bases = [m for m in apks if not PurePosixPath(m.filename).name.lower().startswith(('config.', 'split_config.'))]
        if len(bases) != 1 or not 0 < bases[0].file_size <= 256*1024*1024:
            raise ValueError('Android package has no bounded unambiguous base APK')
        with tempfile.TemporaryFile() as base, outer.open(bases[0]) as source:
            shutil.copyfileobj(source, base, length=1024*1024)
            base.seek(0)
            with zipfile.ZipFile(base) as archive:
                yield archive


def read_identity(path, helper='/usr/libexec/luma-apk-metadata'):
    with identity_archive(path) as archive, tempfile.TemporaryDirectory(prefix='luma-apk-identity-') as root:
        for member, target, limit in [('AndroidManifest.xml', 'manifest', 4*1024*1024),
                                      ('resources.arsc', 'resources', 64*1024*1024)]:
            info = archive.getinfo(member)
            if not 0 < info.file_size <= limit:
                raise ValueError('APK resource metadata exceeds its limit')
            (Path(root)/target).write_bytes(archive.read(info))
        worker = subprocess.run([helper, root+'/manifest', root+'/resources'],
                                capture_output=True, text=True, timeout=5, check=True)
        if len(worker.stdout) > 8192:
            raise ValueError('APK identity exceeds its limit')
        identity = json.loads(worker.stdout)
        label = identity.get('label')
        if not isinstance(label, str) or not label.strip() or len(label) > 256 or any(ord(c) < 32 for c in label):
            label = ''
        icon = identity.get('icon')
        raw = b''
        if isinstance(icon, str) and not icon.startswith('/') and '..' not in PurePosixPath(icon).parts:
            try:
                info = archive.getinfo(icon)
                if 0 < info.file_size <= 2*1024*1024:
                    candidate = archive.read(info)
                    # Raster resources only; adaptive/vector XML is not executable artwork.
                    if raster_extension(candidate):
                        raw = candidate
            except KeyError:
                pass
        result = {'label': label.strip(), 'artwork': base64.b64encode(raw).decode('ascii')}
        for key, value in (('version', _text(identity.get('version'))),
                           ('package', _package(identity.get('package')))):
            if value:
                result[key] = value
        return result


def metadata_for_package(path, digest):
    from .safety import _validate_fingerprint, fingerprint
    _validate_fingerprint(digest)
    root = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'luma/installer/artwork'
    cache = root/(digest+'-apk-v4.json')
    try:
        result = json.loads(cache.read_text())
    except (OSError, ValueError):
        worker = subprocess.run([sys.executable, '-m', 'luma_installer.apk_metadata', str(path)],
                                capture_output=True, text=True, timeout=8, check=True)
        if len(worker.stdout) > 3*1024*1024:
            raise ValueError('APK artwork exceeds its limit')
        result = json.loads(worker.stdout)
        if fingerprint(path)[1] != digest:
            raise ValueError('APK changed during metadata inspection')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as stream:
            json.dump(result, stream)
            temporary = Path(stream.name)
        temporary.replace(cache)
    if not isinstance(result, dict) or not isinstance(result.get('artwork', ''), str):
        raise ValueError('APK identity cache is malformed')
    raw = base64.b64decode(result.get('artwork', ''), validate=True)
    if len(raw) > 2*1024*1024 or (raw and not raster_extension(raw)):
        raise ValueError('APK artwork exceeds its limit')
    icon = ''
    if raw:
        destination = root/(hashlib.sha256(raw).hexdigest()+raster_extension(raw))
        if not destination.exists():
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                stream.write(raw)
                temporary = Path(stream.name)
            temporary.replace(destination)
        icon = str(destination)
    return {'label': _text(result.get('label')), 'icon': icon,
            'version': _text(result.get('version')), 'package': _package(result.get('package'))}


def identity_for_package(path, digest):
    value = metadata_for_package(path, digest)
    return value['label'], value['icon']


if __name__ == '__main__':
    import resource
    # Applied inside this dedicated worker, never preexec_fn in the GTK process.
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256*1024*1024, 256*1024*1024))
    try:
        print(json.dumps(read_identity(sys.argv[1])))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
