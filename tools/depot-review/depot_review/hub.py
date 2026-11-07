# SPDX-License-Identifier: Apache-2.0
"""The Hub's pipeline API: claim work, download uploads, report results.

The service token is read from a root-only file and sent only to the Hub's
own origin. Downloads are size-bounded and checked against the digest the Hub
recorded at upload.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

USER_AGENT = "luma-depot-review/1"


class HubError(RuntimeError):
    pass


class Hub:
    def __init__(self, base: str, token_file: str, paths: dict, timeout: int = 60):
        self.base = base.rstrip("/")
        self.origin = urllib.parse.urlsplit(self.base)[:2]
        self.token = Path(token_file).read_text().strip()
        self.paths = paths
        self.timeout = timeout
        self.context = ssl.create_default_context()

    def _url(self, name: str, **values) -> str:
        path = self.paths[name].format(**{k: urllib.parse.quote(str(v), safe="") for k, v in values.items()})
        return path if path.startswith("https://") else self.base + path

    def _headers(self, url: str) -> dict:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if urllib.parse.urlsplit(url)[:2] == self.origin:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def call(self, method: str, name: str, body=None, **values):
        url = self._url(name, **values)
        data = None
        headers = self._headers(url)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
                if response.status == 204:
                    return None
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            if error.code == 204:
                return None
            detail = error.read()[:500].decode(errors="replace")
            raise HubError(f"{method} {name}: HTTP {error.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise HubError(f"{method} {name}: {error}") from None

    def upload_file(self, name: str, path: Path, field: str = "file", content_type: str = "image/png", **values):
        """multipart/form-data POST of one file (the launch screenshot)."""
        url = self._url(name, **values)
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                f"filename=\"{path.name}\"\r\nContent-Type: {content_type}\r\n\r\n").encode()
        body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        headers = self._headers(url)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raise HubError(f"upload {name}: HTTP {error.code} {error.read()[:300]!r}") from None

    def download(self, url: str, target: Path, *, limit: int, sha256: str | None = None) -> str:
        if not url.startswith("https://") and not url.startswith(self.base):
            url = self.base + url
        request = urllib.request.Request(url, headers=self._headers(url))
        digest = hashlib.sha256()
        size = 0
        partial = target.with_name(target.name + ".part")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response, \
                    partial.open("wb") as out:
                while block := response.read(1 << 20):
                    size += len(block)
                    if size > limit:
                        raise HubError(f"download exceeds {limit} bytes: {url}")
                    digest.update(block)
                    out.write(block)
        except urllib.error.HTTPError as error:
            partial.unlink(missing_ok=True)
            raise HubError(f"download {url}: HTTP {error.code}") from None
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        actual = digest.hexdigest()
        if sha256 and actual != sha256.lower():
            partial.unlink(missing_ok=True)
            raise HubError(f"download digest differs for {url}")
        os.replace(partial, target)
        return actual
