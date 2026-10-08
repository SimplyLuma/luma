# SPDX-License-Identifier: Apache-2.0
"""Native Charlie protocol/keyring owner, independent of the D-Bus adapter."""
from dataclasses import asdict
from pathlib import Path
import tempfile
from .account_lifecycle import account_lock, account_locks
from .accounts import account_id_for_address, provider_by_key, preset_config
from .engine import ImapSmtpTransport, AuthenticationFailure
from .mail_agent import default_store_path
from .model import Account
from .oauth import GmailOAuth, MicrosoftOAuth, failure_requires_sign_in, OAuthError, require_google_oauth, GOOGLE_UNAVAILABLE
from .secrets import SecretStore
from .store import MailStore

class MailOwner:
    def __init__(self):
        self.store = MailStore(default_store_path())
        self.secrets = SecretStore()
        self.google = GmailOAuth()
        self.transport = ImapSmtpTransport(self.secrets.lookup, self._token)

    def _lock(self, identifier):
        return account_lock(self.store.path, identifier)

    def _token(self, account, force_refresh=False):
        with self._lock(account.id):
            current, _config = self._account(account.id)
            if current.provider != account.provider:
                raise ValueError("Mail account changed.")
            return self._token_locked(current, force_refresh)

    def _token_locked(self, account, force_refresh=False):
        saved = self.secrets.lookup(account.id, "oauth-token")
        if not saved: return None
        if account.provider == "gmail":
            raise OSError(GOOGLE_UNAVAILABLE)
        oauth = MicrosoftOAuth.from_token_json(saved) if account.provider == "microsoft" else self.google
        try: token, refreshed = oauth.access_token(saved, force_refresh=force_refresh)
        except OAuthError as error:
            if failure_requires_sign_in(error): raise AuthenticationFailure("Sign in again.") from None
            raise OSError("Token service unavailable.") from None
        if refreshed: self.secrets.store(account.id, "oauth-token", refreshed)
        return token

    def _account(self, identifier):
        account = next((a for a in self.store.accounts() if a.id == identifier), None)
        config = self.store.server_config(identifier)
        if account is None or config is None or not account.enabled: raise ValueError("Mail account unavailable.")
        return account, config

    def perform(self, operation, data, cancelled):
        if operation == "SaveAccount":
            identifiers = [data["account"].id]
        elif operation == "Send":
            identifiers = [data["draft"].account_id]
        elif operation == "MarkRead":
            identifiers = [m.account_id for m in self.store.messages_by_ids(data["message_ids"])]
        else:
            identifiers = [data["account_id"]] if "account_id" in data else []
        with account_locks(self.store.path, identifiers, cancelled=cancelled):
            return self._perform(operation, data, cancelled)

    def _perform(self, operation, data, cancelled):
        def live():
            if cancelled.is_set(): raise RuntimeError("Mail operation cancelled.")
        live()
        if operation == "SaveAccount":
            account, config, password = data["account"], data["config"], data["password"]
            if password:
                self.secrets.store(account.id, "password", password)
                if self.secrets.lookup(account.id, "password") != password: raise AuthenticationFailure("Credential storage unavailable.")
                self.secrets.clear(account.id, "oauth-token")
            elif not (self.secrets.lookup(account.id, "password") or self.secrets.lookup(account.id, "oauth-token")):
                raise AuthenticationFailure("Account credentials unavailable.")
            # Once credential commit starts, finish the matching metadata under
            # the account lock; cancellation cannot leave the old account stranded.
            self.store.upsert_account(account); self.store.upsert_server_config(account.id, config)
            return {"account": asdict(account)}
        if operation == "RemoveAccount":
            if not any(a.id == data["account_id"] for a in self.store.accounts()): raise ValueError("Mail account unavailable.")
            live()
            self.secrets.clear(data["account_id"], "password"); self.secrets.clear(data["account_id"], "oauth-token")
            self.store.delete_account(data["account_id"]); return {}
        if operation == "GoogleSignIn":
            require_google_oauth()
        if operation == "MicrosoftSignIn":
            oauth = MicrosoftOAuth(data["client_id"])
            def open_uri(uri):
                from gi.repository import Gio
                live(); return Gio.AppInfo.launch_default_for_uri(uri, None)
            identity = oauth.sign_in(open_uri, cancel=cancelled); live()
            provider = "microsoft"
            account = Account(account_id_for_address(identity.email), identity.name or identity.email.split("@", 1)[0], identity.email, provider, "red" if provider == "gmail" else "blue", avatar_url=identity.picture)
            with self._lock(account.id):
                self.secrets.store(account.id, "oauth-token", identity.token_json)
                if self.secrets.lookup(account.id, "oauth-token") != identity.token_json: raise AuthenticationFailure("Credential storage unavailable.")
                self.secrets.clear(account.id, "password")
                self.store.upsert_account(account); self.store.upsert_server_config(account.id, preset_config(provider_by_key(provider), identity.email))
                return {"account": asdict(account)}
        if operation == "MarkRead":
            messages = self.store.messages_by_ids(data["message_ids"]); live(); self.store.set_read(data["message_ids"], data["read"])
            for account_id in dict.fromkeys(m.account_id for m in messages):
                account, config = self._account(account_id); live()
                self.transport.set_seen(account, config, tuple(m for m in messages if m.account_id == account_id), data["read"])
            return {}
        if operation == "Send":
            message = data["draft"]; account, config = self._account(message.account_id)
            with tempfile.TemporaryDirectory(prefix="charlie-send-") as temporary:
                for index, (filename, content) in enumerate(data["attachments"]):
                    directory = Path(temporary) / str(index); directory.mkdir(mode=0o700)
                    target = directory / filename; target.write_bytes(content); target.chmod(0o600); message.attachments.append(str(target))
                live(); self.transport.send(account, config, message)
            return {}
        account, config = self._account(data["account_id"])
        if operation == "Sync":
            messages = self.transport.fetch_mail(account, config, cancel=cancelled)
            for message in messages: live(); self.store.upsert_message(message)
            return {}
        if operation == "HydrateAvatar":
            if account.provider == "gmail" and not account.avatar_url:
                from dataclasses import replace
                picture = self.google.profile_picture_for_access_token(self._token(account) or "")
                live()
                if picture: self.store.upsert_account(replace(account, avatar_url=picture))
            return {}
        raise ValueError("Unsupported mail operation.")

    def close(self): self.store.close()

