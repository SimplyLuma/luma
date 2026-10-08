# SPDX-License-Identifier: Apache-2.0

"""Photos sync: the Pictures library, content-addressed.

Each file is identified by its SHA-256. The device sends its whole library as
a manifest, uploads only the bytes the source of truth does not have yet, and
downloads photos other devices added into ``Pictures/Luma Hub``. Hashes are
cached by path, size and modification time, so an unchanged library costs a
directory walk, not a re-read.

Safety rules:
- A library folder that is missing or unreadable is never reported as empty.
- A photo this device ever had — taken here or downloaded — is never
  downloaded again after you delete it.
- Nothing here deletes a file on this device.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

LIBRARY_FOLDER = "Luma Hub"
MAX_PHOTO_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_ITEMS = 20000
_CHUNK = 1024 * 1024

MEDIA_SUFFIXES = frozenset((
    ".avif", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp",
    ".m4v", ".mov", ".mp4", ".webm",
))


class PhotoSyncError(RuntimeError):
    pass


def library_directory(environment: dict[str, str] | None = None) -> Path:
    try:
        from .user_directories import photos_directory
        return Path(photos_directory(environment))
    except ImportError:  # an older overlay without the helper
        env = os.environ if environment is None else environment
        return Path(env.get("HOME", str(Path.home()))) / "Pictures"


def sniff(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:16]
        if any(value in brand for value in (b"avif", b"avis")):
            return "image/avif"
        if any(value in brand for value in (b"heic", b"heix", b"hevc", b"mif1", b"msf1")):
            return "image/heif"
        if b"qt  " in brand:
            return "video/quicktime"
        return "video/mp4"
    return None


def _walk(root: Path):
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        subdirectories[:] = [name for name in subdirectories if not name.startswith(".")]
        for name in files:
            if name.startswith(".") or Path(name).suffix.casefold() not in MEDIA_SUFFIXES:
                continue
            yield Path(directory) / name


def scan(root: Path, cache: dict[str, list]) -> tuple[list[dict], dict[str, list]]:
    """Manifest items for every photo under ``root``, and the refreshed hash cache."""
    if not root.is_dir():
        raise PhotoSyncError(f"The Pictures library at {root} is not available.")
    items, fresh, seen = [], {}, set()
    from datetime import datetime, timezone
    for path in _walk(root):
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError:
            continue
        if not 0 < stat.st_size <= MAX_PHOTO_BYTES:
            continue
        key = str(path)
        cached = cache.get(key)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            sha, mime = cached[2], cached[3]
        else:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    head = stream.read(64)
                    mime = sniff(head)
                    if mime is None:
                        continue
                    digest.update(head)
                    for chunk in iter(lambda: stream.read(_CHUNK), b""):
                        digest.update(chunk)
            except OSError:
                continue
            sha = digest.hexdigest()
        fresh[key] = [stat.st_size, stat.st_mtime_ns, sha, mime]
        if sha in seen:
            continue
        seen.add(sha)
        items.append({
            "sha256": sha, "name": path.name[:255], "size": stat.st_size, "mime": mime,
            "taken_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        })
    if len(items) > MAX_MANIFEST_ITEMS:
        raise PhotoSyncError(f"The library has {len(items)} photos; sync handles {MAX_MANIFEST_ITEMS} for now.")
    return items, fresh


def _unique(folder: Path, name: str) -> Path:
    safe = Path(name).name.lstrip(".") or "photo"
    candidate = folder / safe
    stem, suffix = Path(safe).stem, Path(safe).suffix
    number = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
    return candidate


def sync_photos(*, address: str, token: str, device_id: str, http, state: dict, scope: str,
                environment: dict[str, str] | None, remote_changed: bool) -> str:
    root = library_directory(environment)
    key = f"{scope}|photos"
    record = state.get(key, {})
    if not root.is_dir():
        if int(record.get("count", 0) or 0) > 0:
            raise PhotoSyncError(f"Refusing to sync photos: the Pictures library at {root} is missing. Nothing was sent.")
        return "photos: no Pictures library, skipped"
    items, cache = scan(root, record.get("cache", {}))
    local = {item["sha256"] for item in items}
    manifest_print = hashlib.sha256(json.dumps(sorted(local)).encode()).hexdigest()
    uploaded = downloaded = 0
    if manifest_print != record.get("manifest") or record.get("pending"):
        reply = http.post_json(f"{address}/api/hub/sync/photos/manifest",
                               {"device_id": device_id, "items": items}, token=token)
        by_sha = {item["sha256"]: item for item in items}
        paths = {entry[2]: path for path, entry in cache.items()}
        pending = [sha for sha in reply.get("missing", []) if sha in by_sha]
        for sha in pending:
            path = Path(paths[sha])
            http.put_file(f"{address}/api/hub/sync/photos/blob/{sha}", path, by_sha[sha]["size"], token=token)
            uploaded += 1
        record["pending"] = False
    # Everything this device has ever held, not only what it downloaded.
    downloaded_before = set(record.get("downloaded", [])) | local
    if remote_changed or "downloaded" not in record:
        listing = http.get_json(f"{address}/api/hub/sync/photos", token=token).get("items", [])
        folder = root / LIBRARY_FOLDER
        for item in listing:
            sha = str(item.get("sha256", ""))
            if sha in local or sha in downloaded_before or len(sha) != 64:
                continue
            folder.mkdir(mode=0o755, parents=True, exist_ok=True)
            target = _unique(folder, str(item.get("name", "photo")))
            handle, temporary = tempfile.mkstemp(prefix=".luma-hub-", dir=folder)
            os.close(handle)
            try:
                http.download(f"{address}/api/hub/sync/photos/blob/{sha}", Path(temporary), token=token)
                digest = hashlib.sha256()
                with open(temporary, "rb") as stream:
                    for chunk in iter(lambda: stream.read(_CHUNK), b""):
                        digest.update(chunk)
                if digest.hexdigest() != sha:
                    raise PhotoSyncError("A downloaded photo did not match its fingerprint; it was discarded.")
                os.chmod(temporary, 0o644)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            downloaded_before.add(sha)
            downloaded += 1
    record.update({"manifest": manifest_print, "count": len(items), "cache": cache,
                   "downloaded": sorted(downloaded_before)})
    if downloaded:
        # The new files belong in this device's manifest; the next round sends it.
        record["manifest"] = ""
    state[key] = record
    return f"photos: {len(items)} in library, uploaded {uploaded}, downloaded {downloaded}"
