#!/usr/bin/env python3
"""Create a deterministic private source archive; no network or RPM build."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
source = root / 'src/luma-continuity'
files = [(p, 'luma-continuity/' + str(p.relative_to(source))) for p in source.rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
files += [(root / 'LICENSE.md', 'luma-continuity/LICENSE.md')]
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open('wb') as raw:
    with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for path, name in sorted(files, key=lambda item: item[1]):
                data = path.read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(data); info.mode = 0o755 if '/bin/' in name else 0o644
                info.mtime = 0; info.uid = 0; info.gid = 0
                archive.addfile(info, io.BytesIO(data))
print(hashlib.sha256(args.output.read_bytes()).hexdigest())
