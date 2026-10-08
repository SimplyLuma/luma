# SPDX-License-Identifier: Apache-2.0
"""The Accounts dialog in Messages (ADR-022, ADR-023).

Lists where messages come from (this device, Luma Connect phones, network
accounts) and adds, reconnects and removes network accounts. Sign-in follows the
steps the account's helper asks for.
"""

from __future__ import annotations

import json
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .messages_accounts import AccountLogin, Accounts, NETWORKS, available_networks, network  # noqa: E402
from .qr_code import matrix as qr_matrix  # noqa: E402
from .messages_dialog import MessagesDialog
from luma_appkit import DestructiveDialog

METHOD_LABELS = {
    "qr": "Link with a QR code",
    "code": "Link with a code instead",
    "cookies": "Sign in with Google",
    "phone": "Sign in with a phone number",
}
QR_INSTRUCTIONS = {
    "whatsapp": "On your phone, open WhatsApp, go to Settings › Linked devices › Link a device, and scan this code.",
    "signal": "On your phone, open Signal, go to Settings › Linked devices › Link new device, and scan this code.",
    "telegram": "On your phone, open Telegram, go to Settings › Devices › Link Desktop Device, and scan this code.",
}
CODE_INSTRUCTIONS = {
    "whatsapp": "On your phone, open WhatsApp, go to Settings › Linked devices › Link a device › Link with phone number instead, and enter this code.",
}
FIELD_LABELS = {
    "phone": ("Your phone number", "Include the country code, like +1 555 010 0100."),
    "code": ("The code you received", "It was sent to your phone or to another device where you're signed in."),
    "password": ("Your two-step verification password", "This is the password you set for extra security."),
    "cookies": ("Paste sign-in cookies", "Copy the cookies for messages.google.com from a private browser window as JSON, or as a Cookie header."),
}


def qr_widget(data: str, label: str) -> Gtk.Widget:
    modules = qr_matrix(data)
    if modules is None:
        text = Gtk.Label(label="This device can't draw the code. Install libqrencode.", wrap=True)
        return text
    area = Gtk.DrawingArea(content_width=240, content_height=240, halign=Gtk.Align.CENTER,
                           accessible_role=Gtk.AccessibleRole.IMG)
    area.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def draw(_area, cr, width, height):
        cells = len(modules)
        scale = max(1, min(width, height) // cells)
        left, top = (width - cells * scale) // 2, (height - cells * scale) // 2
        # Scanners need dark modules on a light quiet zone in every theme.
        cr.set_source_rgb(1, 1, 1); cr.rectangle(left, top, cells * scale, cells * scale); cr.fill()
        cr.set_source_rgb(0, 0, 0)
        for y, row in enumerate(modules):
            for x, dark in enumerate(row):
                if dark:
                    cr.rectangle(left + x * scale, top + y * scale, scale, scale)
        cr.fill()
    area.set_draw_func(draw)
    return area


def parse_cookies(text: str) -> dict[str, str]:
    """Cookies pasted as a JSON object, a JSON list of {name, value}, or a Cookie header."""
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:]
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    if isinstance(data, list):
        return {str(item["name"]): str(item["value"]) for item in data if isinstance(item, dict) and "name" in item and "value" in item}
    found = {}
    for part in text.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            found[name] = value
    return found


def _label(text: str, *classes: str, **kwargs) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=kwargs.pop("xalign", 0), **kwargs)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for name in classes:
        label.add_css_class(name)
    return label


