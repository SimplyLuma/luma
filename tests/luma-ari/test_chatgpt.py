# SPDX-License-Identifier: Apache-2.0
"""OAuth protocol tests use fresh test signing keys and disposable storage."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from ari import chatgpt

DISCOVERY = {"issuer": chatgpt.ISSUER, "authorization_endpoint": chatgpt.ISSUER + "/api/accounts/authorize",
             "token_endpoint": chatgpt.ISSUER + "/api/accounts/oauth/token", "jwks_uri": chatgpt.ISSUER + "/.well-known/jwks.json",
             "revocation_endpoint": chatgpt.ISSUER + "/oauth/revoke"}


class MemoryKeyring:
    def __init__(self):
        self.value = None

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)


class TestTransport:
    """Official-looking URLs terminate in this stand-in, never the network."""
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid="test-key", alg="RS256", use="sig")
        self.nonce, self.subject, self.scope = "", "test-subject", chatgpt.SCOPES
        self.email, self.audience, self.issuer = "fixture@example.invalid", None, chatgpt.ISSUER
        self.requests, self.refresh_count = [], 0
        self.refresh_error = None
        self.expired, self.tampered, self.revoked = False, False, False
        self.code_entered, self.code_release = None, None

    def discovery(self):
        return dict(DISCOVERY)

    def json(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if url == DISCOVERY["jwks_uri"]:
            return {"keys": [self.jwk]}
        if url == DISCOVERY["token_endpoint"]:
            form = kwargs["form"]
            if form["grant_type"] == "refresh_token":
                self.refresh_count += 1
                if self.refresh_error:
                    raise self.refresh_error
                return {"access_token": "test-access-rotated", "refresh_token": "test-refresh-rotated",
                        "expires_in": 3600, "token_type": "Bearer", "scope": self.scope}
            if self.code_entered:
                self.code_entered.set()
                assert self.code_release.wait(3)
            now = int(time.time())
            claims = {"iss": self.issuer, "sub": self.subject, "aud": self.audience or form["client_id"],
                      "exp": now - 20 if self.expired else now + 3600, "iat": now, "nonce": self.nonce, "email": self.email}
            encoded = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})
            if self.tampered:
                encoded = encoded.rsplit(".", 1)[0] + "." + "A" * 342
            return {"access_token": "test-access", "refresh_token": "test-refresh", "token_type": "Bearer",
                    "expires_in": 3600, "scope": self.scope, "id_token": encoded}
        if url == chatgpt.API + "/models":
            return {"models": [{"slug": "test-model", "display_name": "Test model", "visibility": "list"},
                               {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"}]}
        raise AssertionError(url)

    def open(self, url, **kwargs):
        assert url == DISCOVERY["revocation_endpoint"]
        assert kwargs["form"]["token_type_hint"] == "refresh_token"
        self.revoked = True
        response = io.BytesIO(b"")
        response.status = 200
        return response


class Accounts(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.keyring, self.transport = MemoryKeyring(), TestTransport()
        self.manager = chatgpt.Manager(keyring=self.keyring, transport=self.transport,
                                       lock_path=Path(self.scratch.name) / "session")
        self.addCleanup(self.cancel)

    def cancel(self):
        if self.manager.login:
            self.manager.cancel(self.manager.login.id)

    def start(self, profile=""):
        attempt = self.manager.start(profile)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(attempt["url"]).query)
        self.transport.nonce = query["nonce"][0]
        return attempt, query

    def callback(self, query, **changes):
        params = {"state": query["state"][0], "code": "test-code", "client_id": "oaiapp_test"}
        params.update(changes)
        url = query["redirect_uri"][0] + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=4) as response:
            return response.read()

    def connect(self):
        _, query = self.start()
        self.callback(query)
        self.assertEqual(self.manager.pending()["status"], "complete")
        return self.manager.cached["active"]

    def test_effort_is_persisted_per_model_and_account_and_revalidated(self):
        first = self.connect()
        with self.manager.vault() as vault:
            vault['accounts'][first]['models'] = [{'id':'test-model', 'name':'Test',
                'supported_reasoning_levels':[{'effort':'low'}, {'effort':'high'}],
                'default_reasoning_level':'low'}]
            self.keyring.save(vault)
        self.manager.set_effort('test-model', 'high')
        restored = chatgpt.Manager(keyring=self.keyring, transport=self.transport,
                                  lock_path=Path(self.scratch.name)/'session')
        restored.status()
        self.assertEqual(restored.effort('test-model'), 'high')
        with self.assertRaises(chatgpt.ChatGPTError):
            restored.set_effort('test-model', 'unsupported')
        self.transport.subject = 'different-person'
        _, query = self.start()
        self.callback(query, client_id='oaiapp_second_effort')
        self.assertEqual(self.manager.pending()['status'], 'complete')
        second = self.manager.cached['active']
        self.assertNotEqual(first, second)
        self.assertIsNone(self.manager.effort('test-model'))
        self.manager.select(first)
        self.assertEqual(self.manager.effort('test-model'), 'high')
        # A changed capability list cannot send a stale saved level.
        with self.manager.vault() as vault:
            vault['accounts'][first]['models'][0]['supported_reasoning_levels'] = [{'effort':'low'}]
            self.keyring.save(vault)
        self.assertEqual(self.manager.effort('test-model'), 'low')

    def test_registration_pkce_identity_and_public_status(self):
        attempt, query = self.start()
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["agent_name_hint"], ["Luma Ari"])
        self.assertEqual(query["resource"], [chatgpt.API])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(set(query["scope"][0].split()), set(chatgpt.SCOPES.split()))
        self.assertTrue(query["redirect_uri"][0].startswith("http://127.0.0.1:"))
        self.callback(query)
        self.manager.status(True)
        status = json.dumps(self.manager.cached)
        for secret in ("test-access", "test-refresh", "id_token", "client_id", "test-subject"):
            self.assertNotIn(secret, status)
        self.assertEqual(self.manager.cached["models"], [{"id": "test-model", "name": "Test model"}])
        form = next(kwargs["form"] for url, kwargs in self.transport.requests if url == DISCOVERY["token_endpoint"])
        self.assertEqual(form["redirect_uri"], query["redirect_uri"][0])
        verifier = form["code_verifier"]
        import base64, hashlib
        self.assertEqual(base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
                         query["code_challenge"][0])

    def test_invalid_state_and_wrong_path_do_not_consume_sign_in(self):
        _, query = self.start()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.callback(query, state="wrong")
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.manager.pending()["status"], "waiting")
        self.assertFalse(self.transport.requests)
        wrong = query["redirect_uri"][0].replace("/auth/callback", "/wrong") + "?state=" + query["state"][0]
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(wrong, timeout=3)
        error.exception.close()
        self.callback(query)
        self.assertEqual(self.manager.pending()["status"], "complete")

    def test_decline_and_missing_registration_do_not_save_credentials(self):
        for changes in ({"error": "access_denied"}, {"client_id": "dynamic_agent_client"}):
            _, query = self.start()
            self.callback(query, **changes)
            self.assertEqual(self.manager.pending()["status"], "failed")
            self.assertEqual(self.keyring.value["accounts"], {})

    def test_keyring_failure_cannot_start_signin_or_publish_an_account_switch(self):
        with patch.object(self.keyring, 'save', side_effect=chatgpt.ChatGPTError('Unlock the keyring')), \
             patch.object(chatgpt, 'HTTPServer') as listener:
            with self.assertRaises(chatgpt.ChatGPTError):
                self.manager.start()
        listener.assert_not_called()
        self.assertIsNone(self.manager.login)
        profile = self.connect()
        _, query = self.start()
        self.callback(query, client_id="second-fixture-registration")
        self.manager.status(True)
        original = copy.deepcopy(self.manager.cached)
        with patch.object(self.keyring, 'save', side_effect=chatgpt.ChatGPTError('Unlock the keyring')):
            with self.assertRaises(chatgpt.ChatGPTError):
                self.manager.select(profile)
        self.assertEqual(self.manager.cached, original)

    def test_unverified_identities_never_replace_the_active_account(self):
        profile = self.connect()
        original = copy.deepcopy(self.keyring.value)
        for name, value in (("audience", "other-client"), ("issuer", "https://example.invalid"),
                            ("expired", True), ("tampered", True), ("subject", "other-person")):
            with self.subTest(name=name):
                previous = getattr(self.transport, name)
                setattr(self.transport, name, value)
                _, query = self.start(profile)
                self.callback(query)
                self.assertEqual(self.manager.pending()["status"], "failed")
                self.assertEqual(self.keyring.value, original)
                setattr(self.transport, name, previous)

    def test_nonce_mismatch_rejected(self):
        _, query = self.start()
        self.transport.nonce = "wrong-nonce"
        self.callback(query)
        self.assertFalse(self.keyring.value["accounts"])

    def test_identity_only_grant_cannot_make_inference_requests(self):
        self.transport.scope = "openid profile email offline_access"
        profile = self.connect()
        self.assertFalse(self.manager.cached["accounts"][0]["plan_enabled"])
        with self.assertRaisesRegex(chatgpt.ChatGPTError, "Allow Ari"):
            self.manager.access(profile)
        self.assertFalse(any(url == chatgpt.API + "/models" for url, _ in self.transport.requests))

    def test_identity_only_signin_without_offline_permission_is_retained(self):
        self.transport.scope = "openid profile email"
        original = self.transport.json
        def without_refresh(url, **kwargs):
            value = original(url, **kwargs)
            if url == DISCOVERY["token_endpoint"]:
                value.pop("refresh_token", None)
            return value
        self.transport.json = without_refresh
        profile = self.connect()
        self.assertFalse(self.manager.cached["accounts"][0]["plan_enabled"])
        self.assertNotIn("refresh_token", self.keyring.value["accounts"][profile])
        self.assertEqual(self.manager.sign_out(profile), "Signed out of ChatGPT.")

    def test_issued_client_ids_are_opaque_and_not_limited_to_an_example_prefix(self):
        _, query = self.start()
        self.callback(query, client_id="issued-fixture-client:42")
        self.assertEqual(self.manager.pending()["status"], "complete")

    def test_cancel_during_exchange_prevents_credential_commit(self):
        self.transport.code_entered, self.transport.code_release = threading.Event(), threading.Event()
        attempt, query = self.start()
        callback = threading.Thread(target=self.callback, args=(query,))
        callback.start()
        self.assertTrue(self.transport.code_entered.wait(2))
        self.manager.cancel(attempt["id"])
        self.transport.code_release.set()
        callback.join(4)
        self.assertFalse(callback.is_alive())
        self.assertFalse(self.keyring.value["accounts"])

    def test_refresh_rotates_atomically_and_is_serialized_across_managers(self):
        profile = self.connect()
        self.keyring.value["accounts"][profile]["expires_at"] = 0
        other = chatgpt.Manager(keyring=self.keyring, transport=self.transport,
                                lock_path=Path(self.scratch.name) / "session")
        results = []
        workers = [threading.Thread(target=lambda manager=manager: results.append(manager.access(profile)))
                   for manager in (self.manager, other)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["test-access-rotated"] * 2)
        self.assertEqual(self.transport.refresh_count, 1)
        self.assertEqual(self.keyring.value["accounts"][profile]["refresh_token"], "test-refresh-rotated")

    def test_temporary_refresh_failure_preserves_tokens_but_revocation_clears_them(self):
        profile = self.connect()
        self.keyring.value["accounts"][profile]["expires_at"] = 0
        original = copy.deepcopy(self.keyring.value)
        self.transport.refresh_error = chatgpt.NetworkError(503)
        with self.assertRaises(chatgpt.NetworkError):
            self.manager.access(profile)
        self.assertEqual(self.keyring.value, original)
        self.transport.refresh_error = chatgpt.NetworkError(400, "refresh_token_reused")
        with self.assertRaisesRegex(chatgpt.ChatGPTError, "Sign in again"):
            self.manager.access(profile)
        self.assertNotIn("access_token", self.keyring.value["accounts"][profile])
        self.assertFalse(self.manager.cached["accounts"][0]["signed_in"])

    def test_signout_retains_registration_and_host_for_reauthorization(self):
        profile = self.connect()
        host = self.keyring.value["host_id"]
        self.assertEqual(self.manager.sign_out(profile), "Signed out of ChatGPT.")
        self.assertTrue(self.transport.revoked)
        self.assertFalse(self.manager.cached["accounts"][0]["signed_in"])
        _, query = self.start(profile)
        self.assertEqual(query["client_id"], ["oaiapp_test"])
        self.assertEqual(query["ext_agent_host_id"], [host])
        self.assertNotIn("agent_name_hint", query)
        self.assertNotIn("id_token_hint", query)

    def test_rejected_session_is_not_reused_but_can_still_be_revoked(self):
        profile = self.connect()
        self.manager.require_signin(profile, "test-access")
        self.assertFalse(self.manager.cached["accounts"][0]["signed_in"])
        with self.assertRaisesRegex(chatgpt.ChatGPTError, "Sign in"):
            self.manager.access(profile)
        self.assertIn("refresh_token", self.keyring.value["accounts"][profile])
        self.manager.sign_out(profile)
        self.assertTrue(self.transport.revoked)

    def test_signout_network_failure_is_explicit_and_does_not_leave_local_tokens(self):
        profile = self.connect()
        with patch.object(self.transport, "open", side_effect=chatgpt.NetworkError(503)):
            message = self.manager.sign_out(profile)
        self.assertIn("not confirmed", message)
        self.assertNotIn("refresh_token", self.keyring.value["accounts"][profile])

    def test_models_and_credentials_stay_bound_to_each_registration(self):
        first = self.connect()
        self.manager.status(True)
        self.transport.subject = "another-subject"
        # Same email, different workspace registration: labels must differ.
        _, query = self.start()
        self.callback(query, client_id="oaiapp_second")
        second = self.manager.cached["active"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.manager.cached["models"], [])
        self.assertNotEqual(*[a["label"] for a in self.manager.cached["accounts"]])
        with self.assertRaises(chatgpt.ChatGPTError):
            self.manager.access(first)
        self.manager.select(first)
        self.assertEqual(self.manager.cached["model"], "test-model")


class NetworkBoundaries(unittest.TestCase):
    def test_tokens_are_never_forwarded_to_other_hosts_or_http(self):
        transport = chatgpt.Transport()
        for url in ("http://auth.openai.com/token", "https://example.invalid/token", "https://auth.openai.com@evil.invalid/token",
                    "https://auth.openai.com:444/token"):
            with self.assertRaises(chatgpt.ChatGPTError), patch.object(transport.opener, "open") as opened:
                transport.open(url, token="fixture-secret")
                opened.assert_not_called()

    def test_redirect_and_remote_error_text_are_not_exposed(self):
        transport = chatgpt.Transport()
        error = urllib.error.HTTPError(chatgpt.API, 302, "fixture-secret", {}, io.BytesIO(b'fixture-secret'))
        with patch.object(transport.opener, "open", side_effect=error):
            with self.assertRaises(chatgpt.NetworkError) as result:
                transport.open(chatgpt.API, token="fixture-secret")
        self.assertNotIn("fixture-secret", str(result.exception))
        self.assertIsNone(chatgpt._NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_discovery_cannot_redirect_authentication_to_another_origin(self):
        transport = chatgpt.Transport()
        for key in ("issuer", "token_endpoint", "jwks_uri", "revocation_endpoint", "authorization_endpoint"):
            discovery = dict(DISCOVERY)
            discovery[key] = "https://example.invalid/steal"
            with patch.object(transport, "json", return_value=discovery):
                with self.assertRaises(chatgpt.ChatGPTError):
                    transport.discovery()


if __name__ == "__main__":
    unittest.main()
