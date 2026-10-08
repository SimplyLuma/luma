# SPDX-License-Identifier: Apache-2.0
"""Share existing local audio copies; never resolve a credentialed stream."""
from pathlib import Path
import shutil
from urllib.parse import unquote, urlsplit


def local_files(store, track_ids):
    """One readable local/offline file for every requested track, in order."""
    result = []
    for track_id in track_ids:
        chosen = None
        for copy in store.copies_for_track(track_id):
            if copy.offline_ready:
                candidate = Path(copy.offline_path)
            elif copy.source_local:
                uri = urlsplit(copy.uri)
                if uri.scheme != 'file' or uri.netloc not in ('', 'localhost'):
                    continue
                candidate = Path(unquote(uri.path))
            else:
                continue
            if candidate.is_file():
                chosen = candidate
                break
        if chosen is None:
            raise FileNotFoundError('Download every track for offline listening before sharing this selection.')
        if chosen not in result:
            result.append(chosen)
    if not result:
        raise FileNotFoundError('There are no audio files in this selection.')
    return tuple(result)


def save_copies(paths, destination):
    """Copy bytes into a new folder without overwriting existing user files."""
    destination = Path(destination)
    folder = destination / 'Tide music'
    counter = 1
    while True:
        try:
            folder.mkdir()
            break
        except FileExistsError:
            counter += 1
            folder = destination / f'Tide music {counter}'
    try:
        used = set()
        for path in paths:
            path = Path(path)
            name = path.name
            suffix = 1
            while name in used:
                suffix += 1
                name = f'{path.stem} {suffix}{path.suffix}'
            used.add(name)
            shutil.copy2(path, folder / name)
    except BaseException:
        # This directory was created by this operation, never a user's folder.
        shutil.rmtree(folder)
        raise
    return folder
