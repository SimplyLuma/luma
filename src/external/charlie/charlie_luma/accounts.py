# SPDX-License-Identifier: Apache-2.0
"""Provider-neutral account enrollment helpers.

Only non-secret server metadata is returned here. Passwords and app passwords
are handed directly to Secret Service by the application controller.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .model import ServerConfig


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    key: str
    label: str
    subtitle: str
    domains: tuple[str, ...]
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    use_starttls: bool = False
    auth_mode: str = "app-password"
    password_hint: str = "Use the app password created by your mail provider."


PROVIDERS = (
    ProviderPreset(
        "gmail", "Google", "Gmail and Google Workspace",
        ("gmail.com", "googlemail.com"), "imap.gmail.com", 993,
        "smtp.gmail.com", 465, auth_mode="google-oauth",
        password_hint="Google sign-in is unavailable in this release. Use an app password for manual setup.",
    ),
    ProviderPreset(
        "microsoft", "Microsoft 365 / Outlook", "Work, school, Outlook.com and Hotmail",
        ("outlook.com", "hotmail.com", "live.com", "msn.com"),
        "outlook.office365.com", 993, "smtp.office365.com", 587,
        use_starttls=True, auth_mode="microsoft-oauth",
        password_hint="Sign in securely with Microsoft in your browser.",
    ),
    ProviderPreset(
        "icloud", "iCloud Mail", "Apple ID with an app-specific password",
        ("icloud.com", "me.com", "mac.com"),
        "imap.mail.me.com", 993, "smtp.mail.me.com", 587,
        use_starttls=True,
        password_hint="Create an app-specific password at account.apple.com.",
    ),
    ProviderPreset(
        "yahoo", "Yahoo Mail", "Yahoo or Ymail with an app password",
        ("yahoo.com", "ymail.com"),
        "imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465,
        password_hint="Create an app password in Yahoo Account Security.",
    ),
    ProviderPreset(
        "fastmail", "Fastmail", "Personal and custom-domain Fastmail accounts",
        ("fastmail.com", "fastmail.fm"),
        "imap.fastmail.com", 993, "smtp.fastmail.com", 465,
        password_hint="Create an app password in Fastmail Settings.",
    ),
    ProviderPreset(
        "zoho", "Zoho Mail", "Zoho-hosted personal and business mail",
        ("zoho.com", "zohomail.com"),
        "imap.zoho.com", 993, "smtp.zoho.com", 465,
        password_hint="Use an app-specific password when two-factor authentication is on.",
    ),
    ProviderPreset(
        "aol", "AOL Mail", "AOL accounts with an app password",
        ("aol.com",), "imap.aol.com", 993, "smtp.aol.com", 465,
        password_hint="Create an app password in AOL Account Security.",
    ),
    ProviderPreset(
        "custom", "Other IMAP", "Any standards-based IMAP and SMTP provider",
        (), "", 993, "", 465, auth_mode="password",
        password_hint="Use the password required by your mail provider.",
    ),
)


def account_id_for_address(address: str) -> str:
    normalized = address.strip().casefold()
    return f"mail-{sha256(normalized.encode('utf-8')).hexdigest()[:20]}"


def provider_for_address(address: str) -> ProviderPreset:
    domain = address.strip().casefold().rsplit("@", 1)[-1]
    return next(
        (preset for preset in PROVIDERS if domain in preset.domains),
        PROVIDERS[-1],
    )


def provider_by_key(key: str) -> ProviderPreset:
    return next((preset for preset in PROVIDERS if preset.key == key), PROVIDERS[-1])


def preset_config(preset: ProviderPreset, address: str) -> ServerConfig:
    return ServerConfig(
        imap_host=preset.imap_host,
        imap_port=preset.imap_port,
        smtp_host=preset.smtp_host,
        smtp_port=preset.smtp_port,
        username=address.strip(),
        use_starttls=preset.use_starttls,
    )
