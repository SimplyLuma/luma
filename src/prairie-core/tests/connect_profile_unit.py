#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The account profile commands against a stub hub and a disposable XDG home.

No network: the hub is an object that records what it was asked and answers
the way the Luma Hub does, including its refusals. The device token must never
appear in anything printed, and nothing may ever call a typed address or
number verified.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError

with tempfile.TemporaryDirectory(prefix="luma-connect-profile-test-") as directory:
    root = Path(directory)
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["HOME"] = str(root / "home")
    os.environ["PRAIRIE_EDS_MODE"] = "disabled"

    from prairie_apps import connect_sync
    from prairie_apps.connect_sync import (
        AuthorisationError, ConnectError, DeviceIdentity, HubResponseError, _error_detail,
        format_phone, get_profile, main, profile_changes, profile_lines, save_identity, set_profile,
    )

    TOKEN = "device-token-that-must-never-be-printed-000000"
    GITHUB = {"provider": "github", "name": "GitHub", "manage_url": "https://github.com/settings/security"}

    def hub_profile(**overrides):
        profile = {"name": "nmcmil", "name_source": "sign_in", "sign_in_name": "nmcmil",
                   "email": "nick@example.com", "email_source": "sign_in", "email_verified": True,
                   "email_verified_by": "GitHub", "sign_in_email": "nick@example.com",
                   "phone": None, "phone_verified": False, "discoverable_by_phone": False, "phone_lookup": "off",
                   "sign_in": GITHUB, "verification": {"email": False, "phone": False, "phone_lookup": False},
                   "updated_at": None}
        profile.update(overrides)
        return profile

    class StubHub:
        """Answers like /api/hub/sync/profile: validates, normalizes, never verifies."""

        def __init__(self):
            self.calls = []
            self.profile = hub_profile()
            self.refuse = None

        def get_json(self, url, *, token="", timeout=None):
            self.calls.append(("GET", url, None, token))
            if self.refuse:
                raise self.refuse
            if url.endswith("/api/hub/sync/account"):
                return {"account": {"name": self.profile["name"]}, "profile": self.profile,
                        "overview": {"devices": []}}
            return {"profile": self.profile}

        def patch_json(self, url, payload, *, token=""):
            self.calls.append(("PATCH", url, payload, token))
            if self.refuse:
                raise self.refuse
            if "phone" in payload:
                if payload["phone"] is not None and not payload["phone"].startswith("+"):
                    raise HubResponseError(400, "Bad Request", "Enter a phone number with its country code, like +1 555 010 0199.")
                self.profile.update(phone=payload["phone"], phone_verified=False)
            if "email" in payload:
                chosen = payload["email"]
                self.profile.update(email=chosen or self.profile["sign_in_email"],
                                    email_source="profile" if chosen else "sign_in",
                                    email_verified=not chosen, email_verified_by=None if chosen else "GitHub")
            if "name" in payload:
                self.profile.update(name=payload["name"] or "nmcmil",
                                    name_source="profile" if payload["name"] else "sign_in")
            if "discoverable_by_phone" in payload:
                on = bool(payload["discoverable_by_phone"] and self.profile["phone"])
                self.profile.update(discoverable_by_phone=on, phone_lookup="waiting_for_verification" if on else "off")
            return {"profile": self.profile}

    # --- phone display --------------------------------------------------------
    assert format_phone("+14155550199") == "+1 415-555-0199"
    assert format_phone("+442079460958") == "+442079460958", "other countries are shown as stored"
    assert format_phone(None) == "" and format_phone("") == ""
    assert format_phone("+1415555019") == "+1415555019", "a malformed number is not dressed up"

    # --- terminal wording is honest about verification ------------------------
    lines = profile_lines(hub_profile())
    assert lines[0] == "Name: nmcmil (from GitHub)", lines
    assert "Email: nick@example.com (verified by GitHub)" in lines, lines
    assert "Phone number: (none)" in lines and "Findable by phone number: off" in lines, lines
    assert lines[-1] == "Sign-in: GitHub; change your password at https://github.com/settings/security", lines
    typed = profile_lines(hub_profile(name="Nick", name_source="profile", email="n@luma.example", email_source="profile",
                                      email_verified=False, email_verified_by=None, phone="+14155550199",
                                      discoverable_by_phone=True, phone_lookup="waiting_for_verification"))
    assert typed[0] == "Name: Nick", typed
    assert "Email: n@luma.example (not verified)" in typed, typed
    assert "Phone number: +1 415-555-0199 (not verified)" in typed, typed
    assert "Findable by phone number: on, once the number is verified" in typed, typed
    assert not any("verified by" in line for line in typed[1:3]), "a typed address must never read as verified"
    local = profile_lines(hub_profile(sign_in={"provider": "luma-connect", "name": "Luma Connect", "manage_url": None}))
    assert local[-1] == "Sign-in: Luma Connect", local

    # --- which fields a `profile set` asks for ---------------------------------
    assert profile_changes(name="Nick") == {"name": "Nick"}
    assert profile_changes(reset_name=True, remove_phone=True, reset_email=True) == {"name": None, "phone": None, "email": None}
    assert profile_changes(phone="+1 415 555 0199", discoverable="on") == {"phone": "+1 415 555 0199", "discoverable_by_phone": True}
    assert profile_changes(discoverable="off") == {"discoverable_by_phone": False}
    assert profile_changes(stdin_text='{"email": "n@luma.example", "phone": null}') == {"email": "n@luma.example", "phone": None}
    for arguments, words in (
        ({}, "Nothing to change"),
        ({"name": "A", "reset_name": True}, "not both"),
        ({"stdin_text": "not json"}, "JSON object"),
        ({"stdin_text": "[1, 2]"}, "JSON object"),
        ({"stdin_text": '{"phone_verified": true}'}, "Unknown profile field"),
        ({"stdin_text": '{"name": 42}'}, "must be text"),
        ({"stdin_text": '{"discoverable_by_phone": "yes"}'}, "true or false"),
    ):
        try:
            profile_changes(**arguments)
        except ConnectError as error:
            assert words in str(error), (arguments, error)
        else:
            raise AssertionError(f"accepted {arguments}")

    # --- not signed in -----------------------------------------------------------
    try:
        get_profile(http=StubHub())
    except ConnectError as error:
        assert "not signed in" in str(error)
    else:
        raise AssertionError("a profile was read without a registration")

    save_identity(DeviceIdentity("19982c3a-3226-4ed7-a2ae-e551667efed6", TOKEN, "https://hub.example", "ThinkPad", "2026-09-14T00:00:00+00:00"))

    # --- read and change through the device route --------------------------------
    hub = StubHub()
    assert get_profile(http=hub)["name"] == "nmcmil"
    assert hub.calls[-1] == ("GET", "https://hub.example/api/hub/sync/profile", None, TOKEN)
    changed = set_profile({"name": "Nick", "phone": "+14155550199"}, http=hub)
    assert changed["name"] == "Nick" and changed["phone"] == "+14155550199" and changed["phone_verified"] is False
    assert hub.calls[-1][:3] == ("PATCH", "https://hub.example/api/hub/sync/profile", {"name": "Nick", "phone": "+14155550199"})
    try:
        set_profile({"verified": True}, http=hub)
    except ConnectError as error:
        assert "Unknown profile field" in str(error)
    else:
        raise AssertionError("an unknown field was sent")
    assert hub.calls[-1][2] == {"name": "Nick", "phone": "+14155550199"}, "nothing may be sent for a refused field"

    # The hub's own words for a refusal reach the person; its status does not bury them.
    try:
        set_profile({"phone": "555-0199"}, http=hub)
    except ConnectError as error:
        assert str(error) == "Enter a phone number with its country code, like +1 555 010 0199.", error
        assert not isinstance(error, AuthorisationError)
    else:
        raise AssertionError("a refused number was reported as saved")
    hub.refuse = HubResponseError(401, "Unauthorized")
    try:
        get_profile(http=hub)
    except AuthorisationError as error:
        assert "enrol" in str(error) and TOKEN not in str(error)
    else:
        raise AssertionError("a revoked token did not ask for re-enrolment")
    hub.refuse = HubResponseError(404, "Not Found")
    try:
        get_profile(http=hub)
    except ConnectError as error:
        assert "does not keep account profiles" in str(error)
    else:
        raise AssertionError("an older hub was not explained")
    hub.refuse = HubResponseError(429, "Too Many Requests")
    try:
        set_profile({"name": "Again"}, http=hub)
    except ConnectError as error:
        assert "could not change your profile" in str(error) and "429" in str(error)
    hub.refuse = None

    # --- the hub's error body is read, bounded, and tolerated when absent ----------
    def http_error(body):
        return HTTPError("https://hub.example/api/hub/sync/profile", 400, "Bad Request", {}, io.BytesIO(body))
    assert _error_detail(http_error(b'{"error":"Your name must be 1 to 60 characters."}')) == "Your name must be 1 to 60 characters."
    assert _error_detail(http_error(b"<html>proxy error</html>")) == ""
    assert _error_detail(http_error(b'{"error": 42}')) == ""
    assert len(_error_detail(http_error(json.dumps({"error": "x " * 4000}).encode()))) <= 300

    # --- the status report carries the profile -------------------------------------
    report = connect_sync.status(http=hub)
    assert report["profile"]["name"] == "Nick" and report["account"]["name"] == "Nick", report

    # --- the command line: show, set from flags, set from standard input -----------
    real_client = connect_sync.HubClient
    connect_sync.HubClient = lambda *a, **k: hub
    try:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert main(["profile"]) == 0
        assert "Name: Nick" in output.getvalue() and "Phone number: +1 415-555-0199 (not verified)" in output.getvalue()
        assert TOKEN not in output.getvalue(), "the device token must never be printed"

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert main(["profile", "set", "--discoverable", "on", "--json"]) == 0
        assert json.loads(output.getvalue())["phone_lookup"] == "waiting_for_verification"

        # The Connect app sends personal details this way so they never sit in argv.
        output, real_stdin = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO('{"email": "n@luma.example"}')
        try:
            with contextlib.redirect_stdout(output):
                assert main(["profile", "set", "--stdin", "--json"]) == 0
        finally:
            sys.stdin = real_stdin
        saved = json.loads(output.getvalue())
        assert saved["email"] == "n@luma.example" and saved["email_verified"] is False
        assert hub.calls[-1][2] == {"email": "n@luma.example"}

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            assert main(["profile", "set", "--phone", "555-0199"]) == connect_sync.EXIT_RETRY
        assert errors.getvalue().strip() == "luma-connect-sync: Enter a phone number with its country code, like +1 555 010 0199.", errors.getvalue()

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            assert main(["profile", "set"]) == connect_sync.EXIT_RETRY
        assert "Nothing to change" in errors.getvalue()

        hub.refuse = HubResponseError(401, "Unauthorized")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            assert main(["profile"]) == connect_sync.EXIT_REENROL
        hub.refuse = None
    finally:
        connect_sync.HubClient = real_client

print("Connect profile: honest verification wording, E.164 display, field selection, stdin changes, "
      "hub refusals shown in the hub's words, 401 re-enrol, and no printed token PASS")
