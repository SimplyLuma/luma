# SPDX-License-Identifier: Apache-2.0
"""Exercises the libsecret call path itself, with GObject Introspection
faked out: this sandbox has no keyring daemon (and often no PyGObject at
all), but `SecretServiceStore` should still be proven to call
`Secret.password_store_sync`/`password_lookup_sync`/`password_clear_sync`
with the schema and attributes it promises, and never with a raw secret
substituted for the opaque reference.
"""
from __future__ import annotations

import contextlib
import io
import sys
import types
import unittest

from luma_tide.credentials import (
    LEGACY_SCHEMA_NAME,
    REFERENCE_PREFIX,
    SCHEMA_NAME,
    CredentialUnavailable,
    SecretServiceStore,
    new_reference,
)


class _FakeGLibError(Exception):
    pass


class _FakeSchema:
    def __init__(self, name: str, flags: object, attributes: dict) -> None:
        self.name = name
        self.flags = flags
        self.attributes = attributes


class _FakeSecret:
    """Stands in for `gi.repository.Secret`: a real in-memory keyring."""

    SchemaAttributeType = types.SimpleNamespace(STRING=0)
    SchemaFlags = types.SimpleNamespace(NONE=0)
    COLLECTION_DEFAULT = "default"

    class Schema:
        @staticmethod
        def new(name: str, flags: object, attributes: dict) -> _FakeSchema:
            return _FakeSchema(name, flags, attributes)

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        # Keyed by (schema name, reference): a real keyring distinguishes
        # entries by schema too, and the legacy-fallback tests below rely on
        # that — the same reference string can exist under both schemas at
        # once, as separate secrets.
        self._vault: dict[tuple[str, str], str] = {}
        self.fail_next_store = False

    def password_store_sync(self, schema, attributes, collection, label, secret, _cancellable):
        self.calls.append(("store", attributes["reference"], label, schema.name))
        if self.fail_next_store:
            self.fail_next_store = False
            return False
        self._vault[(schema.name, attributes["reference"])] = secret
        return True

    def password_lookup_sync(self, schema, attributes, _cancellable):
        self.calls.append(("lookup", attributes["reference"], schema.name))
        return self._vault.get((schema.name, attributes["reference"]))

    def password_clear_sync(self, schema, attributes, _cancellable):
        self.calls.append(("clear", attributes["reference"], schema.name))
        self._vault.pop((schema.name, attributes["reference"]), None)


def _install_fake_gi(secret: _FakeSecret) -> None:
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.GLib = types.SimpleNamespace(Error=_FakeGLibError)
    repository.Secret = secret
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository


class SecretServiceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = _FakeSecret()
        _install_fake_gi(self.secret)

    def tearDown(self) -> None:
        sys.modules.pop("gi", None)
        sys.modules.pop("gi.repository", None)

    def test_store_and_lookup_round_trip_through_the_schema(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()
        self.assertTrue(reference.startswith(REFERENCE_PREFIX))
        store.store(reference, "Tide: listener on music.example.com", "s3cret")
        self.assertEqual(store.lookup(reference), "s3cret")
        self.assertEqual(
            [call[0] for call in self.secret.calls], ["store", "lookup"]
        )
        # Only the opaque reference was used as the lookup key; the schema is
        # exactly the one Tide declares, not some ad-hoc attribute set.
        for _op, used_reference, *_rest in self.secret.calls:
            self.assertEqual(used_reference, reference)

    def test_clear_removes_the_secret(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()
        store.store(reference, "label", "s3cret")
        store.clear(reference)
        self.assertIsNone(store.lookup(reference))
        # The final lookup misses under the current schema and then falls
        # back to (and also misses under) the legacy one — see
        # LegacySchemaMigrationTests for that fallback's own coverage.
        self.assertEqual(
            [call[0] for call in self.secret.calls], ["store", "clear", "lookup", "lookup"]
        )

    def test_store_failure_raises_a_safe_to_show_error_and_keeps_nothing(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()
        self.secret.fail_next_store = True
        with self.assertRaises(CredentialUnavailable):
            store.store(reference, "label", "s3cret")
        self.assertIsNone(store.lookup(reference))

    def test_a_non_reference_string_is_never_sent_to_the_keyring(self) -> None:
        store = SecretServiceStore()
        with self.assertRaises(ValueError):
            store.store("not-an-opaque-reference", "label", "s3cret")
        self.assertEqual(self.secret.calls, [])


class LegacySchemaMigrationTests(unittest.TestCase):
    """A dev-preview build of Tide (with working Subsonic support before
    shipped Tide had it) stored a source's password under the older
    `LEGACY_SCHEMA_NAME` schema, keyed by the exact same opaque `reference`
    string. `SecretServiceStore.lookup` must find it there transparently,
    migrate it into the current schema, and never let the secret value
    itself leak into any log, print, or exception message along the way."""

    def setUp(self) -> None:
        self.secret = _FakeSecret()
        _install_fake_gi(self.secret)

    def tearDown(self) -> None:
        sys.modules.pop("gi", None)
        sys.modules.pop("gi.repository", None)

    def test_a_reference_found_only_under_the_old_schema_is_migrated_and_returned(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()
        # Seed the fake keyring the way a preview install would already have
        # it: under the old schema, never through the new store's API.
        self.secret._vault[(LEGACY_SCHEMA_NAME, reference)] = "s3cret"

        self.assertEqual(store.lookup(reference), "s3cret")

        # Migrated into the current schema...
        self.assertEqual(self.secret._vault.get((SCHEMA_NAME, reference)), "s3cret")
        # ...and the old entry is gone.
        self.assertNotIn((LEGACY_SCHEMA_NAME, reference), self.secret._vault)

        # A second lookup now hits the current schema directly; no more
        # fallback to the legacy schema is needed.
        self.secret.calls.clear()
        self.assertEqual(store.lookup(reference), "s3cret")
        self.assertEqual([call[0] for call in self.secret.calls], ["lookup"])
        self.assertEqual(self.secret.calls[0][2], SCHEMA_NAME)

    def test_a_reference_under_neither_schema_returns_none_and_touches_nothing(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()

        self.assertIsNone(store.lookup(reference))

        # Both schemas were consulted (the miss under the current one, then
        # the fallback), but nothing was ever written or cleared.
        self.assertEqual([call[0] for call in self.secret.calls], ["lookup", "lookup"])
        self.assertEqual(self.secret._vault, {})

    def test_a_reference_already_under_the_new_schema_never_touches_the_old_one(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()
        store.store(reference, "label", "s3cret")
        self.secret.calls.clear()

        self.assertEqual(store.lookup(reference), "s3cret")

        # Only one lookup happened at all, and it was against the current
        # schema — the legacy schema was never consulted.
        self.assertEqual([call[0] for call in self.secret.calls], ["lookup"])
        self.assertEqual(self.secret.calls[0][2], SCHEMA_NAME)

    def test_the_migrated_value_never_appears_in_captured_output(self) -> None:
        store = SecretServiceStore()
        reference = new_reference()
        secret_value = "correct-horse-battery-staple"  # noqa: S105 - test fixture, not a real secret
        self.secret._vault[(LEGACY_SCHEMA_NAME, reference)] = secret_value

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = store.lookup(reference)

        self.assertEqual(result, secret_value)
        self.assertNotIn(secret_value, stdout.getvalue())
        self.assertNotIn(secret_value, stderr.getvalue())

    def test_a_failed_migration_still_returns_the_legacy_value_and_leaves_it_in_place(self) -> None:
        """If the write into the new schema fails (locked keyring, say), the
        caller should still get the secret back this time, and the old entry
        must not be cleared — losing both copies would be worse than a
        migration that just tries again next lookup."""
        store = SecretServiceStore()
        reference = new_reference()
        self.secret._vault[(LEGACY_SCHEMA_NAME, reference)] = "s3cret"
        self.secret.fail_next_store = True

        self.assertEqual(store.lookup(reference), "s3cret")

        self.assertNotIn((SCHEMA_NAME, reference), self.secret._vault)
        self.assertEqual(self.secret._vault.get((LEGACY_SCHEMA_NAME, reference)), "s3cret")


if __name__ == "__main__":
    unittest.main()
