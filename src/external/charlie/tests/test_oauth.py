# SPDX-License-Identifier: Apache-2.0
import json
import time
import unittest
from base64 import urlsafe_b64encode
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from charlie_luma.oauth import GmailOAuth, MicrosoftOAuth, OAuthError, GoogleOAuthUnavailable, failure_requires_sign_in, pkce_challenge


class GmailOAuthTests(unittest.TestCase):
    def test_pkce_matches_rfc_7636_vector(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(pkce_challenge(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_google_oauth_refuses_before_browser_socket_or_network(self):
        oauth = GmailOAuth("client-id", "client-secret")
        self.assertEqual(oauth.profile_picture_for_access_token(""), "")
        with patch("charlie_luma.oauth.HTTPServer") as server, \
             patch("charlie_luma.oauth._post_form") as post, \
             patch("charlie_luma.oauth._userinfo") as profile:
            with self.assertRaises(GoogleOAuthUnavailable):
                oauth.authorization_url("http://localhost:4321", "verifier", "state")
            with self.assertRaises(GoogleOAuthUnavailable):
                oauth.sign_in(lambda _uri: self.fail("Google browser opened"))
            with self.assertRaises(GoogleOAuthUnavailable):
                oauth.profile_picture_for_access_token("retained-access")
            server.assert_not_called(); post.assert_not_called(); profile.assert_not_called()

    def test_saved_google_grants_are_paused_without_refresh_or_revocation(self):
        token = json.dumps({"access_token": "retained", "refresh_token": "retained-refresh",
                            "expires_at": int(time.time()) + 600})
        with patch("charlie_luma.oauth._post_form") as post:
            for saved in (token, "damaged-but-retained"):
                for forced in (False, True):
                    with self.assertRaises(GoogleOAuthUnavailable) as caught:
                        GmailOAuth().access_token(saved, force_refresh=forced)
                    self.assertFalse(failure_requires_sign_in(caught.exception))
            post.assert_not_called()

    def test_saved_identity_can_restore_its_profile_picture_without_network(self):
        payload = urlsafe_b64encode(json.dumps({
            "picture": "https://lh3.googleusercontent.com/a/profile"
        }).encode()).decode().rstrip("=")
        token = json.dumps({"id_token": f"header.{payload}.signature"})
        self.assertEqual(
            GmailOAuth.profile_picture(token),
            "https://lh3.googleusercontent.com/a/profile",
        )



class MicrosoftOAuthTests(unittest.TestCase):
    def test_authorization_url_uses_public_client_pkce_and_mail_scopes(self):
        url = MicrosoftOAuth("charlie-client-id").authorization_url(
            "http://localhost:4321", "verifier", "state-value"
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["client_id"], ["charlie-client-id"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["prompt"], ["select_account"])
        self.assertIn("offline_access", query["scope"][0])
        self.assertIn("IMAP.AccessAsUser.All", query["scope"][0])
        self.assertIn("SMTP.Send", query["scope"][0])

    def test_saved_public_client_id_survives_token_refresh(self):
        token = json.dumps({
            "client_id": "charlie-client-id",
            "access_token": "stale",
            "refresh_token": "refresh",
            "expires_at": int(time.time()) + 600,
            "id_token": "identity",
        })
        oauth = MicrosoftOAuth.from_token_json(token)
        with patch("charlie_luma.oauth._post_form", return_value={
            "access_token": "fresh", "expires_in": 3600
        }) as post:
            access, refreshed = oauth.access_token(token, force_refresh=True)
        self.assertEqual(access, "fresh")
        saved = json.loads(refreshed)
        self.assertEqual(saved["client_id"], "charlie-client-id")
        self.assertEqual(saved["refresh_token"], "refresh")
        self.assertEqual(saved["id_token"], "identity")
        post.assert_called_once()

    def test_saved_microsoft_sign_in_requires_its_public_client_id(self):
        with self.assertRaises(OAuthError):
            MicrosoftOAuth.from_token_json(json.dumps({"access_token": "token"}))
