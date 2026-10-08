# SPDX-License-Identifier: Apache-2.0
"""Luma Messages in Messages (ADR-051): what the window needs beyond any other account.

Every device signed in to Luma Connect has a Luma account in Messages, added
here and never offered in Add Account; its conversations sit in the same
inbox as texts and other networks. The helper (``/usr/libexec/luma-messages/
luma``, package luma-messages-e2ee) does the encryption and all networking;
this module only decides when the account exists, reads what the helper
reports about a conversation, and understands the links people share.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .messages_accounts import Account, Accounts, helper_directory

log = logging.getLogger(__name__)

NETWORK = "luma"
# A username as the Hub accepts one: 3-30 of a-z, 0-9, dot and underscore.
HANDLE = re.compile(r"[a-z0-9](?:[a-z0-9]|[._](?=[a-z0-9])){2,29}")
PROFILE_HOSTS = ("simplyluma.com", "www.simplyluma.com")
APP_SCHEME = "luma-messages"
REPORT_REASONS = (
    ("spam", "Spam"),
    ("harassment", "Harassment or bullying"),
    ("impersonation", "Pretending to be someone else"),
    ("scam", "A scam or fraud"),
    ("other", "Something else"),
)


def connect_device_file(environment: dict[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    data = environment.get("XDG_DATA_HOME") or str(Path(environment.get("HOME", str(Path.home()))) / ".local/share")
    return Path(data) / "luma/connect/device.json"


def helper_installed(directory: Path | None = None) -> bool:
    return os.access((directory or helper_directory()) / NETWORK, os.X_OK)


def setup_problem(*, helper_dir: Path | None = None,
                  environment: dict[str, str] | None = None) -> str:
    """Explain missing local prerequisites without reading any credential."""
    if not helper_installed(helper_dir):
        return "Luma Messages support is missing from this installation. Update Luma, then reopen Messages."
    if not connect_device_file(environment).is_file():
        return "Open Luma Connect and connect this computer to Luma Cloud to use a Luma username."
    return "Luma Messages couldn't load this computer's account. Try again or reopen Messages."


def ensure_account(accounts: Accounts, *, helper_dir: Path | None = None,
                   environment: dict[str, str] | None = None) -> Account | None:
    """The Luma account, made when this device is signed in to Luma Connect.

    Idempotent: one account per device, whatever else exists. Nothing is made
    without the helper installed or without a Luma Connect sign-in, so a device
    that has neither shows nothing new.
    """
    existing = next((account for account in accounts.list() if account.network == NETWORK), None)
    if existing is not None:
        return existing
    if not helper_installed(helper_dir) or not connect_device_file(environment).is_file():
        return None
    account = accounts.create(NETWORK)
    account.name = "Luma"
    account.pending = False
    accounts.save(account)
    log.info("Added Luma Messages for this device's Luma Connect sign-in")
    return account


def is_luma(service) -> bool:
    account = getattr(service, "account", None)
    return account is not None and account.network == NETWORK


def handle_from(text: str) -> str | None:
    """The username in what a person typed or pasted: ``@name``, ``name``, a
    profile link (``https://simplyluma.com/@name``) or an app link
    (``luma-messages://u/name``). Anything else is None."""
    value = (text or "").strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme == APP_SCHEME and parsed.netloc == "u":
            value = parsed.path.strip("/")
        elif parsed.scheme == "https" and (parsed.hostname or "").lower() in PROFILE_HOSTS and parsed.path.startswith("/@"):
            value = parsed.path[2:].strip("/")
        else:
            return None
    value = value.removeprefix("@").lower()
    return value if HANDLE.fullmatch(value) and not re.fullmatch(r"[0-9._]+", value) else None


def public_person_name(person: dict | None, handle: str) -> str:
    """A public username result never promotes an OAuth email to a display name.

    Local Contacts keep their own name/phone/email fallback. Public discovery
    follows ADR-051 and falls back to the actual handle, not a private address.
    """
    value = (person or {}).get("display_name")
    name = " ".join(value.split()) if isinstance(value, str) else ""
    if name and not re.search(r"[^\s<>]+@[^\s<>]+\.[^\s<>]+", name):
        return name[:120]
    return f"@{handle}"


def looks_like_handle(text: str) -> bool:
    """Only what can't be a name or a number: an explicit @ or a link."""
    value = (text or "").strip()
    return (value.startswith("@") or "://" in value) and handle_from(value) is not None


def safety_groups(digits: str) -> list[str]:
    """Sixty digits as twelve groups of five, however the helper spaced them."""
    plain = re.sub(r"\D", "", digits or "")
    return [plain[i:i + 5] for i in range(0, len(plain), 5)] if len(plain) == 60 else []


def conversation_flags(service, address: str) -> dict:
    store = getattr(service, "store", None)
    reader = getattr(store, "luma_flags", None)
    return reader(address) if callable(reader) else {}
