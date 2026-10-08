# SPDX-License-Identifier: MPL-2.0
"""AirPlay passwords in the person's keyring (Secret Service via libsecret).

Passwords are never written to the state file. Without libsecret the service
still works; the person is asked for the password again next session.
"""

from __future__ import annotations

import logging
from typing import Callable

import gi

__all__ = ("PasswordStore",)

log = logging.getLogger("luma-audio-devices")

SCHEMA_NAME = "org.projectluma.AudioDevices.AirPlay"


class PasswordStore:
    def __init__(self) -> None:
        try:
            gi.require_version("Secret", "1")
            from gi.repository import Secret
        except (ImportError, ValueError):
            self._secret = None
            self._schema = None
            log.info("libsecret is unavailable; AirPlay passwords are kept for this session only")
        else:
            self._secret = Secret
            self._schema = Secret.Schema.new(SCHEMA_NAME, Secret.SchemaFlags.NONE,
                                             {"receiver": Secret.SchemaAttributeType.STRING})
        self._session: dict[str, str] = {}

    def lookup(self, receiver_id: str, callback: Callable[[str | None], None]) -> None:
        if receiver_id in self._session:
            callback(self._session[receiver_id])
            return
        if self._secret is None:
            callback(None)
            return

        def done(_source, result) -> None:
            try:
                callback(self._secret.password_lookup_finish(result))
            except Exception as error:  # GLib.Error, or no keyring at all
                log.info("keyring lookup failed: %s", error)
                callback(None)

        self._secret.password_lookup(self._schema, {"receiver": receiver_id}, None, done)

    def store(self, receiver_id: str, label: str, password: str) -> None:
        self._session[receiver_id] = password
        if self._secret is None:
            return

        def done(_source, result) -> None:
            try:
                self._secret.password_store_finish(result)
            except Exception as error:
                log.info("keyring store failed: %s", error)

        self._secret.password_store(self._schema, {"receiver": receiver_id},
                                    self._secret.COLLECTION_DEFAULT,
                                    f"AirPlay password for {label}", password, None, done)

    def clear(self, receiver_id: str) -> None:
        self._session.pop(receiver_id, None)
        if self._secret is None:
            return

        def done(_source, result) -> None:
            try:
                self._secret.password_clear_finish(result)
            except Exception as error:
                log.info("keyring clear failed: %s", error)

        self._secret.password_clear(self._schema, {"receiver": receiver_id}, None, done)
