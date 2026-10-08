# SPDX-License-Identifier: Apache-2.0
import unittest

from charlie_luma.accounts import (
    PROVIDERS,
    account_id_for_address,
    provider_by_key,
    provider_for_address,
    preset_config,
)


class AccountPresetTests(unittest.TestCase):
    def test_common_providers_have_secure_standard_endpoints(self):
        gmail = provider_for_address("Nick@Gmail.com")
        self.assertEqual(gmail.key, "gmail")
        self.assertEqual(preset_config(gmail, "Nick@Gmail.com").imap_host, "imap.gmail.com")

        microsoft = provider_for_address("nick@outlook.com")
        config = preset_config(microsoft, "nick@outlook.com")
        self.assertEqual(config.smtp_port, 587)
        self.assertTrue(config.use_starttls)
        self.assertEqual(microsoft.auth_mode, "microsoft-oauth")

    def test_provider_picker_covers_mainstream_and_manual_mail(self):
        self.assertEqual(
            [provider.label for provider in PROVIDERS],
            [
                "Google",
                "Microsoft 365 / Outlook",
                "iCloud Mail",
                "Yahoo Mail",
                "Fastmail",
                "Zoho Mail",
                "AOL Mail",
                "Other IMAP",
            ],
        )
        for key in ("icloud", "yahoo", "fastmail", "zoho", "aol"):
            provider = provider_by_key(key)
            config = preset_config(provider, f"nick@{provider.domains[0]}")
            self.assertEqual(config.imap_port, 993)
            self.assertTrue(config.imap_host)
            self.assertTrue(config.smtp_host)

    def test_unknown_domains_use_custom_servers(self):
        custom = provider_for_address("nick@example.org")
        self.assertEqual(custom, provider_by_key("custom"))
        self.assertFalse(custom.imap_host)

    def test_account_identity_is_stable_and_case_insensitive(self):
        self.assertEqual(
            account_id_for_address(" Nick@Example.org "),
            account_id_for_address("nick@example.org"),
        )
