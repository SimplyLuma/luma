# SPDX-License-Identifier: Apache-2.0
"""Privacy-bounded remote avatar lookup and disk caching.

The UI keeps AppKit initials as its immediate and permanent fallback. Lookups
never use remote images embedded in email: account photos come from Google's
authenticated profile response, people use a one-way Libravatar hash, and
brand fallback sends only the public sender domain to the resolver.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import threading
import time
from typing import Callable
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


MAX_IMAGE_BYTES = 1024 * 1024
POSITIVE_TTL = 7 * 24 * 60 * 60
NEGATIVE_TTL = 24 * 60 * 60
CONSUMER_DOMAINS = frozenset({
    "aol.com", "fastmail.com", "gmail.com", "googlemail.com", "hey.com",
    "hotmail.com", "icloud.com", "live.com", "mail.com", "me.com",
    "outlook.com", "pm.me", "proton.me", "protonmail.com", "yahoo.com",
})


def _trusted_profile_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and (
        host == "googleusercontent.com"
        or host.endswith(".googleusercontent.com")
        or host == "ggpht.com"
        or host.endswith(".ggpht.com")
    )


def avatar_candidates(address: str, preferred_url: str = "") -> tuple[str, ...]:
    """Return privacy-ordered candidates without exposing a raw address."""

    normalized = address.strip().casefold()
    candidates: list[str] = []
    if preferred_url and _trusted_profile_url(preferred_url):
        candidates.append(preferred_url)
    if "@" not in normalized:
        return tuple(candidates)
    digest = sha256(normalized.encode("utf-8")).hexdigest()
    candidates.append(
        f"https://seccdn.libravatar.org/avatar/{digest}?s=128&d=404"
    )
    domain = normalized.rsplit("@", 1)[1].strip(".")
    if domain and domain not in CONSUMER_DOMAINS:
        candidates.append(
            f"https://unavatar.io/{quote(domain, safe='')}?fallback=false"
        )
    return tuple(candidates)


def _supported_image(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith(b"\x00\x00\x01\x00")
    )


def fetch_avatar(url: str) -> bytes | None:
    request = Request(
        url,
        headers={
            "Accept": "image/png,image/jpeg,image/webp,image/gif,image/x-icon",
            "User-Agent": "Charlie/0.2 avatar resolver",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            if not response.geturl().startswith("https://"):
                return None
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (OSError, ValueError):
        return None
    if len(data) > MAX_IMAGE_BYTES or not _supported_image(data):
        return None
    return data


class AvatarLoader:
    """Deduplicated two-worker avatar resolver with positive/negative TTLs."""

    def __init__(
        self,
        root: Path,
        *,
        fetch: Callable[[str], bytes | None] = fetch_avatar,
    ) -> None:
        self.root = root
        self.fetch = fetch
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="charlie-avatar")
        self._lock = threading.Lock()
        self._waiting: dict[str, list[Callable[[bytes | None], None]]] = {}

    @staticmethod
    def _fresh(path: Path, ttl: int) -> bool:
        try:
            return time.time() - path.stat().st_mtime < ttl
        except OSError:
            return False

    def request(
        self,
        address: str,
        preferred_url: str,
        callback: Callable[[bytes | None], None],
    ) -> None:
        normalized = address.strip().casefold()
        key = sha256(f"{normalized}\0{preferred_url}".encode("utf-8")).hexdigest()
        image = self.root / f"{key}.image"
        missing = self.root / f"{key}.missing"
        if self._fresh(image, POSITIVE_TTL):
            try:
                callback(image.read_bytes())
                return
            except OSError:
                pass
        if self._fresh(missing, NEGATIVE_TTL):
            callback(None)
            return
        with self._lock:
            callbacks = self._waiting.setdefault(key, [])
            callbacks.append(callback)
            if len(callbacks) > 1:
                return
        self._pool.submit(
            self._resolve, key, avatar_candidates(normalized, preferred_url), image, missing
        )

    def _resolve(
        self,
        key: str,
        candidates: tuple[str, ...],
        image: Path,
        missing: Path,
    ) -> None:
        data = next((value for url in candidates if (value := self.fetch(url))), None)
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if data:
                temporary = image.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
                temporary.write_bytes(data)
                temporary.chmod(0o600)
                temporary.replace(image)
                missing.unlink(missing_ok=True)
            else:
                missing.touch(mode=0o600)
                image.unlink(missing_ok=True)
        except OSError:
            pass
        with self._lock:
            callbacks = self._waiting.pop(key, [])
        for callback in callbacks:
            callback(data)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
