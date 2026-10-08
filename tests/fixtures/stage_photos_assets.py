"""Stage v70's local reference images for Photos conform; never commit them.

Run before conform: python3 tests/fixtures/stage_photos_assets.py
After the last capture, remove only the generated photos-v70 directory to
restore a clean worktree. The manifest and fixture JSON stay committed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--studio', type=Path,
                        default=Path.home() / 'Documents/LumaDesign/studio')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / 'photos-v70-assets.json').read_text())
    # Validate everything before creating staging files. Never accept traversal.
    copies = []
    for entry in manifest['files']:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid reference asset path')
        source = args.studio / relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError(f'Reference asset changed: {relative}')
        copies.append((source, here / 'photos-v70' / relative))
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    print(f'Staged {len(copies)} local reference assets for Photos conform')


if __name__ == '__main__':
    main()