class AccountsDialog(MessagesDialog):
    """``window`` supplies services, the Accounts registry, secrets and add/remove callbacks."""

    def __init__(self, window) -> None:
        super().__init__(title="Accounts", content_width=480, content_height=640, follows_content_size=False)
        self.window = window
        self.login: AccountLogin | None = None
        self.view = Adw.NavigationView()
        self.set_child(self.view)
        self.view.connect("popped", self._popped)
        self.connect("closed", lambda *_: self._cancel_login())
        self.main = self._page(Gtk.Box(), "Accounts", "accounts")
        self.view.add(self.main)
        self.refresh()

    # Pages
    def _page(self, content: Gtk.Widget, title: str, tag: str) -> Adw.NavigationPage:
        toolbar = Adw.ToolbarView()
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=440, tightening_threshold=360, child=content)
        for setter in (clamp.set_margin_start, clamp.set_margin_end, clamp.set_margin_top, clamp.set_margin_bottom):
            setter(18)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
        return Adw.NavigationPage(child=toolbar, title=title, tag=tag)

    def _set_content(self, page: Adw.NavigationPage, content: Gtk.Widget) -> None:
        page.get_child().get_content().get_child().get_child().set_child(content)

    def refresh(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        services = self.window.services
        here = Adw.PreferencesGroup(title="On this device")
        native = next(service for service in services if service.native)
        here.add(self._row("Text messages on this device", native.capability.reason or "SMS and MMS through this device's own phone service.",
                           "phone-symbolic"))
        box.append(here)
        phones = [service for service in services if service.provider is not None and not service.native and not getattr(service, "account", None)]
        if phones:
            group = Adw.PreferencesGroup(title="Luma Connect phones",
                                         description="Phones paired in Luma Connect that allowed texts. Pair or remove them there.")
            for service in phones:
                row = self._row(service.label, self._service_state(service), "phone-symbolic")
                button = Gtk.Button(label="Open Luma Connect", valign=Gtk.Align.CENTER)
                button.connect("clicked", lambda *_: self._open_connect())
                row.add_suffix(button)
                group.add(row)
            box.append(group)
        accounts = Adw.PreferencesGroup(title="Accounts",
                                        description="Chats from other networks, signed in on this device. Nothing passes through a Luma server.")
        linked = [service for service in services if getattr(service, "account", None)]
        stopped = next((service.provider.outbound_disabled() for service in linked
                        if callable(getattr(service.provider, "outbound_disabled", None))), None)
        if stopped:
            # The kill switch: nothing is sent from any account until the person turns it back on.
            halted = Adw.PreferencesGroup(title="Sending is turned off")
            row = self._row("Messages stopped sending from your accounts",
                            "To keep anything from going out twice, nothing is sent until you turn sending back on. "
                            f"Reason: {stopped[:160]}", "dialog-warning-symbolic")
            resume = Gtk.Button(label="Turn Sending On", valign=Gtk.Align.CENTER)
            resume.add_css_class("suggested-action")
            resume.connect("clicked", lambda _b, provider=linked[0].provider: (provider.resume_outbound(), self.refresh()))
            row.add_suffix(resume)
            halted.add(row)
            box.append(halted)
        if not linked:
            accounts.add(self._row("No accounts yet", "Add Google Messages, WhatsApp, Signal or Telegram to see those chats here.", "mail-message-new-symbolic"))
        for service in linked:
            account = service.account
            subtitle = " · ".join(part for part in (account.handle, self._service_state(service)) if part)
            row = self._row(f"{network(account.network).name}" + (f" — {account.name}" if account.name else ""), subtitle, "system-users-symbolic")
            if service.provider.status.get("state") != "ready":
                again = Gtk.Button(label="Sign in again" if service.provider.status.get("network_state") == "needs_login" else "Reconnect",
                                   valign=Gtk.Align.CENTER)
                again.connect("clicked", lambda _b, s=service: self._reconnect(s))
                row.add_suffix(again)
            if network(account.network).automatic:
                # Luma comes with this device's Luma Connect sign-in; it has a
                # profile rather than a Remove button.
                profile = Gtk.Button(label="Profile", valign=Gtk.Align.CENTER)
                profile.connect("clicked", lambda *_: (self.close(), self.window._show_luma_profile()))
                row.add_suffix(profile)
                accounts.add(row)
                continue
            remove = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect("clicked", lambda _b, s=service: self._confirm_remove(s))
            row.add_suffix(remove)
            accounts.add(row)
        box.append(accounts)
        add = Gtk.Button(label="Add account", halign=Gtk.Align.CENTER)
        add.add_css_class("suggested-action"); add.add_css_class("pill")
        add.connect("clicked", lambda *_: self._show_networks())
        box.append(add)
        self._set_content(self.main, box)

    @staticmethod
    def _row(title: str, subtitle: str, icon: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle or ""))
        row.set_subtitle_lines(3)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        return row

    @staticmethod
    def _service_state(service) -> str:
        provider = service.provider
        if provider is None:
            return service.capability.reason
        state = provider.status.get("state")
        if state == "ready":
            return "Connected"
        return service.transport.inspect().reason or {"connecting": "Connecting…", "offline": "Offline"}.get(state, "Not connected")

    def _open_connect(self) -> None:
        from luma_appkit.application_directory import launch
        from luma_appkit import Toast
        launch("org.projectluma.Connect.desktop", callback=lambda opened, error:
               None if opened else Toast.show(self.window.host, error, kind="error"))

    def _show_networks(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        from .messages_app_client import sandboxed
        if sandboxed():
            ids = json.loads(self.window.agent.call('ApplicationInfo')[0])['networks']
            installed = [item for item in NETWORKS if item.id in ids]
        else:
            installed = available_networks(self.window.helper_directory)
        group = Adw.PreferencesGroup(title="Add an account")
        for item in NETWORKS:
            if item.automatic:
                continue  # added by Messages itself (Luma), never from here
            row = Adw.ActionRow(title=GLib.markup_escape_text(item.name), subtitle=GLib.markup_escape_text(
                item.description if item in installed else "Not installed on this device."))
            row.set_subtitle_lines(3)
            row.set_sensitive(item in installed)
            if item in installed:
                row.set_activatable(True)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", lambda _r, n=item: self._show_network(n))
            group.add(row)
        box.append(group)
        self.view.push(self._page(box, "Add an account", "networks"))

    def _show_network(self, item, account=None) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.append(_label(item.name, "title-2"))
        box.append(_label(item.description))
        if item.warning:
            warning = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            warning.add_css_class("card")
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic"); icon.set_margin_start(12); icon.set_valign(Gtk.Align.START); icon.set_margin_top(12)
            warning.append(icon)
            text = _label(item.warning); text.set_margin_top(10); text.set_margin_bottom(10); text.set_margin_end(12); text.set_hexpand(True)
            warning.append(text)
            box.append(warning)
        privacy = _label("Your sign-in stays in this device's keyring and your chats are stored only on this device.", "dim-label")
        box.append(privacy)
        for index, method in enumerate(item.methods):
            button = Gtk.Button(label=METHOD_LABELS.get(method, method), halign=Gtk.Align.CENTER)
            button.add_css_class("pill")
            if index == 0:
                button.add_css_class("suggested-action")
            button.connect("clicked", lambda _b, m=method, n=item: self._start_login(n, m, account))
            box.append(button)
        self.view.push(self._page(box, item.name, "network"))

    # Sign-in
    def _start_login(self, item, method: str, account=None) -> None:
        self._cancel_login()
        self.login_network, self.login_method, self.login_account = item, method, account
        self.login_page = self._page(Gtk.Box(), f"Add {item.name}", "login")
        self.view.push(self.login_page)
        self._login_step("starting", {})
        from .messages_app_client import sandboxed, ApplicationLogin
        if sandboxed():
            self.login = ApplicationLogin(self.window.agent, item.id, self._login_step, account)
        else:
            self.login = AccountLogin(item.id, self.window.accounts, secrets=self.window.account_secrets, dispatch=GLib.idle_add,
                                      on_step=self._login_step, helper_dir=self.window.helper_directory,
                                      account=account, **self.window.login_options)
        self.login.start(method)

    def _login_step(self, name: str, data: dict) -> None:
        if self.login_page is None:
            return
        item = self.login_network
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER)
        box.set_name(f"login-{name}")
        if name == "starting":
            box.append(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, width_request=32, height_request=32))
            box.append(_label(f"Starting {item.name}…", justify=Gtk.Justification.CENTER, xalign=0.5))
        elif name == "login.qr":
            box.append(qr_widget(str(data.get("data", "")), f"Code to link {item.name}"))
            box.append(_label(QR_INSTRUCTIONS.get(item.id, f"Scan this code with {item.name} on your phone."), justify=Gtk.Justification.CENTER, xalign=0.5))
            box.append(_label("The code refreshes by itself.", "dim-label", justify=Gtk.Justification.CENTER, xalign=0.5))
        elif name == "login.code":
            code = Gtk.Label(label=str(data.get("code", "")), selectable=True)
            code.add_css_class("title-1")
            if data.get("kind") == "emoji":
                code.add_css_class("messages-login-emoji")
                box.append(code)
                box.append(_label(f"On your phone, open {item.name} and tap this emoji to link Luma.", justify=Gtk.Justification.CENTER, xalign=0.5))
            else:
                box.append(code)
                box.append(_label(CODE_INSTRUCTIONS.get(item.id, f"Enter this code in {item.name} on your phone."), justify=Gtk.Justification.CENTER, xalign=0.5))
        elif name == "browser.waiting":
            box.append(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, width_request=32, height_request=32))
            box.append(_label("Sign in with your Google account in the Firefox window that just opened. It closes by itself once you're signed in.",
                              justify=Gtk.Justification.CENTER, xalign=0.5))
            box.append(_label("That Firefox profile is temporary and is deleted afterwards.", "dim-label", justify=Gtk.Justification.CENTER, xalign=0.5))
        elif name == "login.needs":
            field_name = str(data.get("field", ""))
            title, hint = FIELD_LABELS.get(field_name, (field_name.title(), ""))
            box.set_valign(Gtk.Align.START)
            box.append(_label(title, "title-4"))
            if data.get("hint"):
                box.append(_label(str(data["hint"])))
            box.append(_label(hint, "dim-label"))
            if field_name == "cookies":
                entry_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.CHAR, monospace=True, height_request=140)
                entry_view.add_css_class("card")
                entry_view.update_property([Gtk.AccessibleProperty.LABEL], [title])
                box.append(entry_view)
                value = lambda: json.dumps(parse_cookies(entry_view.get_buffer().get_text(*entry_view.get_buffer().get_bounds(), False)))
            else:
                entry = Gtk.PasswordEntry(show_peek_icon=True) if field_name == "password" else Gtk.Entry()
                entry.update_property([Gtk.AccessibleProperty.LABEL], [title])
                if field_name == "phone":
                    entry.set_input_purpose(Gtk.InputPurpose.PHONE)
                elif field_name == "code":
                    entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
                box.append(entry)
                value = entry.get_text
            button = Gtk.Button(label="Continue", halign=Gtk.Align.END)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda *_: (self.login.submit(field_name, value()), self._login_step("starting", {})))
            box.append(button)
        elif name == "login.error":
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic"); icon.set_pixel_size(48)
            box.append(icon)
            box.append(_label(str(data.get("message") or f"{item.name} couldn't sign in."), justify=Gtk.Justification.CENTER, xalign=0.5))
            again = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
            again.add_css_class("pill")
            again.connect("clicked", lambda *_: self._start_login_again())
            box.append(again)
            self.login = None
        elif name == "login.done":
            account = data["account"]
            icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic"); icon.set_pixel_size(48)
            box.append(icon)
            who = account.name or account.handle
            box.append(_label(f"{item.name} is added" + (f" for {who}" if who else "") + ". Your chats appear in Messages as they sync.",
                              justify=Gtk.Justification.CENTER, xalign=0.5))
            done = Gtk.Button(label="Done", halign=Gtk.Align.CENTER)
            done.add_css_class("suggested-action"); done.add_css_class("pill")
            done.connect("clicked", lambda *_: self.view.pop_to_tag("accounts"))
            box.append(done)
            self.login = None
            self.window.account_added(account)
            self.refresh()
        else:
            return
        self._set_content(self.login_page, box)

    def _start_login_again(self) -> None:
        self.view.pop()
        self._start_login(self.login_network, self.login_method, self.login_account)

    def _cancel_login(self) -> None:
        if self.login is not None:
            self.login.cancel()
            self.login = None

    def _popped(self, _view, page) -> None:
        if page.get_tag() == "login":
            self._cancel_login()
            self.login_page = None
        if self.view.get_visible_page() is self.main:
            self.refresh()

    login_page = None

    # Existing accounts
    def _reconnect(self, service) -> None:
        if service.provider.status.get("network_state") == "needs_login":
            self._show_network(network(service.account.network), service.account)
            return
        service.provider.retry()
        GLib.timeout_add(1500, lambda: (self.refresh(), False)[1])

    def _confirm_remove(self, service) -> None:
        account = service.account
        name = network(account.network).name
        self.close()
        def removed(_checked):
            self.window.remove_account_service(service)
            self.window._show_accounts()
        DestructiveDialog.ask(self.window, title=f"Remove {name}?",
                              body=f"Luma signs out of {name} on this device and deletes its chats from this device. They stay in {name} on your other devices.",
                              action='Remove',
                              on_confirm=removed, on_cancel=self.window._show_accounts)
