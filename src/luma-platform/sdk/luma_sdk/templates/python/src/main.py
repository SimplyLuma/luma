#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("LumaUI", "1")
gi.require_version("LumaSemantics", "1")
from gi.repository import Adw, Gtk, LumaSemantics, LumaUI


class Window(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="@APP_NAME@")
        LumaUI.init()
        self.set_default_size(920, 680)
        self.set_size_request(360, 294)

        context = LumaUI.Context.new_from_environment()
        self.set_decorated(context.get_decorated())

        action_bar = LumaUI.ActionBar.new()
        action_bar.set_title("@APP_NAME@")
        add = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add.set_action_name("app.create")
        add.update_property([Gtk.AccessibleProperty.LABEL], ["Create item"])
        action_bar.add_trailing(add)

        sidebar = Gtk.Label(label="Navigation")
        content = Gtk.Label(label="Start building here")
        scaffold = LumaUI.AdaptiveScaffold.new()
        scaffold.set_sidebar(sidebar, "Navigation")
        scaffold.set_content(content, "@APP_NAME@")

        view = Adw.ToolbarView()
        view.add_top_bar(action_bar)
        view.set_content(scaffold)
        self.set_content(view)

        self.semantic_root = LumaSemantics.SemanticObject.new(
            "application-root", "application", "@APP_NAME@"
        )
        self.semantic_root.add_action(
            LumaSemantics.Action.new("application.create", "Create item")
        )


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="@APP_ID@")
        action = self.create_action("create", self.on_create)
        self.add_action(action)

    @staticmethod
    def create_action(name, callback):
        from gi.repository import Gio
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        return action

    @staticmethod
    def on_create(_action, _parameter) -> None:
        print("Create item")

    def do_activate(self) -> None:
        (self.props.active_window or Window(self)).present()


raise SystemExit(Application().run([]))
