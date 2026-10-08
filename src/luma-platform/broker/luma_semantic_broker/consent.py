# SPDX-License-Identifier: Apache-2.0
"""Broker-owned consent UI; callers supply text, never widgets or markup."""

from __future__ import annotations

import argparse

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, Gtk  # noqa: E402


SCOPE_LABELS = {
    "observe-public": "View public app information",
    "observe-private": "View private app information",
    "invoke-low-risk": "Use ordinary app actions",
    "invoke-consequential": "Request consequential actions",
    "invoke-destructive": "Request destructive actions",
    "invoke-security-sensitive": "Request security-sensitive actions",
}


class ConsentApplication(Adw.Application):
    def __init__(self, arguments: argparse.Namespace) -> None:
        super().__init__(
            application_id="org.projectluma.SemanticConsent",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.arguments = arguments
        self.allowed = False

    def do_activate(self) -> None:
        window = Adw.ApplicationWindow(application=self, title="App Access")
        window.set_default_size(440, 360)
        window.set_resizable(False)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        toolbar.add_top_bar(header)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(24)
        content.set_margin_bottom(20)
        content.set_margin_start(24)
        content.set_margin_end(24)

        if self.arguments.mode == "access":
            heading = f"Allow {self.arguments.client} to use {self.arguments.target}?"
            body = "Luma will share only the information and actions listed below."
            details = [
                SCOPE_LABELS.get(item, item)
                for item in self.arguments.scopes.split(",")
                if item
            ]
            if self.arguments.persistent:
                details.append("Remember this decision until it expires")
            allow_label = "Allow"
            destructive = False
        else:
            heading = f"Allow {self.arguments.action}?"
            body = (
                f"{self.arguments.client} requested this action in "
                f"{self.arguments.target}."
            )
            details = [f"Risk: {self.arguments.risk.replace('-', ' ')}"]
            allow_label = "Continue"
            destructive = self.arguments.risk in {
                "destructive",
                "security-sensitive",
            }

        title = Gtk.Label(label=heading, wrap=True, xalign=0)
        title.add_css_class("title-2")
        description = Gtk.Label(label=body, wrap=True, xalign=0)
        description.add_css_class("dim-label")
        content.append(title)
        content.append(description)
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        details_box.add_css_class("card")
        for detail in details:
            label = Gtk.Label(label=detail, wrap=True, xalign=0)
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            label.set_margin_start(12)
            label.set_margin_end(12)
            details_box.append(label)
        content.append(details_box)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        deny = Gtk.Button(label="Don’t Allow")
        deny.connect("clicked", self._finish, False, window)
        allow = Gtk.Button(label=allow_label)
        allow.add_css_class("destructive-action" if destructive else "suggested-action")
        allow.connect("clicked", self._finish, True, window)
        actions.append(deny)
        actions.append(allow)
        content.append(actions)
        toolbar.set_content(content)
        window.set_content(toolbar)
        window.connect("close-request", self._closed)
        window.present()

    def _finish(self, _button, allowed: bool, window: Adw.ApplicationWindow) -> None:
        self.allowed = allowed
        window.destroy()
        self.quit()

    def _closed(self, _window) -> bool:
        self.allowed = False
        self.quit()
        return False


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="luma-semantic-consent")
    subparsers = value.add_subparsers(dest="mode", required=True)
    access = subparsers.add_parser("access")
    access.add_argument("--client", required=True)
    access.add_argument("--target", required=True)
    access.add_argument("--scopes", required=True)
    access.add_argument("--persistent", action="store_true")
    confirm = subparsers.add_parser("confirm")
    confirm.add_argument("--client", required=True)
    confirm.add_argument("--target", required=True)
    confirm.add_argument("--action", required=True)
    confirm.add_argument("--risk", required=True)
    return value


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    application = ConsentApplication(options)
    status = application.run([])
    if status != 0:
        return 2
    return 0 if application.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
