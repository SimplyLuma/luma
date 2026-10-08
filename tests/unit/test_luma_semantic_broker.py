# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / "src/luma-platform/broker"
sys.path.insert(0, str(BROKER))

from luma_semantic_broker.core import (  # noqa: E402
    MAX_SURFACES_PER_APPLICATION,
    BrokerCore,
    BrokerError,
)
from luma_semantic_broker.model import Grant, Identity, IdentityStrength, Scope  # noqa: E402
from luma_semantic_broker.policy import RateLimiter  # noqa: E402
from luma_semantic_broker.store import AuditStore, GrantStore, StoreError  # noqa: E402
from luma_semantic_broker.validation import (  # noqa: E402
    MAX_SURFACE_BYTES,
    ValidationError,
    semantic_surface,
)


def surface(*, privacy: str = "public", children: list | None = None, risk: str = "low") -> dict:
    return {
        "schema_version": "0.1",
        "id": "notes",
        "kind": "application",
        "name": "Notes",
        "privacy": privacy,
        "actions": [
            {
                "id": "notes.create",
                "label": "Create note",
                "risk": risk,
                "enabled": True,
            }
        ],
        "children": children or [],
    }


def live_extension(*, privacy: str = "private", expires_at: datetime | None = None) -> dict:
    return {
        "schema_version": "0.1",
        "id": "notes.current-activity",
        "app_id": "org.projectluma.Notes",
        "category": "generic",
        "title": "Writing release notes",
        "subtitle": "Project Luma",
        "privacy": privacy,
        "progress": -1.0,
        "actions": [],
        "starts_at": datetime(2026, 8, 27, 12, tzinfo=UTC).isoformat(),
        "expires_at": (expires_at or datetime(2026, 8, 27, 13, tzinfo=UTC)).isoformat(),
    }


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 27, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class BrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.clock = Clock()
        self.locked = False
        self.core = BrokerCore(
            grant_store=GrantStore(root / "grants.json"),
            audit_store=AuditStore(root / "audit.jsonl"),
            locked=lambda: self.locked,
            clock=self.clock,
        )
        self.provider = Identity(
            "native:org.projectluma.Notes",
            "org.projectluma.Notes",
            "Notes",
            ":1.10",
            os.getuid(),
            1010,
            IdentityStrength.MANAGED_NATIVE,
        )
        self.agent = Identity(
            "flatpak:org.example.Agent",
            "org.example.Agent",
            "Example Agent",
            ":1.20",
            os.getuid(),
            2020,
            IdentityStrength.SANDBOXED,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish(self, value: dict | None = None):
        return self.core.register_surface(
            self.provider,
            "org.projectluma.Notes",
            "notes",
            value or surface(),
            "/org/projectluma/Notes/Semantics",
        )

    def publish_live(self, value: dict | None = None):
        return self.core.register_live_extension(
            self.provider,
            "org.projectluma.Notes",
            "notes.current-activity",
            value or live_extension(),
            "/org/projectluma/Notes/LiveExtension",
        )

    def test_live_extension_is_bounded_redacted_and_expires(self) -> None:
        published = self.publish_live()
        visible = self.core.list_live_extensions()
        self.assertEqual(visible[0]["extension"]["title"], "Writing release notes")
        self.locked = True
        redacted = self.core.list_live_extensions()
        self.assertEqual(redacted[0]["extension"]["title"], "Ongoing activity")
        self.assertNotIn("subtitle", redacted[0]["extension"])
        self.assertEqual(redacted[0]["extension"]["actions"], [])
        self.locked = False
        self.clock.value += timedelta(hours=2)
        self.assertEqual(self.core.list_live_extensions(), ())
        self.assertNotIn(published.publication_id, self.core.live_extensions)

    def test_live_extension_rejects_spoofing_and_long_lifetime(self) -> None:
        spoofed = live_extension()
        spoofed["app_id"] = "org.projectluma.Calendar"
        with self.assertRaisesRegex(ValidationError, "identity"):
            self.publish_live(spoofed)
        with self.assertRaisesRegex(ValidationError, "expiry"):
            self.publish_live(
                live_extension(expires_at=self.clock.value + timedelta(days=8))
            )

    def test_live_extension_rejects_unbounded_or_unsafe_panel_text(self) -> None:
        oversized_title = live_extension()
        oversized_title["title"] = "T" * 257
        with self.assertRaisesRegex(ValidationError, "title exceeds"):
            self.publish_live(oversized_title)

        oversized_subtitle = live_extension()
        oversized_subtitle["subtitle"] = "S" * 513
        with self.assertRaisesRegex(ValidationError, "subtitle exceeds"):
            self.publish_live(oversized_subtitle)

        bidi_title = live_extension()
        bidi_title["title"] = "Meeting\u202eexe"
        with self.assertRaisesRegex(ValidationError, "unsafe control"):
            self.publish_live(bidi_title)

    def test_disconnect_removes_live_extension(self) -> None:
        published = self.publish_live()
        self.core.disconnect(self.provider.sender)
        self.assertNotIn(published.publication_id, self.core.live_extensions)

    def grant(self, *requested: Scope, persistent: bool = False) -> None:
        self.core.grant_access(
            self.agent,
            "org.projectluma.Notes",
            [scope.value for scope in requested],
            lifetime_seconds=3600,
            persistent=persistent,
        )

    def test_spoofed_provider_application_is_rejected(self) -> None:
        with self.assertRaisesRegex(BrokerError, "not authorized"):
            self.core.register_surface(
                self.provider,
                "org.projectluma.Calendar",
                "notes",
                surface(),
                "/org/projectluma/Notes/Semantics",
            )

    def test_secret_object_is_rejected_before_broker_storage(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must not be serialized"):
            self.publish(surface(privacy="secret"))
        self.assertEqual(self.core.surfaces, {})

    def test_unknown_fields_and_oversized_payloads_are_rejected(self) -> None:
        malformed = surface()
        malformed["surprise"] = "payload"
        with self.assertRaises(ValidationError):
            semantic_surface(malformed)
        oversized = surface()
        oversized["name"] = "x" * (MAX_SURFACE_BYTES + 1)
        with self.assertRaises(ValidationError):
            semantic_surface(oversized)
        misleading = surface()
        misleading["name"] = "Notes\u202eSystem"
        with self.assertRaisesRegex(ValidationError, "unsafe control"):
            semantic_surface(misleading)
        multiline_action = surface()
        multiline_action["actions"][0]["label"] = "Create\nAllow everything"
        with self.assertRaisesRegex(ValidationError, "unsafe control"):
            semantic_surface(multiline_action)

    def test_private_descendants_are_filtered_without_private_scope(self) -> None:
        private_note = {
            "schema_version": "0.1",
            "id": "note:one",
            "kind": "document",
            "name": "Private title",
            "privacy": "private",
            "actions": [],
            "children": [],
        }
        self.publish(surface(children=[private_note]))
        self.grant(Scope.OBSERVE_PUBLIC)
        visible = self.core.list_surfaces(self.agent)
        self.assertEqual(visible[0]["surface"]["children"], [])

    def test_private_action_cannot_be_invoked_by_guessing_its_identifier(self) -> None:
        private_note = {
            "schema_version": "0.1",
            "id": "note:one",
            "kind": "document",
            "name": "Private title",
            "privacy": "private",
            "actions": [
                {
                    "id": "notes.delete",
                    "label": "Move note to Trash",
                    "risk": "destructive",
                    "enabled": True,
                }
            ],
            "children": [],
        }
        published = self.publish(surface(children=[private_note]))
        self.grant(Scope.OBSERVE_PUBLIC, Scope.INVOKE_DESTRUCTIVE)
        decision, _surface, _action = self.core.authorize_invocation(
            self.agent,
            published.publication_id,
            "note:one",
            "notes.delete",
            confirmed=True,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "missing observe-private")

    def test_locked_session_denies_observation_and_invocation(self) -> None:
        published = self.publish()
        self.grant(Scope.OBSERVE_PUBLIC, Scope.INVOKE_LOW_RISK)
        self.locked = True
        self.assertEqual(self.core.list_surfaces(self.agent), ())
        decision, _published, _action = self.core.authorize_invocation(
            self.agent,
            published.publication_id,
            "notes",
            "notes.create",
            confirmed=True,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "session is locked")

    def test_destructive_action_needs_scope_and_fresh_confirmation(self) -> None:
        published = self.publish(surface(risk="destructive"))
        self.grant(Scope.OBSERVE_PUBLIC, Scope.INVOKE_DESTRUCTIVE)
        denied, _published, _action = self.core.authorize_invocation(
            self.agent,
            published.publication_id,
            "notes",
            "notes.create",
            confirmed=False,
        )
        allowed, _published, _action = self.core.authorize_invocation(
            self.agent,
            published.publication_id,
            "notes",
            "notes.create",
            confirmed=True,
        )
        self.assertTrue(denied.confirmation_required)
        self.assertFalse(denied.allowed)
        self.assertTrue(allowed.allowed)

    def test_expired_and_revoked_grants_fail_closed(self) -> None:
        self.publish()
        self.grant(Scope.OBSERVE_PUBLIC)
        self.clock.value += timedelta(hours=2)
        self.assertEqual(self.core.list_surfaces(self.agent), ())
        self.grant(Scope.OBSERVE_PUBLIC)
        self.core.revoke_access(self.agent, "org.projectluma.Notes")
        self.assertEqual(self.core.list_surfaces(self.agent), ())

    def test_session_grant_is_not_written_during_later_store_update(self) -> None:
        self.grant(Scope.OBSERVE_PUBLIC, persistent=False)
        second = Identity(
            "flatpak:org.example.Second",
            "org.example.Second",
            "Second Agent",
            ":1.21",
            os.getuid(),
            2021,
            IdentityStrength.SANDBOXED,
        )
        self.core.grant_access(
            second,
            "org.projectluma.Notes",
            [Scope.OBSERVE_PUBLIC.value],
            lifetime_seconds=3600,
            persistent=True,
        )
        stored = self.core.grant_store.load()
        self.assertEqual([grant.client_key for grant in stored], [second.key])

    def test_native_identities_cannot_receive_persistent_access(self) -> None:
        identities = (
            self.provider,
            Identity(
                "transient:1000::1.99",
                "",
                "Unverified native application",
                ":1.99",
                os.getuid(),
                9999,
                IdentityStrength.TRANSIENT_NATIVE,
            ),
        )
        for identity in identities:
            with self.subTest(strength=identity.strength):
                with self.assertRaisesRegex(BrokerError, "session access only"):
                    self.core.grant_access(
                        identity,
                        "org.projectluma.Notes",
                        [Scope.OBSERVE_PUBLIC.value],
                        lifetime_seconds=3600,
                        persistent=True,
                    )

    def test_provider_disconnect_removes_owned_surfaces(self) -> None:
        self.publish()
        self.core.disconnect(self.provider.sender)
        self.assertEqual(self.core.surfaces, {})

    def test_application_cannot_publish_unbounded_surfaces(self) -> None:
        for index in range(MAX_SURFACES_PER_APPLICATION):
            value = surface()
            value["id"] = f"notes-{index}"
            self.core.register_surface(
                self.provider,
                "org.projectluma.Notes",
                value["id"],
                value,
                "/org/projectluma/Notes/Semantics",
            )
        overflow = surface()
        overflow["id"] = "notes-overflow"
        with self.assertRaisesRegex(BrokerError, "application publication limit"):
            self.core.register_surface(
                self.provider,
                "org.projectluma.Notes",
                overflow["id"],
                overflow,
                "/org/projectluma/Notes/Semantics",
            )

    def test_rate_limit_is_per_authenticated_identity(self) -> None:
        limiter = RateLimiter(limit=2, window_seconds=10)
        self.assertTrue(limiter.allow("a", now=0))
        self.assertTrue(limiter.allow("a", now=1))
        self.assertFalse(limiter.allow("a", now=2))
        self.assertTrue(limiter.allow("b", now=2))
        self.assertTrue(limiter.allow("a", now=11))

    def test_store_permissions_and_audit_omit_surface_payload(self) -> None:
        self.publish()
        self.grant(Scope.OBSERVE_PUBLIC, persistent=True)
        self.core.list_surfaces(self.agent)
        self.assertEqual(stat.S_IMODE(self.core.grant_store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.core.audit_store.path.stat().st_mode), 0o600)
        audit_text = self.core.audit_store.path.read_text(encoding="utf-8")
        self.assertNotIn("Notes", audit_text)
        event = json.loads(audit_text.splitlines()[-1])
        self.assertEqual(
            set(event),
            {"allowed", "client_key", "operation", "reason", "target", "timestamp"},
        )

    def test_broad_or_corrupt_grant_store_fails_closed(self) -> None:
        path = Path(self.temporary.name) / "unsafe.json"
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o644)
        with self.assertRaisesRegex(StoreError, "too broad"):
            GrantStore(path).load()

    def test_persisted_grant_cannot_extend_beyond_policy_maximum(self) -> None:
        path = Path(self.temporary.name) / "overlong.json"
        store = GrantStore(path)
        store.save(
            [
                Grant(
                    self.agent.key,
                    "org.projectluma.Notes",
                    frozenset({Scope.OBSERVE_PUBLIC}),
                    self.clock.value + timedelta(days=31),
                    True,
                )
            ]
        )
        with self.assertRaisesRegex(BrokerError, "invalid lifetime"):
            BrokerCore(
                grant_store=store,
                audit_store=AuditStore(Path(self.temporary.name) / "late-audit"),
                locked=lambda: False,
                clock=self.clock,
            )


if __name__ == "__main__":
    unittest.main()
