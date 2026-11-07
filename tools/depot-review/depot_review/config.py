# SPDX-License-Identifier: Apache-2.0
"""Service configuration: one JSON file, every path explicit."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT = "/etc/luma/depot-review.json"


@dataclass
class Config:
    hub: str = "https://hub.simplyluma.com"
    token_file: str = "/srv/luma-build/depot-keys/secrets/hub-pipeline.token"
    paths: dict = field(default_factory=lambda: {
        "claim_check": "/api/depot/pipeline/checks/claim",
        "report_check": "/api/depot/pipeline/checks/{id}/report",
        "check_screenshot": "/api/depot/pipeline/checks/{id}/screenshot",
        "claim_publish": "/api/depot/pipeline/publish/claim",
        "report_publish": "/api/depot/pipeline/publish/{id}/report",
        "claim_withdraw": "/api/depot/pipeline/withdraw/claim",
        "report_withdraw": "/api/depot/pipeline/withdraw/{id}/report",
        "catalogue": "/api/depot/catalogue",
    })
    # The deployed checkout of SimplyLuma/luma the service runs from.
    source: Path = Path("/srv/luma-build/depot-review/source")
    work: Path = Path("/srv/dev-storage/depot-review/work")
    state: Path = Path("/srv/dev-storage/depot-review/state")
    publish_root: Path = Path("/srv/luma-build/depot-review")
    storage_conf: Path = Path("/srv/dev-storage/depot-review/storage.conf")
    review_image: str = "localhost/luma-depot-review:1"
    signer_image: str = ""
    clamav_db: Path = Path("/srv/dev-storage/depot-review/clamav")
    vm_base: Path = Path("/srv/dev-storage/depot-review/vm/base-x86_64.qcow2")
    vm_key: Path = Path("/srv/dev-storage/depot-review/vm/review-key")
    depot_root: Path = Path("/srv/luma-build/n1105-depot/depot-root")
    depot_keys: Path = Path("/srv/luma-build/depot-keys")
    branch: str = "stable"
    publication_locks: list = field(default_factory=lambda: ["/srv/luma-build/depot-publication.lock"])
    catalogue_key: Path = Path("/srv/luma-build/depot-keys/minisign/depot-catalogue-v1.key")
    catalogue_pub: Path = Path("/srv/luma-build/depot-keys/minisign/depot-catalogue-v1.pub")
    catalogue_dest: str = "depot/catalogue/v1/"
    catalogue_url: str = "https://dl.simplyluma.com/depot/catalogue/v1/catalogue.json"
    media_base: str = "https://media.simplyluma.com/depot/"
    media_prefix: str = "depot/"
    r2_account: str = "30f2bc572e1ef22b91d30d096f6ffdfa"
    r2_bucket: str = "luma-images"
    r2_credentials: Path = Path("/root/.config/luma-maintainer-publisher")
    poll_seconds: int = 20
    catalogue_seconds: int = 60
    # False skips catalogue publishing (rehearsals against a stand-in Hub).
    publish_catalogue: bool = True
    max_upload_bytes: int = 4 * 1024 ** 3
    vm_arches: list = field(default_factory=lambda: ["x86_64"])

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        config = cls()
        file = Path(path or DEFAULT)
        if file.exists():
            for key, value in json.loads(file.read_text()).items():
                if not hasattr(config, key):
                    raise ValueError(f"unknown setting {key}")
                current = getattr(config, key)
                if isinstance(current, Path):
                    value = Path(value)
                elif isinstance(current, dict):
                    value = {**current, **value}
                setattr(config, key, value)
        return config
