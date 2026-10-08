# SPDX-License-Identifier: Apache-2.0
"""Secret Service boundary.

Only opaque account IDs and credential kinds become Secret Service attributes.
Secret values never enter application configuration, logs or process arguments.
"""
from __future__ import annotations

from . import APP_ID


class SecretStore:
    def __init__(self) -> None:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        self.Secret = Secret
        self.schema = Secret.Schema.new(
            APP_ID,
            Secret.SchemaFlags.NONE,
            {
                "account": Secret.SchemaAttributeType.STRING,
                "kind": Secret.SchemaAttributeType.STRING,
            },
        )

    def store(self, account_id: str, kind: str, value: str) -> None:
        self.Secret.password_store_sync(
            self.schema,
            {"account": account_id, "kind": kind},
            self.Secret.COLLECTION_DEFAULT,
            f"Charlie {kind} for {account_id}",
            value,
            None,
        )

    def lookup(self, account_id: str, kind: str) -> str | None:
        return self.Secret.password_lookup_sync(
            self.schema, {"account": account_id, "kind": kind}, None
        )

    def clear(self, account_id: str, kind: str) -> bool:
        return bool(
            self.Secret.password_clear_sync(
                self.schema, {"account": account_id, "kind": kind}, None
            )
        )
