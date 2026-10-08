"""Bounded static APK identity; independent of Android runtime availability."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile


def read_identity(path, helper='/usr/libexec/luma-apk-metadata'):
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix='luma-apk-identity-') as root:
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
                    if candidate.startswith(b'\x89PNG\r\n\x1a\n'):
                        raw = candidate
            except KeyError:
                pass
        return {'label': label.strip(), 'artwork': base64.b64encode(raw).decode('ascii')}


def identity_for_package(path, digest):
    from .safety import _validate_fingerprint, fingerprint
    _validate_fingerprint(digest)
    root = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'luma/installer/artwork'
    cache = root/(digest+'-apk-v1.json')
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
    raw = base64.b64decode(result.get('artwork', ''), validate=True)
    if len(raw) > 2*1024*1024:
        raise ValueError('APK artwork exceeds its limit')
    icon = ''
    if raw:
        destination = root/(hashlib.sha256(raw).hexdigest()+'.png')
        if not destination.exists():
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                stream.write(raw)
                temporary = Path(stream.name)
            temporary.replace(destination)
        icon = str(destination)
    return result.get('label', ''), icon


if __name__ == '__main__':
    import resource
    # Applied inside this dedicated worker, never preexec_fn in the GTK process.
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    resource.setrlimit(resource.RLIMIT_FSIZE, (68*1024*1024, 68*1024*1024))
    try:
        print(json.dumps(read_identity(sys.argv[1])))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
