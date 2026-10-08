# SPDX-License-Identifier: Apache-2.0
"""Native adaptive GTK 4 / libadwaita / Luma AppKit interface."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, replace
from hashlib import sha256
import os
from pathlib import Path
import sys
import threading
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango

from luma_appkit import (
    AppWindow,
    AddRow,
    SidebarRow,
    RowAction,
    Avatar,
    Command,
    CommandGroup,
    CommandRegistry,
    ConnectedButtonGroup,
    EmptyState,
    IconButton,
    Island,
    NavigationRow,
    NavigationSidebar,
    Toolbar,
    add_style_sheet,
    attach_context_menu,
    command_popover,
    install_appkit,
)

from . import APP_ID, __version__
from .account_ui import AccountEditorWindow as LumaAccountEditorWindow
from .background import request_background, service_installed_async
from .accounts import (
    PROVIDERS,
    account_id_for_address,
    provider_by_key,
    provider_for_address,
    preset_config,
)
from .avatar import AvatarLoader
from .engine import (
    AuthenticationFailure,
    ImapSmtpTransport,
    MailEngine,
    NetworkFailureKind,
    network_failure_kind,
)
from .fixtures import DEMO_ACCOUNT, demo_messages
from .html_reader import reader as html_reader, set_preview_mode
from .mail_agent import AGENT_NAME, AGENT_REASON, MAIL_AGENT_INTERFACE, MAIL_AGENT_PATH
from .mime import MessagePresentation, message_presentation
from .model import (
    Account,
    Attachment,
    Conversation,
    Draft,
    Message,
    ServerConfig,
    address_parts,
    normalized_subject,
)
from .oauth import (
    GmailOAuth,
    GoogleOAuthUnavailable,
    GOOGLE_UNAVAILABLE,
    require_google_oauth,
    MicrosoftOAuth,
    OAuthError,
    OAuthIdentity,
    failure_requires_sign_in,
)
from .search_provider import SearchProvider
from .window import CharlieWindow
from .secrets import SecretStore
from .store import MailStore
from .time_display import display_time


NAVIGATION_PANE_WIDTH = 178
THREAD_PANE_WIDTH = 348
MEDIUM_BREAKPOINT = 1099
COMPACT_BREAKPOINT = 699


def _data_home() -> Path:
    if Path("/.flatpak-info").exists():
        # Intentional single shared mailbox dataset; no keyring/config-home grant.
        return Path.home() / ".local/share/charlie"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "charlie"


def _cache_home() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "charlie"


def _resource(name: str) -> Path:
    override = os.environ.get("CHARLIE_RESOURCE_DIR")
    if override:
        return Path(override) / name
    return (Path("/app") if Path("/.flatpak-info").exists() else Path("/usr")) / "share/charlie" / name


def _set_accessible(widget: Gtk.Widget, label: str) -> Gtk.Widget:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return widget


def _clear(container: Gtk.Widget) -> None:
    while child := container.get_first_child():
        if isinstance(container, Gtk.ListBox):
            container.remove(child)
        else:
            container.remove(child)


class HtmlMessagePreview(Gtk.Box):
    """A measured formatted message that expands inside its conversation."""

    COLLAPSED_HEIGHT = 340

    def __init__(self, html: str, on_open: Callable[[], None] | None = None, *, private: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("charlie-html-preview")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_vexpand(False)
        self._expanded = False
        self._content_height = self.COLLAPSED_HEIGHT

        self.frame = Gtk.Overlay()
        self.frame.add_css_class("charlie-html-preview-frame")
        self.frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.frame.set_vexpand(False)
        self.frame.set_size_request(-1, self.COLLAPSED_HEIGHT)
        self.view = html_reader(
            html,
            allow_remote=True,
            preview=True,
            on_content_height=self._content_height_ready,
        )
        self.view.set_hexpand(True)
        self.view.set_vexpand(True)
        if on_open is not None:
            self.view.set_cursor_from_name("pointer")
            self.view.set_tooltip_text("Open full message")
            activate = Gtk.GestureClick.new()
            activate.set_button(Gdk.BUTTON_PRIMARY)
            activate.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

            def open_message(_gesture, n_press: int, _x: float, _y: float) -> None:
                if n_press == 1:
                    on_open()

            activate.connect("released", open_message)
            self.view.add_controller(activate)
        self.viewport = Gtk.ScrolledWindow()
        self.viewport.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.viewport.set_propagate_natural_width(False)
        self.viewport.set_propagate_natural_height(False)
        self.viewport.set_child(self.view)
        self.frame.set_child(self.viewport)

        foot = Gtk.CenterBox(orientation=Gtk.Orientation.HORIZONTAL)
        foot.add_css_class("charlie-html-preview-foot")
        foot.set_halign(Gtk.Align.FILL)
        foot.set_valign(Gtk.Align.END)
        self.expand_button = Gtk.Button(label="Show full email")
        self.expand_button.set_valign(Gtk.Align.END)
        self.expand_button.add_css_class("flat")
        self.expand_button.add_css_class("charlie-html-action")
        self.expand_button.add_css_class("charlie-html-expand")
        self.expand_button.connect("clicked", self._toggle_expanded)
        foot.set_center_widget(self.expand_button)
        self.frame.add_overlay(foot)
        self.append(self.frame)

        self.collapse_button = Gtk.Button(label="Show less")
        self.collapse_button.set_hexpand(True)
        self.collapse_button.set_halign(Gtk.Align.FILL)
        self.collapse_button.add_css_class("flat")
        self.collapse_button.add_css_class("charlie-html-action")
        self.collapse_button.add_css_class("charlie-html-collapse")
        self.collapse_button.connect("clicked", self._toggle_expanded)
        self.collapse_button.set_visible(False)
        self.append(self.collapse_button)
        # v71 .crpriv: a sender's remote images wait until asked for.
        self.privacy = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, visible=private)
        self.privacy.add_css_class("charlie-html-privacy")
        shield = Gtk.Label(label="Images hidden to keep you private", xalign=0, hexpand=True)
        shield.add_css_class("lumaui-t-caption")
        shield.add_css_class("lumaui-t-muted")
        self.privacy.append(shield)
        load = Gtk.Button(label="Load images")
        load.add_css_class("charlie-html-load")
        load.connect("clicked", lambda _b: self.privacy.set_visible(False))
        self.privacy.append(load)
        self.append(self.privacy)

    def _content_height_ready(self, height: int) -> None:
        self._content_height = height
        if self._expanded:
            self._apply_expanded_height()

    def _apply_expanded_height(self) -> None:
        self.frame.set_size_request(
            -1,
            max(self.COLLAPSED_HEIGHT, self._content_height),
        )

    def _toggle_expanded(self, *_args) -> None:
        self._expanded = not self._expanded
        self.expand_button.set_visible(not self._expanded)
        self.collapse_button.set_visible(self._expanded)
        set_preview_mode(self.view, not self._expanded)
        if self._expanded:
            self._apply_expanded_height()
        else:
            self.frame.set_size_request(-1, self.COLLAPSED_HEIGHT)


class HtmlMessageWindow(AppWindow):
    def __init__(self, application: "CharlieApplication", message: Message) -> None:
        super().__init__(
            application=application,
            app_id=APP_ID,
            title=message.subject,
            icon_name=APP_ID,
            commands=CommandRegistry(()),
            geometry_scope="formatted-message",
            default_width=900,
            default_height=760,
            minimum_width=360,
            minimum_height=420,
        )
        island = Island()
        toolbar = Toolbar()
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        heading = Gtk.Label(label=message.subject, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        heading.add_css_class("luma-action-title")
        detail = Gtk.Label(
            label=f"{message.sender_label} · {display_time(message.sent_at)}",
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
        )
        detail.add_css_class("luma-appbar-sub")
        titles.append(heading); titles.append(detail)
        toolbar.append(titles)
        close = IconButton("window-close-symbolic", "Close", context=self.context, quiet=True)
        close.connect("clicked", lambda *_: self.close())
        toolbar.append(close)
        island.append(toolbar)
        presentation = message_presentation(message)
        view = html_reader(presentation.html or message.body_html, allow_remote=True)
        view.set_hexpand(True); view.set_vexpand(True)
        island.append(view)
        self.set_body(island)


class AccountEditorWindow(AppWindow):
    """Add or edit one provider-neutral IMAP/SMTP account."""

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
            geometry_scope="account-editor",
            default_width=620,
            default_height=720,
            minimum_width=360,
            minimum_height=520,
        )
        self.app = application
        self.manager = manager
        self.account = account
        current = application.store.server_config(account.id) if account else None

        island = Island()
        island.add_css_class("charlie-account-form")
        toolbar = Toolbar()
        title = Gtk.Label(label="Edit Account" if account else "Add Account", xalign=0, hexpand=True)
        title.add_css_class("luma-action-title")
        toolbar.append(title)
        close = IconButton("window-close-symbolic", "Close", context=self.context, quiet=True)
        close.connect("clicked", lambda *_: self.close())
        toolbar.append(close)
        island.append(toolbar)

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form.set_margin_start(18); form.set_margin_end(18)
        form.set_margin_top(16); form.set_margin_bottom(18)
        self.google_button = Gtk.Button(
            label="Google sign-in unavailable"
        )
        self.google_button.set_sensitive(False)
        self.google_button.connect("clicked", lambda *_: self._google_sign_in())
        self._append_field(
            form,
            "Gmail",
            self.google_button,
            GOOGLE_UNAVAILABLE,
        )
        divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        divider.set_margin_top(4); divider.set_margin_bottom(4)
        form.append(divider)
        manual = Gtk.Label(label="Other account or manual setup", xalign=0)
        manual.add_css_class("luma-action-title")
        form.append(manual)
        self.name = Gtk.Entry(text=account.display_name if account else "", placeholder_text="Your name")
        self.address = Gtk.Entry(text=account.address if account else "", placeholder_text="you@example.com")
        self.provider = Gtk.DropDown.new_from_strings([preset.label for preset in PROVIDERS])
        initial_provider = provider_by_key(account.provider) if account else PROVIDERS[-1]
        self.provider.set_selected(PROVIDERS.index(initial_provider))
        self.username = Gtk.Entry(
            text=current.username if current else (account.address if account else ""),
            placeholder_text="Usually your full email address",
        )
        self.password = Gtk.PasswordEntry(show_peek_icon=True)
        self.password.set_tooltip_text(
            "Leave blank to keep the saved password" if account else "Password or app password"
        )
        self.imap_host = Gtk.Entry(text=current.imap_host if current else "", placeholder_text="imap.example.com")
        self.imap_port = Gtk.Entry(text=str(current.imap_port if current else 993), input_purpose=Gtk.InputPurpose.DIGITS)
        self.smtp_host = Gtk.Entry(text=current.smtp_host if current else "", placeholder_text="smtp.example.com")
        self.smtp_port = Gtk.Entry(text=str(current.smtp_port if current else 465), input_purpose=Gtk.InputPurpose.DIGITS)
        self.starttls = Gtk.Switch(active=current.use_starttls if current else False, valign=Gtk.Align.CENTER)

        self._append_field(form, "Name", self.name)
        self._append_field(form, "Email address", self.address)
        self._append_field(form, "Provider", self.provider,
                           "Use your provider’s password or app password for manual setup.")
        self._append_field(form, "Username", self.username)
        self._append_field(form, "Password", self.password,
                           "Stored only in Luma’s encrypted Secret Service.")
        self._append_field(form, "Incoming server", self.imap_host)
        self._append_field(form, "IMAP port", self.imap_port)
        self._append_field(form, "Outgoing server", self.smtp_host)
        self._append_field(form, "SMTP port", self.smtp_port)
        self._append_field(form, "Use STARTTLS for SMTP", self.starttls)

        self.error = Gtk.Label(label="", xalign=0, wrap=True)
        self.error.add_css_class("error")
        self.error.set_visible(False)
        form.append(self.error)
        save = Gtk.Button(label="Save & Sync", halign=Gtk.Align.END)
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._save())
        form.append(save)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(form)
        island.append(scroll)
        self.set_body(island)

        self.provider.connect("notify::selected", lambda *_: self._apply_provider())
        if not account:
            self.address.connect("changed", self._detect_provider)

    @staticmethod
    def _append_field(container: Gtk.Box, label: str, widget: Gtk.Widget, hint: str = "") -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.add_css_class("charlie-form-row")
        heading = Gtk.Label(label=label, xalign=0)
        heading.add_css_class("charlie-form-label")
        row.append(heading)
        row.append(widget)
        if hint:
            detail = Gtk.Label(label=hint, xalign=0, wrap=True)
            detail.add_css_class("luma-appbar-sub")
            row.append(detail)
        container.append(row)

    def _detect_provider(self, entry: Gtk.Entry) -> None:
        if "@" not in entry.get_text():
            return
        preset = provider_for_address(entry.get_text())
        self.provider.set_selected(PROVIDERS.index(preset))

    def _apply_provider(self) -> None:
        preset = PROVIDERS[self.provider.get_selected()]
        if not preset.imap_host:
            return
        config = preset_config(preset, self.address.get_text())
        self.imap_host.set_text(config.imap_host)
        self.imap_port.set_text(str(config.imap_port))
        self.smtp_host.set_text(config.smtp_host)
        self.smtp_port.set_text(str(config.smtp_port))
        self.starttls.set_active(config.use_starttls)
        if not self.username.get_text() or self.username.get_text() == self.address.get_text():
            self.username.set_text(self.address.get_text())

    def _show_error(self, message: str) -> None:
        self.error.set_label(message)
        self.error.set_visible(True)

    def _google_sign_in(self) -> None:
        self._show_error(GOOGLE_UNAVAILABLE)

    def _google_complete(self, account: Account | None, error: Exception | None) -> None:
        self.google_button.set_sensitive(False)
        self.google_button.set_label("Google sign-in unavailable")
        self._show_error(GOOGLE_UNAVAILABLE)

    def _save(self) -> None:
        if getattr(self, "_saving", False): return
        _name, address = address_parts(self.address.get_text())
        if not address or "@" not in address:
            self._show_error("Enter a valid email address.")
            return
        try:
            imap_port = int(self.imap_port.get_text())
            smtp_port = int(self.smtp_port.get_text())
            if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535):
                raise ValueError
        except ValueError:
            self._show_error("Server ports must be between 1 and 65535.")
            return
        if not self.imap_host.get_text().strip() or not self.smtp_host.get_text().strip():
            self._show_error("Both incoming and outgoing server addresses are required.")
            return
        if not self.account and not self.password.get_text():
            self._show_error("Enter the password or app password for this account.")
            return

        preset = PROVIDERS[self.provider.get_selected()]
        account = Account(
            id=self.account.id if self.account else account_id_for_address(address),
            display_name=self.name.get_text().strip() or address.split("@", 1)[0],
            address=address,
            provider=preset.key,
            colour=self.account.colour if self.account else {
                "gmail": "red", "microsoft": "blue", "icloud": "blue",
                "fastmail": "green", "yahoo": "violet",
            }.get(preset.key, "amber"),
            avatar_url=self.account.avatar_url if self.account else "",
        )
        config = ServerConfig(
            self.imap_host.get_text().strip(), imap_port,
            self.smtp_host.get_text().strip(), smtp_port,
            self.username.get_text().strip() or address,
            self.starttls.get_active(),
        )
        self._saving = True
        self.password.set_sensitive(False)
        def saved(error):
            if not self.get_visible(): return
            self._saving = False
            self.password.set_sensitive(True)
            if error is not None:
                self._show_error("Charlie could not store this account securely.")
                return
            self.manager.reload()
            if self.app.window: self.app.window.accounts_changed(account.id)
            self.close()
            if self.app.window: self.app.window._sync_all()
        try:
            self.app.save_account_async(account, config, self.password.get_text(), saved)
        except Exception as error:
            saved(error)


class AccountWindow(AppWindow):
    def __init__(self, application: "CharlieApplication") -> None:
        super().__init__(
            application=application, app_id=APP_ID, title="Accounts",
            icon_name=APP_ID, commands=CommandRegistry(()), geometry_scope="accounts",
            default_width=660, default_height=560, minimum_width=360, minimum_height=420,
        )
        self.app = application
        island = Island()
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("luma-navigation-list")
        self.list.connect("row-activated", lambda _list, row: self._edit(row.charlie_account))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.list)
        self.empty = EmptyState("Your mail, together", "Add an account to read and send your email in Charlie.",
                               "mail", primary=("Add account", lambda: self._edit(None)))
        self.pages = Gtk.Stack(hexpand=True, vexpand=True)
        self.pages.add_named(self.empty, "empty")
        self.pages.add_named(scroll, "accounts")
        island.append(self.pages)
        self.add_account = AddRow("Add account", icon="plus", on_activate=lambda: self._edit(None))
        island.append(self.add_account)
        self.set_body(island)
        self.reload()

    def reload(self) -> None:
        _clear(self.list)
        accounts = self.app.store.accounts()
        self.pages.set_visible_child_name("accounts" if accounts else "empty")
        self.add_account.set_visible(bool(accounts))
        for account in accounts:
            row = SidebarRow(account.display_name, subtitle=account.address,
                             lead=self.app.avatar_widget(account.display_name, account.address,
                                                         account_id=account.id),
                             trail=RowAction("trash-2", f"Remove {account.display_name}",
                                             lambda value=account: self._confirm_remove(value)))
            row.charlie_account = account
            row.set_tooltip_text(f"Edit {account.display_name}")
            self.list.append(row)

    def _edit(self, account: Account | None) -> None:
        self.app.show_account_editor(self, account)

    def _confirm_remove(self, account: Account) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Remove {account.display_name}?",
            body="Its locally cached mail and saved credential will be removed from Charlie.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _dialog, response: self._remove(account) if response == "remove" else None)
        dialog.present(self)

    def _remove(self, account: Account) -> None:
        self.list.set_sensitive(False)
        def removed(error):
            if not self.get_visible(): return
            self.list.set_sensitive(True)
            if error is not None:
                dialog = Adw.AlertDialog(heading="Account could not be removed",
                                        body="Please try again when the mail service is available.")
                dialog.add_response("close", "Close"); dialog.present(self)
                return
            self.reload()
            if self.app.window: self.app.window.accounts_changed(None)
        try: self.app.remove_account_async(account.id, removed)
        except Exception as error: removed(error)


# Keys that type or edit text. A shortcut made of one of them alone must never
# fire while the person is writing.
_TEXT_EDITING_KEYS = {"Delete", "BackSpace", "space"}
_MODIFIERS = {"Ctrl", "Shift", "Alt", "Super"}


def _single_key(shortcut: tuple[str, ...]) -> str | None:
    """The key of a shortcut that is one unmodified typing key, else None."""
    if len(shortcut) != 1 or shortcut[0] in _MODIFIERS:
        return None
    key = shortcut[0]
    return key.lower() if len(key) == 1 else (key if key in _TEXT_EDITING_KEYS else None)


def _typing(window: Gtk.Window) -> bool:
    """True when keyboard focus is in something that takes text."""
    focus = window.get_focus()
    while focus is not None:
        if isinstance(focus, (Gtk.Editable, Gtk.TextView)):
            return True
        focus = focus.get_parent()
    return False


def guard_single_key_shortcuts(window: Gtk.Window, application: Gtk.Application,
                               registry: CommandRegistry) -> tuple[str, ...]:
    """Keep one-key shortcuts such as E (Archive) out of text fields.

    The kit installs every command shortcut as an application accelerator,
    and GTK runs accelerators before the focused widget sees the key, so a
    bare E archived the conversation instead of typing "e" into the inline
    reply. Each one-key shortcut moves to a shortcut controller on this
    window in the bubble phase (after the focused widget has had the key),
    and fires only when focus is not in a text field; Delete and Backspace in
    an empty field therefore never trash a conversation either. The menu
    keeps showing the key. Returns the guarded command ids.
    """
    controller = Gtk.ShortcutController()
    controller.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
    controller.set_scope(Gtk.ShortcutScope.LOCAL)
    guarded: dict[str, str] = {}
    for group in registry.groups:
        for command in group.commands:
            key = _single_key(command.shortcut)
            if key is None:
                continue
            action = command.id.replace(".", "-")
            application.set_accels_for_action(f"app.{action}", [])
            guarded[f"app.{action}"] = key

            def fire(_widget, _args, action=action) -> bool:
                if _typing(window) or not application.get_action_enabled(action):
                    return False
                application.activate_action(action, None)
                return True

            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(key), Gtk.CallbackAction.new(fire)))
    if not guarded:
        return ()
    window.add_controller(controller)
    _label_menu_keys(application.get_menubar(), guarded)
    return tuple(guarded)


def _label_menu_keys(model: Gio.MenuModel | None, keys: dict[str, str]) -> None:
    """Show a guarded key beside its menu item without binding it."""
    if not isinstance(model, Gio.Menu):
        return
    for index in range(model.get_n_items()):
        for link in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
            _label_menu_keys(model.get_item_link(index, link), keys)
        action = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION, GLib.VariantType.new("s"))
        if action is not None and action.get_string() in keys:
            item = Gio.MenuItem.new_from_model(model, index)
            item.set_attribute_value("accel", GLib.Variant.new_string(keys[action.get_string()]))
            model.remove(index)
            model.insert_item(index, item)


def open_attachment(window: Gtk.Window, attachment: Attachment) -> None:
    """Open a downloaded attachment with its default app (a private cached copy)."""
    cache = _cache_home() / "attachment-previews"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_name = Path(attachment.filename).name or "attachment"
    destination = cache / f"{sha256(attachment.data).hexdigest()[:16]}-{safe_name}"
    if not destination.exists():
        destination.write_bytes(attachment.data)
        destination.chmod(0o600)
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(destination)))

    def opened(file_launcher: Gtk.FileLauncher, result) -> None:
        try:
            file_launcher.launch_finish(result)
        except GLib.Error:
            pass
    launcher.launch(window, None, opened)


def save_attachment(window: Gtk.Window, attachment: Attachment) -> None:
    """Save a downloaded attachment where the person chooses."""
    dialog = Gtk.FileDialog(title="Save Attachment", initial_name=attachment.filename)

    def saved(file_dialog: Gtk.FileDialog, result) -> None:
        try:
            destination = file_dialog.save_finish(result)
            destination.replace_contents(attachment.data, None, False, Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        except GLib.Error:
            pass
    dialog.save(window, None, saved)


class CharlieApplication(Adw.Application):
    def __init__(self, *, data_home: Path | None = None, seed_demo: bool = False) -> None:
        GLib.set_application_name("Charlie")
        GLib.set_prgname(APP_ID)
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN)
        self.data_home = data_home or _data_home()
        self.avatar_loader = AvatarLoader(_cache_home() / "avatars")
        self.seed_demo = seed_demo or os.environ.get("CHARLIE_DEMO") == "1"
        # LUMA_CHARLIE_FIXTURE=v71: simulator v71's mailbox in memory; nothing syncs, sends or is written to disk.
        self.fixture_mode = os.environ.get("LUMA_CHARLIE_FIXTURE", "")
        self.fixture = None
        self.store: MailStore | None = None
        self.secrets: SecretStore | None = None
        self.engine: MailEngine | None = None
        self.host_client = None
        self.window: CharlieWindow | None = None
        self.account_window: AccountWindow | None = None
        self.account_editor: AccountEditorWindow | None = None
        self.formatted_message_window: HtmlMessageWindow | None = None
        self.pending_uri = ""
        self.search_provider: SearchProvider | None = None
        self.did_initial_sync = False
        self.google_oauth = GmailOAuth()
        self.oauth_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="charlie-oauth")
        self.avatar_hydration_pending: set[str] = set()

    def show_accounts(self) -> None:
        if self.account_window is None:
            self.account_window = AccountWindow(self)
            self.account_window.connect("close-request", self._account_window_closed)
        else:
            self.account_window.reload()
        self.account_window.present()

    def _account_window_closed(self, _window: AccountWindow) -> bool:
        self.account_window = None
        return False

    def show_account_editor(self, manager: AccountWindow, account: Account | None) -> None:
        if self.account_editor is not None:
            self.account_editor.close()
        self.account_editor = LumaAccountEditorWindow(self, manager, account)
        self.account_editor.connect("close-request", self._account_editor_closed)
        self.account_editor.present()

    def _account_editor_closed(self, _window: AccountEditorWindow) -> bool:
        self.account_editor = None
        return False

    def show_formatted_message(self, message: Message) -> None:
        if self.formatted_message_window is not None:
            self.formatted_message_window.close()
        self.formatted_message_window = HtmlMessageWindow(self, message)
        self.formatted_message_window.connect(
            "close-request", self._formatted_message_closed
        )
        self.formatted_message_window.present()

    def _formatted_message_closed(self, _window: HtmlMessageWindow) -> bool:
        self.formatted_message_window = None
        return False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        for name, callback in (
            ("open-message", self._open_message_action),
            ("mark-read", self._mark_read_action),
            ("archive-message", self._archive_message_action),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", callback)
            self.add_action(action)
        install_appkit()
        if scheme := os.environ.get("CHARLIE_TEST_COLOR_SCHEME"):
            manager = Adw.StyleManager.get_default()
            manager.set_color_scheme(
                Adw.ColorScheme.FORCE_LIGHT if scheme == "light" else Adw.ColorScheme.FORCE_DARK
            )
        style = _resource("charlie.css")
        if style.exists():
            add_style_sheet(str(style))
        self._open_store()
        if Path("/.flatpak-info").exists():
            from .host_mail_client import Client, Engine
            self.host_client = Client()
            self.engine = Engine(self.host_client)
        else:
            self.secrets = SecretStore()
            self.engine = MailEngine(
                self.store,
                ImapSmtpTransport(self.secrets.lookup, self.oauth_access_token),
            )

    def _open_store(self) -> MailStore:
        if self.store is None and self.fixture_mode:
            from . import fixture_v71
            self.store = MailStore(Path(":memory:"))
            self.fixture = fixture_v71.load(self.store)
            return self.store
        if self.store is None:
            self.store = MailStore(self.data_home / "mail.db")
            if self.seed_demo and not self.store.accounts():
                self.store.upsert_account(DEMO_ACCOUNT)
                for message in demo_messages():
                    self.store.upsert_message(message)
        return self.store

    def do_dbus_register(self, connection: Gio.DBusConnection, object_path: str) -> bool:
        """Export search before the bus name is owned, so the Shell's first query finds it.

        The Shell starts Charlie over D-Bus to search mail. That start uses
        --gapplication-service and opens no window; only Activate does.
        """
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        self.search_provider = SearchProvider(self, self._open_store())
        self.search_provider.export(connection)
        return True

    def do_dbus_unregister(self, connection: Gio.DBusConnection, object_path: str) -> None:
        if self.search_provider:
            self.search_provider.unexport(connection)
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def ensure_mail_agent(self) -> None:
        """Ask luma-background to run the mail agent if it is not running (ADR-033).

        New-mail notifications come from the agent, which runs whether or not
        this window is open and opens messages here through app.open-message.
        The request is made only when there is an account to watch and the
        service is installed; the service identifies Charlie by this process,
        applies the person's decision and starts an allowed agent. Without the
        service the window works on its own, as before. Nothing here starts
        the agent directly, and nothing waits.
        """
        connection = self.get_dbus_connection()
        if connection is None or self.seed_demo or self.fixture is not None or not self.configured_accounts():
            return

        def request(installed: bool) -> None:
            if installed:
                request_background(APP_ID, reason=AGENT_REASON, autostart=True, connection=connection)

        def running(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                owned = bool(bus.call_finish(result).unpack()[0])
            except GLib.Error:
                owned = False
            if not owned:
                service_installed_async(bus, request)

        connection.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (AGENT_NAME,)), GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE,
            5000, None, running,
        )

    def call_mail_agent(self, method: str) -> None:
        """Tell a running agent to CheckNow or Reload; never starts it and never waits."""
        connection = self.get_dbus_connection()
        if connection is None:
            return
        connection.call(
            AGENT_NAME, MAIL_AGENT_PATH, MAIL_AGENT_INTERFACE, method, None, None,
            Gio.DBusCallFlags.NO_AUTO_START, 10000, None, self._mail_agent_answered,
        )

    @staticmethod
    def _mail_agent_answered(connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            connection.call_finish(result)
        except GLib.Error:
            pass  # not running, not installed or turned off: the window keeps syncing on its own

    def _open_message_action(self, _action, parameter: GLib.Variant) -> None:
        self.activate()
        if self.window:
            # The agent may have stored this message after the list was built.
            self.window.refresh()
            self.window.open_message_id(parameter.get_string())

    def _mark_read_action(self, _action, parameter: GLib.Variant) -> None:
        if self.store and self.engine:
            messages = self.store.messages_by_ids((parameter.get_string(),))
            self.engine.mark_read(messages, True)
            if self.window:
                self.window.refresh()

    def _archive_message_action(self, _action, parameter: GLib.Variant) -> None:
        if self.store:
            self.store.move_messages((parameter.get_string(),), "archive")
            if self.window:
                self.window.refresh()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = CharlieWindow(self)
            self.ensure_mail_agent()
        if scheme := os.environ.get("CHARLIE_TEST_COLOR_SCHEME"):
            Adw.StyleManager.get_for_display(self.window.get_display()).set_color_scheme(
                Adw.ColorScheme.FORCE_LIGHT if scheme == "light" else Adw.ColorScheme.FORCE_DARK
            )
        self.window.present()
        if os.environ.pop("CHARLIE_TEST_OPEN_FIRST", "") == "1":
            GLib.idle_add(self._open_first_test_conversation)
        if screenshot_path := os.environ.pop("CHARLIE_TEST_SCREENSHOT", ""):
            GLib.timeout_add(1600, self._capture_test_window, screenshot_path)
        if not self.did_initial_sync and self.fixture is None:
            self.did_initial_sync = True
            GLib.idle_add(lambda: (self.window._sync_all(), GLib.SOURCE_REMOVE)[1])
        if self.pending_uri:
            uri, self.pending_uri = self.pending_uri, ""
            self._route_uri(uri)

    def _open_first_test_conversation(self) -> bool:
        if self.window is not None and self.window._threads:
            self.window.open_thread(self.window._threads[0].id)
        return GLib.SOURCE_REMOVE

    def _capture_test_window(self, destination: str) -> bool:
        """Snapshot synthetic visual fixtures without capturing the desktop."""
        if self.window is None:
            return GLib.SOURCE_REMOVE
        width, height = self.window.get_width(), self.window.get_height()
        snapshot = Gtk.Snapshot.new()
        Gtk.WidgetPaintable.new(self.window).snapshot(snapshot, width, height)
        node = snapshot.to_node()
        surface = self.window.get_surface()
        if node is None or surface is None:
            return GLib.SOURCE_REMOVE
        renderer = Gsk.Renderer.new_for_surface(surface)
        bounds = Graphene.Rect().init(0, 0, width, height)
        renderer.render_texture(node, bounds).save_to_png(destination)
        return GLib.SOURCE_REMOVE

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        arguments = command_line.get_arguments()[1:]
        self.activate()
        for argument in arguments:
            if argument.startswith(("mailto:", "charlie:")):
                self._route_uri(argument)
        return 0

    def do_open(self, files, _count, _hint) -> None:
        self.activate()
        for item in files:
            uri = item.get_uri()
            if uri:
                self._route_uri(uri)

    def _route_uri(self, uri: str) -> None:
        if self.window is None:
            self.pending_uri = uri; return
        if uri.startswith("mailto:"):
            self.window.open_mailto(uri)
        elif uri.startswith("charlie://message/"):
            self.window.open_message_id(unquote(uri.rsplit("/", 1)[-1]))

    def configured_accounts(self) -> tuple[Account, ...]:
        if self.store is None:
            return ()
        return tuple(
            account for account in self.store.accounts()
            if account.enabled and self.store.server_config(account.id) is not None
        )

    def avatar_widget(
        self,
        name: str,
        address: str,
        *,
        account_id: str = "",
        compact: bool = False,
    ) -> Gtk.Widget:
        """An account's or a person's face (the kit's avatar)."""
        del address, account_id
        return Avatar(name, compact=compact)

    def hydrate_account_avatar(self, account: Account) -> None:
        """Fill an existing Gmail account photo after a proven-good sync."""

        if (
            account.provider != "gmail"
            or account.avatar_url
            or account.id in self.avatar_hydration_pending
        ):
            return
        if self.host_client is not None:
            def hydrated(_result, _error):
                if self.window and not self.host_client.closed.is_set():
                    GLib.idle_add(lambda: (self.window._populate_sidebar(), self.window.refresh(), GLib.SOURCE_REMOVE)[-1] if self.window else GLib.SOURCE_REMOVE)
            self.engine._submit("HydrateAvatar", {"account_id": account.id}, hydrated)
            return
        self.avatar_hydration_pending.add(account.id)

        def load() -> str:
            token = self.oauth_access_token(account)
            return self.google_oauth.profile_picture_for_access_token(token or "")

        future = self.oauth_pool.submit(load)

        def done(result: Future) -> None:
            try:
                picture = str(result.result())
            except Exception:
                picture = ""

            def on_main() -> bool:
                self.avatar_hydration_pending.discard(account.id)
                if picture and self.store is not None:
                    current = next(
                        (value for value in self.store.accounts() if value.id == account.id),
                        None,
                    )
                    if current is not None and not current.avatar_url:
                        self.store.upsert_account(replace(current, avatar_url=picture))
                        if self.window:
                            self.window._populate_sidebar()
                            self.window.refresh()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(on_main)

        future.add_done_callback(done)

    def default_account_id(self) -> str:
        accounts = self.configured_accounts()
        return accounts[0].id if accounts else ""

    def _account_operation_async(self, operation, arguments, native_action, complete):
        def finished(_result, error):
            def on_main():
                if self.host_client is not None and self.host_client.closed.is_set():
                    return GLib.SOURCE_REMOVE
                if error is None and self.host_client is not None:
                    self.call_mail_agent("Reload")
                    if operation == "SaveAccount": self.ensure_mail_agent()
                complete(error)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(on_main)
        if self.host_client is not None:
            return self.engine._submit(operation, arguments, finished)
        future = self.oauth_pool.submit(native_action)
        def done(result):
            try: result.result(); error = None
            except Exception as failure: error = failure
            finished(None, error)
        future.add_done_callback(done)
        return future

    def save_account_async(self, account, config, password, complete):
        return self._account_operation_async("SaveAccount",
            {"account": asdict(account), "config": asdict(config), "password": password},
            lambda: self.save_account(account, config, password), complete)

    def remove_account_async(self, account_id, complete):
        return self._account_operation_async("RemoveAccount", {"account_id": account_id},
            lambda: self.remove_account(account_id), complete)

    def save_account(self, account: Account, config: ServerConfig, password: str) -> None:
        if self.host_client is not None:
            self.host_client.request("SaveAccount", {"account": asdict(account), "config": asdict(config), "password": password})
            self.call_mail_agent("Reload")
            self.ensure_mail_agent()
            return
        if self.store is None: return
        from .account_lifecycle import account_lock
        with account_lock(self.store.path, account.id):
            return self._save_account_locked(account, config, password)

    def _save_account_locked(self, account, config, password):
        if self.store is None or self.secrets is None:
            raise RuntimeError("Charlie has not finished starting")
        if password:
            self.secrets.store(account.id, "password", password)
            self.secrets.clear(account.id, "oauth-token")
        elif (
            self.secrets.lookup(account.id, "password") is None
            and self.secrets.lookup(account.id, "oauth-token") is None
        ):
            raise PermissionError("Account credentials are unavailable")
        self.store.upsert_account(account)
        self.store.upsert_server_config(account.id, config)
        self.call_mail_agent("Reload")
        self.ensure_mail_agent()

    def begin_google_sign_in(
        self, complete: Callable[[Account | None, Exception | None], None]
    ) -> Future:
        error = GoogleOAuthUnavailable(GOOGLE_UNAVAILABLE)
        future = Future()
        future.set_exception(error)
        GLib.idle_add(lambda: (complete(None, error), GLib.SOURCE_REMOVE)[1])
        return future

    def _begin_host_sign_in(self, operation, arguments, complete):
        future = self.oauth_pool.submit(self.host_client.request, operation, arguments)
        def done(result):
            try:
                from .host_mail_wire import account as decode_account
                value, error = decode_account(result.result()["account"]), None
            except Exception as failure:
                value, error = None, failure
            def on_main():
                if not self.host_client.closed.is_set():
                    if error is None:
                        self.call_mail_agent("Reload")
                        self.ensure_mail_agent()
                    complete(value, error)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(on_main)
        future.add_done_callback(done)
        return future

    def _open_oauth_uri(self, uri: str) -> bool:
        finished = threading.Event()
        launched = [False]

        def on_main() -> bool:
            try:
                launched[0] = Gio.AppInfo.launch_default_for_uri(uri, None)
            finally:
                finished.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(on_main)
        return finished.wait(10) and launched[0]

    def begin_microsoft_sign_in(
        self,
        client_id: str,
        complete: Callable[[Account | None, Exception | None], None],
    ) -> Future:
        if self.host_client is not None:
            return self._begin_host_sign_in("MicrosoftSignIn", {"client_id": client_id}, complete)
        oauth = MicrosoftOAuth(client_id)
        future = self.oauth_pool.submit(oauth.sign_in, self._open_oauth_uri)

        def done(result: Future) -> None:
            try:
                identity: OAuthIdentity = result.result()
                account = Account(
                    id=account_id_for_address(identity.email),
                    display_name=identity.name or identity.email.split("@", 1)[0],
                    address=identity.email,
                    provider="microsoft",
                    colour="blue",
                )
                config = preset_config(provider_by_key("microsoft"), identity.email)
                if self.store is None or self.secrets is None:
                    raise RuntimeError("Charlie has not finished starting")
                from .account_lifecycle import account_lock
                with account_lock(self.store.path, account.id):
                    self.secrets.store(account.id, "oauth-token", identity.token_json)
                    self.secrets.clear(account.id, "password")
                    self.store.upsert_account(account)
                    self.store.upsert_server_config(account.id, config)

                def signed_in() -> bool:
                    self.call_mail_agent("Reload")
                    self.ensure_mail_agent()
                    complete(account, None)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(signed_in)
            except Exception as error:
                GLib.idle_add(
                    lambda value=error: (complete(None, value), GLib.SOURCE_REMOVE)[1]
                )

        future.add_done_callback(done)
        return future

    def oauth_access_token(
        self, account: Account, force_refresh: bool = False
    ) -> str | None:
        if self.secrets is None:
            return None
        from .account_lifecycle import account_lock
        with account_lock(self.store.path, account.id):
            if not any(value.id == account.id for value in self.store.accounts()): return None
            return self._oauth_access_token_locked(account, force_refresh)

    def _oauth_access_token_locked(self, account, force_refresh=False):
        token_json = self.secrets.lookup(account.id, "oauth-token")
        if not token_json:
            return None
        try:
            if account.provider == "gmail":
                require_google_oauth()
            oauth = (
                MicrosoftOAuth.from_token_json(token_json)
                if account.provider == "microsoft" else self.google_oauth
            )
            access_token, refreshed = oauth.access_token(token_json, force_refresh=force_refresh)
        except GoogleOAuthUnavailable as error:
            raise OSError(str(error)) from None
        except OAuthError as error:
            if failure_requires_sign_in(error):
                raise AuthenticationFailure(
                    f"{account.provider.title()} refused the saved sign-in"
                ) from None
            raise OSError(
                f"{account.provider.title()}'s token service is temporarily unavailable"
            ) from None
        if refreshed:
            self.secrets.store(account.id, "oauth-token", refreshed)
        return access_token

    def remove_account(self, account_id: str) -> None:
        if self.host_client is not None:
            self.host_client.request("RemoveAccount", {"account_id": account_id})
            self.call_mail_agent("Reload")
            return
        if self.store is None: return
        from .account_lifecycle import account_lock
        with account_lock(self.store.path, account_id):
            return self._remove_account_locked(account_id)

    def _remove_account_locked(self, account_id):
        if self.store is None or self.secrets is None:
            return
        self.secrets.clear(account_id, "password")
        self.secrets.clear(account_id, "oauth-token")
        self.store.delete_account(account_id)
        self.call_mail_agent("Reload")

    def sync_account(
        self, account: Account, complete: Callable[[Exception | None], None] | None = None
    ) -> None:
        if self.store is None or self.engine is None:
            return
        config = self.store.server_config(account.id)
        if config is None:
            if complete:
                complete(RuntimeError("Account server settings are unavailable"))
            return

        def finished(_messages: tuple[Message, ...] | None, error: Exception | None) -> None:
            def on_main() -> bool:
                if self.window:
                    self.window._populate_sidebar()
                    self.window.refresh()
                if error is None:
                    # A successful foreground connection proves the account is
                    # healthy. Restart a background worker that may have been
                    # latched by an older build and withdraw its stale alert.
                    self.call_mail_agent("Reload")
                    self.hydrate_account_avatar(account)
                if complete:
                    complete(error)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(on_main)

        self.engine.sync(account, config, finished)

    def send_draft(self, draft: Draft, complete: Callable[[Exception | None], None]) -> None:
        if self.store is None or self.engine is None:
            complete(RuntimeError("Charlie has not finished starting"))
            return
        account = next((item for item in self.store.accounts() if item.id == draft.account_id), None)
        config = self.store.server_config(draft.account_id)
        if account is None or config is None:
            complete(RuntimeError("Add an account before sending mail"))
            return
        self.engine.send(
            account, config, draft,
            lambda error: GLib.idle_add(lambda: (complete(error), GLib.SOURCE_REMOVE)[1]),
        )

    @staticmethod
    def friendly_network_error(error: Exception) -> str:
        kind = network_failure_kind(error)
        if kind is NetworkFailureKind.AUTHENTICATION:
            return "Authentication failed. Check the email address and app password in Accounts."
        if kind is NetworkFailureKind.CONNECTION:
            return "Charlie could not reach the mail server. Check the server addresses and your connection."
        return "The mail server could not complete that request. Review the account settings and try again."

    def do_shutdown(self) -> None:
        if self.engine:
            self.engine.close()
        self.oauth_pool.shutdown(wait=False, cancel_futures=True)
        self.avatar_loader.close()
        if self.store:
            self.store.close()
        Adw.Application.do_shutdown(self)


def main(argv: list[str] | None = None) -> int:
    return CharlieApplication().run(argv or sys.argv)
