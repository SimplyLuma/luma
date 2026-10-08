# SPDX-License-Identifier: Apache-2.0
"""Provider-first Luma account enrollment UI.

Protocol and secret handling remain in the application controller.  This
module owns only the progressive account surface: choose a familiar provider,
authenticate, and reveal raw server fields only when someone asks for them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from luma_appkit import (
    AppWindow,
    Avatar,
    CommandRegistry,
    Island,
    NavigationRow,
    TitleIsland,
)

from . import APP_ID
from .accounts import PROVIDERS, account_id_for_address, provider_by_key, preset_config
from .model import Account, ServerConfig, address_parts
from .oauth import MicrosoftOAuth, OAuthError, GOOGLE_UNAVAILABLE

if TYPE_CHECKING:
    from .application import AccountWindow, CharlieApplication


class AccountEditorWindow(AppWindow):
    """A provider-first setup flow using AppKit islands and navigation rows."""

    def __init__(
        self,
        application: "CharlieApplication",
        manager: "AccountWindow",
        account: Account | None = None,
    ) -> None:
        super().__init__(
            application=application,
            app_id=APP_ID,
            title="Edit Account" if account else "Add Account",
            icon_name=APP_ID,
            commands=CommandRegistry(()),
            geometry_scope="account-editor-luma",
            default_width=640,
            default_height=680,
            minimum_width=360,
            minimum_height=480,
        )
        self.app = application
        self.manager = manager
        self.account = account
        self.current = application.store.server_config(account.id) if account else None
        self.preset = provider_by_key(account.provider) if account else PROVIDERS[-1]
        self.advanced_visible = bool(account) or self.preset.key == "custom"

        island = Island()
        island.add_css_class("charlie-account-form")
        self.title_island = TitleIsland("Edit Account" if account else "Add Account", lead="back",
                                        lead_label="Choose another provider",
                                        on_lead=self._show_providers, phone_only=False)
        self.back = self.title_island.lead_button
        self.heading = self.title_island.title_label
        island.append(self.title_island)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=180,
            vexpand=True,
        )
        self.stack.add_named(self._provider_page(), "providers")
        self.stack.add_named(self._setup_page(), "setup")
        island.append(self.stack)
        self.set_body(island)

        if account:
            self._show_setup(self.preset)
        else:
            self._show_providers()

    @staticmethod
    def _scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        scroll.set_child(child)
        return scroll

    @staticmethod
    def _section(label: str) -> Gtk.Label:
        value = Gtk.Label(label=label.upper(), xalign=0)
        value.add_css_class("luma-section-label")
        value.add_css_class("content")
        return value

    @staticmethod
    def _field(label: str, widget: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("charlie-account-field")
        name = Gtk.Label(label=label, xalign=0, valign=Gtk.Align.CENTER)
        name.add_css_class("charlie-form-label")
        row.append(name)
        widget.set_hexpand(True)
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
        row.append(widget)
        return row

    def _provider_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.add_css_class("charlie-account-page")
        page.set_margin_start(18)
        page.set_margin_end(18)
        page.set_margin_top(16)
        page.set_margin_bottom(18)

        title = Gtk.Label(label="Choose your email provider", xalign=0)
        title.add_css_class("luma-display-title")
        page.append(title)
        description = Gtk.Label(
            label=(
                "Charlie fills in standard secure settings. Raw server details "
                "stay out of the way unless you choose to review them."
            ),
            xalign=0,
            wrap=True,
        )
        description.add_css_class("luma-appbar-sub")
        page.append(description)

        self.provider_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            activate_on_single_click=True,
        )
        self.provider_list.add_css_class("charlie-provider-list")
        for preset in PROVIDERS:
            row = NavigationRow(
                preset.label,
                subtitle=preset.subtitle,
                icon_widget=Avatar(preset.label),
            )
            row.add_css_class("charlie-provider-row")
            row.charlie_provider = preset
            self.provider_list.append(row)
        self.provider_list.connect("row-activated", self._provider_activated)
        page.append(self.provider_list)
        return self._scroll(page)

    def _setup_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.add_css_class("charlie-account-page")
        page.set_margin_start(18)
        page.set_margin_end(18)
        page.set_margin_top(16)
        page.set_margin_bottom(18)

        self.provider_summary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )
        self.provider_summary.add_css_class("charlie-provider-summary")
        page.append(self.provider_summary)

        self.oauth_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.oauth_panel.add_css_class("charlie-account-card")
        self.oauth_copy = Gtk.Label(xalign=0, wrap=True)
        self.oauth_copy.add_css_class("charlie-account-copy")
        self.oauth_panel.append(self.oauth_copy)
        self.microsoft_client_id = Gtk.Entry(
            placeholder_text="Microsoft Application (client) ID",
            hexpand=True,
        )
        self.microsoft_client_id.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Microsoft Application client ID"]
        )
        self.oauth_panel.append(self.microsoft_client_id)
        self.oauth_error = Gtk.Label(label="", xalign=0, wrap=True)
        self.oauth_error.add_css_class("error")
        self.oauth_error.set_visible(False)
        self.oauth_panel.append(self.oauth_error)
        self.oauth_button = Gtk.Button(label="Continue in Browser", halign=Gtk.Align.END)
        self.oauth_button.add_css_class("suggested-action")
        self.oauth_button.connect("clicked", lambda *_: self._oauth_sign_in())
        self.oauth_panel.append(self.oauth_button)
        page.append(self.oauth_panel)

        self.manual_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.manual_panel.append(self._section("Account"))
        self.name = Gtk.Entry(
            text=self.account.display_name if self.account else "",
            placeholder_text="Your name",
        )
        self.address = Gtk.Entry(
            text=self.account.address if self.account else "",
            placeholder_text="you@example.com",
        )
        self.password = Gtk.PasswordEntry(show_peek_icon=True)
        account_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        account_card.add_css_class("charlie-account-card")
        account_card.append(self._field("Name", self.name))
        account_card.append(self._field("Email", self.address))
        account_card.append(self._field("Password", self.password))
        self.manual_panel.append(account_card)
        self.password_help = Gtk.Label(xalign=0, wrap=True)
        self.password_help.add_css_class("luma-appbar-sub")
        self.manual_panel.append(self.password_help)

        self.advanced_button = Gtk.Button(
            label="Show server settings",
            halign=Gtk.Align.START,
        )
        self.advanced_button.add_css_class("charlie-disclosure")
        self.advanced_button.connect("clicked", lambda *_: self._toggle_advanced())
        self.manual_panel.append(self.advanced_button)

        self.username = Gtk.Entry(
            text=self.current.username if self.current else (
                self.account.address if self.account else ""
            ),
            placeholder_text="Usually your full email address",
        )
        self.imap_host = Gtk.Entry(
            text=self.current.imap_host if self.current else "",
            placeholder_text="imap.example.com",
        )
        self.imap_port = Gtk.Entry(
            text=str(self.current.imap_port if self.current else 993),
            input_purpose=Gtk.InputPurpose.DIGITS,
        )
        self.smtp_host = Gtk.Entry(
            text=self.current.smtp_host if self.current else "",
            placeholder_text="smtp.example.com",
        )
        self.smtp_port = Gtk.Entry(
            text=str(self.current.smtp_port if self.current else 465),
            input_purpose=Gtk.InputPurpose.DIGITS,
        )
        self.starttls = Gtk.Switch(
            active=self.current.use_starttls if self.current else False,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.END,
        )
        self.server_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.server_card.add_css_class("charlie-account-card")
        self.server_card.append(self._field("Username", self.username))
        self.server_card.append(self._field("Incoming", self.imap_host))
        self.server_card.append(self._field("IMAP port", self.imap_port))
        self.server_card.append(self._field("Outgoing", self.smtp_host))
        self.server_card.append(self._field("SMTP port", self.smtp_port))
        self.server_card.append(self._field("SMTP STARTTLS", self.starttls))
        self.manual_panel.append(self.server_card)

        self.manual_error = Gtk.Label(label="", xalign=0, wrap=True)
        self.manual_error.add_css_class("error")
        self.manual_error.set_visible(False)
        self.manual_panel.append(self.manual_error)
        save = Gtk.Button(label="Save & Sync", halign=Gtk.Align.END)
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._save())
        self.manual_panel.append(save)
        page.append(self.manual_panel)
        return self._scroll(page)

    def _provider_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._show_setup(row.charlie_provider)

    def _show_providers(self) -> None:
        if self.account:
            return
        self.heading.set_label("Add Account")
        self.back.set_visible(False)
        self.stack.set_visible_child_name("providers")
        self.provider_list.grab_focus()

    def _fill_provider_summary(self) -> None:
        while child := self.provider_summary.get_first_child():
            self.provider_summary.remove(child)
        self.provider_summary.append(Avatar(self.preset.label))
        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        name = Gtk.Label(label=self.preset.label, xalign=0)
        name.add_css_class("charlie-row-sender")
        detail = Gtk.Label(label=self.preset.subtitle, xalign=0, wrap=True)
        detail.add_css_class("luma-appbar-sub")
        labels.append(name)
        labels.append(detail)
        self.provider_summary.append(labels)

    def _show_setup(self, preset) -> None:
        self.preset = preset
        self.heading.set_label(("Edit " if self.account else "Add ") + preset.label)
        self.back.set_visible(self.account is None)
        self._fill_provider_summary()
        oauth = preset.auth_mode in {"google-oauth", "microsoft-oauth"}
        self.oauth_panel.set_visible(oauth)
        self.manual_panel.set_visible(not oauth)
        self.oauth_error.set_visible(False)
        self.manual_error.set_visible(False)

        self.oauth_button.set_sensitive(preset.auth_mode != "google-oauth")
        if preset.auth_mode == "google-oauth":
            self.oauth_copy.set_label(
                GOOGLE_UNAVAILABLE
            )
            self.manual_panel.set_visible(True)
            self.password_help.set_label(preset.password_hint)
            self.advanced_visible = bool(self.account)
            if self.account is None:
                self._apply_preset()
            self._sync_advanced()
            self.microsoft_client_id.set_visible(False)
            self.oauth_button.set_label(
                "Google sign-in unavailable"
            )
        elif preset.auth_mode == "microsoft-oauth":
            self.oauth_copy.set_label(
                "Your browser handles Microsoft sign-in. Enter Charlie’s public desktop application ID; no client secret belongs on this computer."
            )
            self.microsoft_client_id.set_visible(True)
            if self.account and self.app.secrets:
                saved = self.app.secrets.lookup(self.account.id, "oauth-token")
                if saved:
                    try:
                        self.microsoft_client_id.set_text(
                            MicrosoftOAuth.from_token_json(saved).client_id
                        )
                    except OAuthError:
                        pass
            self.oauth_button.set_label(
                "Reconnect with Microsoft" if self.account else "Continue with Microsoft"
            )
        else:
            self.password_help.set_label(
                preset.password_hint
                + (" Leave it blank to keep the saved password." if self.account else "")
            )
            self.advanced_visible = bool(self.account) or preset.key == "custom"
            # Existing accounts may intentionally override a provider's
            # standard hosts or ports.  Only apply a preset for new setup;
            # editing must preserve the configuration loaded from the store.
            if self.account is None:
                self._apply_preset()
            self._sync_advanced()
        self.stack.set_visible_child_name("setup")

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        self._sync_advanced()

    def _sync_advanced(self) -> None:
        self.server_card.set_visible(self.advanced_visible)
        self.advanced_button.set_label(
            "Hide server settings" if self.advanced_visible else "Show server settings"
        )
        self.advanced_button.set_visible(self.preset.key != "custom")

    def _apply_preset(self) -> None:
        if not self.preset.imap_host:
            return
        config = preset_config(self.preset, self.address.get_text())
        self.imap_host.set_text(config.imap_host)
        self.imap_port.set_text(str(config.imap_port))
        self.smtp_host.set_text(config.smtp_host)
        self.smtp_port.set_text(str(config.smtp_port))
        self.starttls.set_active(config.use_starttls)
        if not self.username.get_text():
            self.username.set_text(self.address.get_text())

    def _oauth_sign_in(self) -> None:
        self.oauth_error.set_visible(False)
        if self.preset.auth_mode == "google-oauth":
            self.oauth_button.set_sensitive(False)
            self.oauth_button.set_label("Google sign-in unavailable")
            self._oauth_error(GOOGLE_UNAVAILABLE)
            return
        client_id = self.microsoft_client_id.get_text().strip()
        if not client_id:
            self._oauth_error("Enter the Microsoft Application (client) ID first.")
            return
        self.oauth_button.set_sensitive(False)
        self.oauth_button.set_label("Waiting for Microsoft…")
        self.app.begin_microsoft_sign_in(client_id, self._microsoft_complete)

    def _google_complete(self, account: Account | None, error: Exception | None) -> None:
        self.oauth_button.set_sensitive(False)
        self.oauth_button.set_label("Google sign-in unavailable")
        self._oauth_error(GOOGLE_UNAVAILABLE)

    def _microsoft_complete(self, account: Account | None, error: Exception | None) -> None:
        self.oauth_button.set_sensitive(True)
        self.oauth_button.set_label("Continue with Microsoft")
        self._oauth_complete(account, error, "Microsoft")

    def _oauth_error(self, message: str) -> None:
        self.oauth_error.set_label(message)
        self.oauth_error.set_visible(True)

    def _oauth_complete(
        self,
        account: Account | None,
        error: Exception | None,
        provider: str,
    ) -> None:
        if error or account is None:
            self._oauth_error(
                str(error) if isinstance(error, OAuthError)
                else f"{provider} sign-in could not finish."
            )
            return
        self._finish(account)

    def _manual_error(self, message: str) -> None:
        self.manual_error.set_label(message)
        self.manual_error.set_visible(True)

    def _save(self) -> None:
        _name, address = address_parts(self.address.get_text())
        if not address or "@" not in address:
            self._manual_error("Enter a valid email address.")
            return
        try:
            imap_port = int(self.imap_port.get_text())
            smtp_port = int(self.smtp_port.get_text())
            if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535):
                raise ValueError
        except ValueError:
            self._manual_error("Server ports must be between 1 and 65535.")
            return
        if not self.imap_host.get_text().strip() or not self.smtp_host.get_text().strip():
            self._manual_error("Both incoming and outgoing server addresses are required.")
            return
        if not self.account and not self.password.get_text():
            self._manual_error("Enter the password or app password for this account.")
            return

        account = Account(
            id=self.account.id if self.account else account_id_for_address(address),
            display_name=self.name.get_text().strip() or address.split("@", 1)[0],
            address=address,
            provider=self.preset.key,
            colour=self.account.colour if self.account else {
                "icloud": "blue",
                "fastmail": "green",
                "yahoo": "violet",
                "zoho": "red",
                "aol": "violet",
            }.get(self.preset.key, "amber"),
            avatar_url=self.account.avatar_url if self.account else "",
        )
        config = ServerConfig(
            self.imap_host.get_text().strip(),
            imap_port,
            self.smtp_host.get_text().strip(),
            smtp_port,
            self.username.get_text().strip() or address,
            self.starttls.get_active(),
        )
        self.stack.set_sensitive(False)
        def saved(error):
            if not self.get_visible(): return
            self.stack.set_sensitive(True)
            if error is not None:
                self._manual_error("Charlie could not store this account securely.")
                return
            self._finish(account)
        try:
            self.app.save_account_async(account, config, self.password.get_text(), saved)
        except Exception as error:
            saved(error)

    def _finish(self, account: Account) -> None:
        self.manager.reload()
        if self.app.window:
            self.app.window.accounts_changed(account.id)
        self.close()
        if self.app.window:
            self.app.window._sync_all()
