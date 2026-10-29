# SPDX-License-Identifier: Apache-2.0
"""Native account/consent UI. Only the daemon handles OAuth tokens."""
import json

import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

USAGE_URL = "https://chatgpt.com/settings/usage"


def _message(error):
    if isinstance(error, GLib.Error):
        Gio.DBusError.strip_remote_error(error)
        return error.message
    return "Ari could not update ChatGPT. Try again."


class ChatGPTDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title="ChatGPT", content_width=440, content_height=560)
        self.window, self.attempt, self.timer = window, "", None
        self.closed, self.working, self.polling = False, False, False
        self.message = ""
        self.data = window.chatgpt_data
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                               margin_start=20, margin_end=20, margin_top=12, margin_bottom=20)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.content)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.append(Adw.HeaderBar())
        layout.append(scroll)
        self.set_child(layout)
        self.connect("closed", self._closed)
        self.render()
        self.refresh(True)

    def words(self, value):
        label = Gtk.Label(label=value, wrap=True, xalign=0)
        label.set_max_width_chars(48)
        self.content.append(label)
        return label

    def button(self, title, callback, name, *, primary=False):
        button = Gtk.Button(label=title, sensitive=not self.working)
        button.set_name("ari-chatgpt-" + name)
        button.set_size_request(-1, 44)
        label = button.get_child()
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(36)
        if primary:
            button.add_css_class("suggested-action")
        if title == "Continue with ChatGPT":
            button.remove_css_class("suggested-action")
            content = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
            content.append(Gtk.Image.new_from_icon_name("chatgpt-symbolic"))
            button.set_child(None)
            content.append(label)
            button.set_child(content)
            provider = Gtk.CssProvider()
            provider.load_from_string("button { background: #000; color: #fff; }")
            button.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            button.set_tooltip_text("Continue in your browser on OpenAI's sign-in page")
        button.connect("clicked", lambda *_: callback())
        self.content.append(button)
        return button

    def render(self):
        while child := self.content.get_first_child():
            self.content.remove(child)
        self.words("Use your ChatGPT plan").add_css_class("title-3")
        self.words("Ari sends your conversation and tool results to OpenAI. Eligible requests use your plan's shared "
                   "allowance. Manage limits and credit spending in ChatGPT settings.")
        self.content.append(Gtk.LinkButton(uri=USAGE_URL, label="Manage usage", halign=Gtk.Align.START))
        if self.message or self.data.get("error"):
            self.words(self.message or self.data["error"])
        if self.working:
            self.content.append(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER))
        if self.attempt:
            self.words("Finish sign-in in your browser, then return to Ari.")
            button = self.button("Cancel sign-in", self.cancel, "cancel")
            button.set_sensitive(True)
            return
        account = next((item for item in self.data.get("accounts", []) if item["id"] == self.data.get("active")), None)
        if account:
            self.words("Selected account: " + account["label"])
            if account.get("signed_in"):
                if account.get("plan_enabled"):
                    models = self.data.get("models", [])
                    if models:
                        self.words("Model")
                        selected = next((i for i, item in enumerate(models) if item["id"] == self.data.get("model")), 0)
                        picker = Gtk.DropDown.new_from_strings([item["name"] for item in models])
                        picker.set_selected(selected)
                        picker.set_sensitive(not self.working)
                        picker.set_name("ari-chatgpt-model")
                        self.content.append(picker)
                        self.button("Use ChatGPT plan", lambda: self.use(models[picker.get_selected()]["id"]), "use", primary=True)
                    else:
                        self.words("No available models have been loaded. Refresh models to check this account's access.")
                    self.button("Refresh models", lambda: self.refresh(True), "refresh")
                else:
                    self.words("You are signed in. Authorize plan usage to let ChatGPT answer in Ari.")
                    self.button("Authorize ChatGPT plan usage", lambda: self.start(account["id"]), "authorize", primary=True)
                self.button("Sign out of this account", lambda: self.change("SignOutChatGPT", account["id"]), "signout")
            else:
                self.button("Continue with ChatGPT", lambda: self.start(account["id"]), "signin", primary=True)
        else:
            self.button("Continue with ChatGPT", self.start, "signin", primary=True)
        for other in self.data.get("accounts", []):
            if not account or other["id"] != account["id"]:
                self.button(other["label"], lambda key=other["id"]: self.change("SelectChatGPTAccount", key),
                            "account-" + other["id"])
        if self.data.get("accounts"):
            self.button("Add another ChatGPT account", self.start, "add")

    def refresh(self, online=False):
        if self.closed:
            return
        self.working = True
        self.render()
        def ready(value):
            if self.closed:
                return
            self.working = False
            if isinstance(value, Exception):
                self.message = "Ari could not check ChatGPT. Try again."
            else:
                self.data = json.loads(value[0])
                self.window.chatgpt_loaded(self.data)
            self.render()
        self.window.daemon.call("ChatGPT", "(b)", (online,), ready)

    def start(self, account=""):
        if self.working or self.attempt:
            return
        self.working, self.message = True, "Opening ChatGPT sign-in…"
        self.render()
        def started(value):
            if isinstance(value, Exception):
                if not self.closed:
                    self.working, self.message = False, _message(value)
                    self.render()
                return
            result = json.loads(value[0])
            if self.closed:
                self.window.daemon.call("CancelChatGPTSignIn", "(s)", (result["id"],))
                return
            self.attempt = result["id"]
            self.working, self.message = False, ""
            try:
                # The daemon supplies only an authorization URL, never tokens.
                Gio.AppInfo.launch_default_for_uri(result["url"], self.window.get_display().get_app_launch_context())
            except GLib.Error:
                self.cancel()
                self.message = "The browser could not be opened. Choose a default browser and try again."
            if self.attempt:
                self.timer = GLib.timeout_add(500, self.poll)
            self.render()
        self.window.daemon.call("StartChatGPTSignIn", "(s)", (account,), started)

    def poll(self):
        if self.closed or not self.attempt:
            self.timer = None
            return GLib.SOURCE_REMOVE
        if self.polling:
            return GLib.SOURCE_CONTINUE
        self.polling = True
        attempt = self.attempt
        def ready(value):
            self.polling = False
            if self.closed or attempt != self.attempt:
                return
            if isinstance(value, Exception):
                self.message = "Ari could not check sign-in. Cancel and try again."
                self.render()
                return
            self.data = json.loads(value[0])
            pending = self.data.get("pending") or {}
            if pending.get("id") != attempt:
                self.attempt, self.message = "", "The sign-in session ended. Try again."
            elif pending["status"] not in ("waiting", "checking"):
                self.attempt, self.message = "", pending["message"]
                self.window.chatgpt_loaded(self.data)
                if pending["status"] == "complete":
                    self.refresh(True)
                    return
            self.render()
        self.window.daemon.call("ChatGPT", "(b)", (False,), ready)
        return GLib.SOURCE_CONTINUE

    def cancel(self):
        if self.attempt:
            self.window.daemon.call("CancelChatGPTSignIn", "(s)", (self.attempt,))
            self.attempt = ""
        self.message = "Sign-in cancelled."
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        if not self.closed:
            self.render()

    def change(self, method, account):
        self.working, self.message = True, ""
        self.render()
        def changed(value):
            if self.closed:
                return
            self.working = False
            self.message = _message(value) if isinstance(value, Exception) else value[1]
            self.refresh(method == "SelectChatGPTAccount")
        self.window.daemon.call(method, "(s)", (account,), changed)

    def use(self, model):
        self.working, self.message = True, ""
        self.render()
        def selected(value):
            if self.closed:
                return
            if isinstance(value, Exception) or not value[0]:
                self.working, self.message = False, _message(value) if isinstance(value, Exception) else value[1]
                self.render()
                return
            def enabled(value):
                if self.closed:
                    return
                self.working = False
                if isinstance(value, Exception) or not value[0]:
                    self.message = _message(value) if isinstance(value, Exception) else value[1]
                    self.render()
                    return
                self.window.route_generation += 1
                self.window.state["model"] = "chatgpt:" + model
                self.data.update(model=model, brain="chatgpt")
                self.window.chatgpt_loaded(self.data)
                self.window.notify("You're using your ChatGPT plan. Manage usage in ChatGPT settings.")
                self.close()
            self.window.daemon.call("SetBrain", "(s)", ("chatgpt",), enabled)
        self.window.daemon.call("SetChatGPTModel", "(s)", (model,), selected)

    def _closed(self, *_args):
        self.closed = True
        self.cancel()
        self.window.chatgpt_dialog = None
