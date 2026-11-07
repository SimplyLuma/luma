# SPDX-License-Identifier: Apache-2.0
"""Put immutable objects into Cloudflare R2 (S3 API, SigV4), standard library only."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class R2:
    def __init__(self, account: str, bucket: str, credentials: Path):
        self.host = f"{account}.r2.cloudflarestorage.com"
        self.bucket = bucket
        self.key_id = (credentials / "r2-access-key-id").read_text().strip()
        self.secret = (credentials / "r2-secret-access-key").read_text().strip()

    def _request(self, method: str, key: str, body: bytes = b"", headers: dict | None = None):
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = "/" + self.bucket + "/" + urllib.parse.quote(key, safe="/-_.~")
        payload = hashlib.sha256(body).hexdigest()
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        headers.update({"host": self.host, "x-amz-content-sha256": payload, "x-amz-date": amz_date})
        signed = sorted(headers)
        canonical = "\n".join([method, path, "", "".join(f"{h}:{headers[h].strip()}\n" for h in signed),
                               ";".join(signed), payload])
        scope = f"{date}/auto/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key_bytes = ("AWS4" + self.secret).encode()
        for part in (date, "auto", "s3", "aws4_request"):
            key_bytes = hmac.new(key_bytes, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key_bytes, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.key_id}/{scope}, "
                                    f"SignedHeaders={';'.join(signed)}, Signature={signature}")
        request = urllib.request.Request(f"https://{self.host}{path}", data=body if method == "PUT" else None,
                                         method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def exists(self, key: str) -> bool:
        status, _ = self._request("HEAD", key)
        return status == 200

    def put(self, key: str, data: bytes, content_type: str, immutable: bool = True) -> None:
        cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
        status, body = self._request("PUT", key, data, {"content-type": content_type, "cache-control": cache})
        if status >= 300:
            raise RuntimeError(f"R2 PUT {key}: HTTP {status} {body[:200]!r}")
