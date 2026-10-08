# SPDX-License-Identifier: Apache-2.0
"""Source passwords, kept only in the system secret service.

A remote source row holds an opaque `auth_ref`; the credential lives behind
it in the user's keyring (libsecret / org.freedesktop.secrets), never in
Tide's own database, logs, or exception messages. There is no plaintext
fallback: when the secret service is missing or locked, Tide says so and
does not save the credential anywhere else.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import uuid
from typing import Protocol

SCHEMA_NAME = "org.projectluma.Tide.SourceCredential"
# A dev-preview build of Tide shipped Subsonic support before the real,
# shipped Tide did. It stored the exact same kind of secret, keyed by the
# same opaque `reference` string, under this older schema name.
# `SecretServiceStore.lookup` falls back to it transparently and migrates
# what it finds, so a preview user's saved sign-in survives the upgrade.
LEGACY_SCHEMA_NAME = "org.projectluma.Tide.SourcePassword"
REFERENCE_PREFIX = "secret-service:"

_log = logging.getLogger("tide")


class CredentialUnavailable(Exception):
    """The keyring could not store or return a secret. Safe to show."""


def new_reference() -> str:
    """A fresh opaque reference, unrelated to the source's own identity."""
    return f"{REFERENCE_PREFIX}{uuid.uuid4()}"


def reference_for(source_id: str) -> str:
    """The keyring reference for a source's saved sign-in. It is derived from
    the source's identity, so signing in again replaces the same entry."""
    return f"{REFERENCE_PREFIX}{source_id}"


class CredentialStore(Protocol):
    def store(self, reference: str, label: str, secret: str) -> None: ...
    def lookup(self, reference: str) -> str | None: ...
    def clear(self, reference: str) -> None: ...


class SecretServiceStore:
    """libsecret's synchronous API; it may wait for an unlock prompt, so call
    it from a worker thread, never Tide's GTK thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        try:
            import gi

            gi.require_version("Secret", "1")
            from gi.repository import GLib, Secret
        except (ImportError, ValueError):
            raise CredentialUnavailable(
                "This system has no keyring service, so Tide can't keep the sign-in safely."
            ) from None
        self.GLib = GLib
        self.Secret = Secret
        self.schema = Secret.Schema.new(
            SCHEMA_NAME,
            Secret.SchemaFlags.NONE,
            {"reference": Secret.SchemaAttributeType.STRING},
        )
        self._legacy_schema = Secret.Schema.new(
            LEGACY_SCHEMA_NAME,
            Secret.SchemaFlags.NONE,
            {"reference": Secret.SchemaAttributeType.STRING},
        )

    def _attributes(self, reference: str) -> dict[str, str]:
        if not reference.startswith(REFERENCE_PREFIX):
            raise ValueError("not a secret-service reference")
        return {"reference": reference}

    def store(self, reference: str, label: str, secret: str) -> None:
        with self._lock:
            try:
                stored = self.Secret.password_store_sync(
                    self.schema, self._attributes(reference), self.Secret.COLLECTION_DEFAULT,
                    label, secret, None,
                )
            except self.GLib.Error:
                stored = False
        if not stored:
            raise CredentialUnavailable(
                "Tide couldn't save this in the keyring. Unlock the keyring and try again."
            )

    def lookup(self, reference: str) -> str | None:
        with self._lock:
            try:
                value = self.Secret.password_lookup_sync(
                    self.schema, self._attributes(reference), None
                )
            except self.GLib.Error:
                raise CredentialUnavailable(
                    "Tide couldn't read the sign-in from the keyring. Unlock the keyring and try again."
                ) from None
            if value is not None:
                return value
            return self._lookup_legacy_and_migrate(reference)

    def _lookup_legacy_and_migrate(self, reference: str) -> str | None:
        """Called with `self._lock` already held, on a miss under the
        current schema. A dev-preview build stored the same reference under
        `LEGACY_SCHEMA_NAME` (see its module docstring); if that's where the
        secret is, copy it under the current schema, drop the old entry, and
        return it as if the primary lookup had succeeded. Never found under
        either schema: behaves exactly as it does today, and returns None."""
        try:
            legacy_value = self.Secret.password_lookup_sync(
                self._legacy_schema, self._attributes(reference), None
            )
        except self.GLib.Error:
            raise CredentialUnavailable(
                "Tide couldn't read the sign-in from the keyring. Unlock the keyring and try again."
            ) from None
        if legacy_value is None:
            return None
        try:
            migrated = self.Secret.password_store_sync(
                self.schema, self._attributes(reference), self.Secret.COLLECTION_DEFAULT,
                "Tide: source sign-in (migrated from preview build)", legacy_value, None,
            )
        except self.GLib.Error:
            migrated = False
        if migrated:
            _log.info("Moved a saved Tide sign-in to the current keyring schema")
            # Best effort: the value is already usable below either way, and
            # a leftover legacy entry just means this migration is retried
            # (harmlessly) on the next lookup.
            with contextlib.suppress(self.GLib.Error):
                self.Secret.password_clear_sync(
                    self._legacy_schema, self._attributes(reference), None
                )
        return legacy_value

    def clear(self, reference: str) -> None:
        with self._lock:
            try:
                self.Secret.password_clear_sync(self.schema, self._attributes(reference), None)
            except self.GLib.Error:
                raise CredentialUnavailable(
                    "Tide couldn't remove the saved sign-in from the keyring."
                ) from None


class MemoryCredentialStore:
    """For tests and fixtures only; never used by the shipped application."""

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def store(self, reference: str, label: str, secret: str) -> None:
        del label
        self.secrets[reference] = secret

    def lookup(self, reference: str) -> str | None:
        return self.secrets.get(reference)

    def clear(self, reference: str) -> None:
        self.secrets.pop(reference, None)
