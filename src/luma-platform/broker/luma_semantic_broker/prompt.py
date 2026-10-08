# SPDX-License-Identifier: Apache-2.0
"""Launch the fixed broker-owned consent UI without accepting caller markup."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .model import Identity


PROMPT = "/usr/libexec/luma-semantic-consent"
SYSTEMD_RUN = "/usr/bin/systemd-run"
BIDI_CONTROLS = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def safe_label(value: str) -> str:
    """Return bounded single-line text suitable for a trusted consent prompt."""

    sanitized = "".join(
        " " if character.isspace() else character
        for character in value
        if (character.isspace() or ord(character) >= 32)
        and ord(character) != 127
        and character not in BIDI_CONTROLS
    )
    return " ".join(sanitized.split())[:256] or "Unknown application"


class PromptRunner:
    def __init__(
        self,
        executable: str = PROMPT,
        runner: str = SYSTEMD_RUN,
    ) -> None:
        self.executable = executable
        self.runner = runner

    def _command(self, arguments: list[str]) -> list[str]:
        # The broker is deliberately denied direct GPU/device access. Ask the
        # user's service manager to launch the fixed, root-owned consent UI in
        # its own process boundary instead of weakening the broker sandbox.
        return [
            self.runner,
            "--user",
            "--wait",
            "--collect",
            "--quiet",
            "--property=NoNewPrivileges=yes",
            "--",
            *arguments,
        ]

    def request_access(
        self,
        client: Identity,
        target_label: str,
        scopes: tuple[str, ...],
        persistent: bool,
        callback: Callable[[bool], None],
    ) -> Gio.Subprocess | None:
        arguments = [
            self.executable,
            "access",
            "--client",
            safe_label(client.label),
            "--target",
            safe_label(target_label),
            "--scopes",
            ",".join(scopes),
        ]
        if persistent:
            arguments.append("--persistent")
        return self._run(arguments, callback)

    def confirm_action(
        self,
        client: Identity,
        target_label: str,
        action_label: str,
        risk: str,
        callback: Callable[[bool], None],
    ) -> Gio.Subprocess | None:
        return self._run(
            [
                self.executable,
                "confirm",
                "--client",
                safe_label(client.label),
                "--target",
                safe_label(target_label),
                "--action",
                safe_label(action_label),
                "--risk",
                risk,
            ],
            callback,
        )

    @staticmethod
    def _finish(process: Gio.Subprocess, result, callback: Callable[[bool], None]) -> None:
        try:
            callback(bool(process.wait_check_finish(result)))
        except GLib.Error:
            callback(False)

    def _run(
        self, arguments: list[str], callback: Callable[[bool], None]
    ) -> Gio.Subprocess | None:
        try:
            process = Gio.Subprocess.new(
                self._command(arguments), Gio.SubprocessFlags.NONE
            )
        except GLib.Error:
            callback(False)
            return None
        process.wait_check_async(None, self._finish, callback)
        return process
