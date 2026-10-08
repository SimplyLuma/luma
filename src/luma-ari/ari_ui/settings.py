# SPDX-License-Identifier: Apache-2.0
"""Ari's settings: Models, Permissions and Activity (brief §6.3, §7.2 rule 11).

Every value here comes from ari-daemon and every change goes back through it,
so the window, the popover and the `ari` CLI never disagree.
"""
from __future__ import annotations

import json
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, GObject, Gtk

from luma_appkit import SectionLabel

PAGES = {  # id: title, sidebar glyph, what the page is for
    "models": ("Models", "drive-harddisk-symbolic", "Where Ari thinks, and with which model"),
    "permissions": ("Permissions", "channel-secure-symbolic", "What Ari may do for you"),
    "activity": ("Activity", "document-open-recent-symbolic", "Everything Ari did, newest first"),
}
FILTERS = (("all", "All"), ("changes", "Changes"), ("refused", "Refused"))
TOOL_GLYPHS = {
    "now": "preferences-system-time-symbolic", "calculate": "accessories-calculator-symbolic",
    "convert": "accessories-calculator-symbolic", "weather": "weather-few-clouds-symbolic",
    "web_search": "system-search-symbolic", "fetch_page": "web-browser-symbolic", "define": "accessories-dictionary-symbolic",
    "find_app": "view-app-grid-symbolic", "list_display_modes": "video-display-symbolic",
    "install_model": "folder-download-symbolic", "remove_model": "user-trash-symbolic",
    "set_permission": "channel-secure-symbolic",
}
TOOL_NAMES = {
    "now": "Checked the time", "calculate": "Worked something out", "convert": "Converted units",
    "weather": "Checked the weather", "web_search": "Searched the web", "fetch_page": "Read a page",
    "define": "Looked up a word", "find_app": "Looked for an app", "list_display_modes": "Checked displays",
    "install_model": "Downloaded a model", "remove_model": "Removed a model", "set_permission": "Permission changed",
}


def _when(stamp: str) -> str:
    try:
        moment = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return ""
    if time.localtime(moment)[:3] == time.localtime()[:3]:
        return time.strftime("%-I:%M %p", time.localtime(moment))
    return time.strftime("%-d %b, %-I:%M %p", time.localtime(moment))


def _boxed() -> Gtk.ListBox:
    listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listing.add_css_class("boxed-list")
    return listing


def _caption(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    label.add_css_class("dim-label")
    label.add_css_class("caption")
    return label


class SettingsView(Gtk.ScrolledWindow):
    def __init__(self, window) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.window = window
        self.page = ""
        self.filter = "all"
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content.add_css_class("ari-settings")
        self.set_child(Adw.Clamp(maximum_size=680, child=self.content))
        self.progress: dict[str, Gtk.ProgressBar] = {}

    def show(self, page: str) -> None:
        self.page = page
        {"models": self._models, "permissions": self._permissions, "activity": self._activity}[page]()

    def refresh(self) -> None:
        if self.page:
            self.show(self.page)

    def _clear(self) -> None:
        while child := self.content.get_first_child():
            self.content.remove(child)

    def _section(self, title: str) -> None:
        label = SectionLabel(title, variant="content")
        if self.content.get_first_child() is not None:
            label.add_css_class("ari-settings-gap")
        self.content.append(label)

    # ── Models ───────────────────────────────────────────────────────────

    def _models(self, refresh_cloud: bool = False) -> None:
        def cloud_done(value, data) -> None:
            if self.page != "models":
                return
            cloud = json.loads(value[0]) if not isinstance(value, GLib.Error) else {
                "brain": data.get("brain", "local"), "error": "Ari couldn't check OpenRouter.", "key_set": False,
                "recommended": [], "models": [], "monthly_limit": 10, "spent": 0, "private": True, "model": ""}
            self._render_models(data, cloud)

        def done(value) -> None:
            if isinstance(value, GLib.Error) or self.page != "models":
                return
            data = json.loads(value[0])
            self.window.models = data
            self.window.daemon.call("Cloud", "(b)", (refresh_cloud,), lambda v: cloud_done(v, data), timeout=60000)
        self.window.daemon.call("Models", None, (), done)

    def _render_models(self, data: dict, cloud: dict) -> None:
        self._clear()
        self._brain(data, cloud)
        if cloud["brain"] == "openrouter":
            self._openrouter(cloud)
        self._section("On this computer")
        installed = [m for m in data["models"] if m["installed"]]
        available = [m for m in data["models"] if not m["installed"]]
        if installed:
            listing = _boxed()
            for model in installed:
                listing.append(self._model_row(model, data))
            self.content.append(listing)
        else:
            self.content.append(_caption("No model yet. Download one below and Ari can answer."))
        if available:
            self._section("Available")
            listing = _boxed()
            for model in available:
                listing.append(self._model_row(model, data))
            self.content.append(listing)
        self.content.append(_caption(
            f"This machine: {data['summary']}. Every model here runs on it, and nothing you ask leaves it. "
            "The switch takes effect with your next message."))

    # ── Where Ari thinks ─────────────────────────────────────────────────

    def _brain(self, data: dict, cloud: dict) -> None:
        self._section("Where Ari thinks")
        listing = _boxed()
        first = None
        for brain, title, subtitle in (
                ("local", "On this computer", "Private. Nothing you ask leaves this machine."),
                ("openrouter", "OpenRouter", "Larger models in the cloud, paid per use from your OpenRouter credit.")):
            check = Gtk.CheckButton(active=cloud["brain"] == brain, valign=Gtk.Align.CENTER)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.connect("toggled", lambda c, b=brain: c.get_active() and b != cloud["brain"] and self._choose_brain(b, c))
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            listing.append(row)
        self.content.append(listing)

    def _choose_brain(self, brain: str, check: Gtk.CheckButton) -> None:
        def apply() -> None:
            self.window.daemon.call("SetBrain", "(s)", (brain,), lambda _v: (self.refresh(), self.window.refresh_models()))
        if brain == "local":
            apply()
            return
        dialog = Adw.AlertDialog(
            heading="Think through OpenRouter?",
            body="Your questions, the conversation and what Ari's tools find are sent to OpenRouter and to the "
                 "company that runs the model you choose. You pay them per use from your OpenRouter credit.\n\n"
                 "Ari's rules don't change: changes still show and can be undone, and web pages still can't "
                 "make changes.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("use", "Use OpenRouter")
        dialog.set_response_appearance("use", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.connect("response", lambda _d, response: apply() if response == "use" else self.refresh())
        dialog.present(self.window)

    def _openrouter(self, cloud: dict) -> None:
        self._section("OpenRouter account")
        listing = _boxed()
        if not cloud["key_set"]:
            entry = Adw.PasswordEntryRow(title="API key", show_apply_button=True)
            entry.connect("apply", lambda row: self._set_key(row))
            listing.append(entry)
            self.content.append(listing)
            link = Gtk.LinkButton(uri=cloud.get("keys_page", "https://openrouter.ai/settings/keys"),
                                  label="Create a key on OpenRouter", halign=Gtk.Align.START)
            self.content.append(link)
            self.content.append(_caption(
                "Give the key a credit limit there. It holds no matter what happens on this computer. "
                "The key is kept in your keyring, not in a file."))
            return
        key = cloud.get("key") or {}
        if key.get("limit") is None:
            subtitle = "No credit limit on this key. Set one on OpenRouter so a mistake can't cost more than you choose."
        else:
            subtitle = f"${(key.get('limit_remaining') or 0):.2f} of ${key['limit']:.2f} left on this key"
        row = Adw.ActionRow(title="Connected", subtitle=subtitle)
        row.set_subtitle_lines(3)
        tile = Gtk.Image(icon_name="network-server-symbolic", pixel_size=16, valign=Gtk.Align.CENTER)
        tile.add_css_class("ari-step-tile")
        tile.add_css_class("amber" if key.get("limit") is None else "green")
        row.add_prefix(tile)
        forget = Gtk.Button(label="Remove Key", valign=Gtk.Align.CENTER)
        forget.add_css_class("ari-pill")
        forget.connect("clicked", lambda *_: self.window.daemon.call("ForgetCloudKey", None, (),
                                                                       lambda _v: self.refresh()))
        row.add_suffix(forget)
        listing.append(row)
        self.content.append(listing)
        if cloud.get("error"):
            self.content.append(_caption(cloud["error"]))

        by_id = {m["id"]: m for m in cloud.get("models", [])}
        current = by_id.get(cloud.get("model", ""))
        self._section("Recommended models")
        listing = _boxed()
        for model in cloud.get("recommended", []):
            listing.append(self._cloud_row(model, cloud, title=f"{model['title']} · {model['name']}",
                                           why=model["why"]))
        self.content.append(listing)
        self.content.append(_caption(
            "Prices come from OpenRouter as you choose. A question here means one answer with Ari's tools; "
            "a long task such as building an app takes many."))

        self._section("Any model")
        listing = _boxed()
        if current and current["id"] not in {m["id"] for m in cloud.get("recommended", [])}:
            listing.append(self._cloud_row(current, cloud, title=current["name"], why=current["id"]))
        expander = Adw.ExpanderRow(title="Choose another model",
                                   subtitle=f"All {len(cloud.get('models', []))} OpenRouter models that can use Ari's tools")
        search = Gtk.SearchEntry(placeholder_text="Search by name or ID", margin_top=8, margin_bottom=8,
                                 margin_start=12, margin_end=12)
        results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)

        def fill(*_args) -> None:
            while child := results.get_first_child():
                results.remove(child)
            words = search.get_text().lower().split()
            shown = [m for m in cloud.get("models", []) if all(w in (m["name"] + " " + m["id"]).lower() for w in words)]
            for model in shown[:40]:
                results.append(self._cloud_row(model, cloud, title=model["name"], why=model["id"]))
            if len(shown) > 40:
                results.append(Adw.ActionRow(title=f"{len(shown) - 40} more", subtitle="Narrow the search to see them"))
        search.connect("search-changed", fill)
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.append(search)
        holder.append(results)
        expander.add_row(holder)
        expander.connect("notify::expanded", lambda row, _p: row.get_expanded() and not results.get_first_child() and fill())
        listing.append(expander)
        manual = Adw.EntryRow(title="Model ID, such as provider/model-name", show_apply_button=True)
        manual.connect("apply", lambda row: self._use_cloud_model(row.get_text().strip(), by_id.get(row.get_text().strip())))
        listing.append(manual)
        self.content.append(listing)

        self._section("Spending")
        listing = _boxed()
        limit = Adw.SpinRow.new_with_range(0, 1000, 1)
        limit.set_title("Monthly limit")
        limit.set_subtitle(f"${cloud.get('spent', 0):.2f} spent this month. At the limit Ari stops using OpenRouter "
                           "until next month, or until you raise it.")
        limit.set_digits(0)
        limit.set_value(float(cloud.get("monthly_limit", 10)))
        limit.connect("notify::value", lambda row, _p: self.window.daemon.call(
            "SetCloudOptions", "(s)", (json.dumps({"monthly_limit": row.get_value()}),)))
        listing.append(limit)
        private = Adw.SwitchRow(title="Skip providers that train on your data",
                                subtitle="Some cheaper providers keep prompts to train models. Turning this off allows them.",
                                active=bool(cloud.get("private", True)))
        private.connect("notify::active", lambda row, _p: self.window.daemon.call(
            "SetCloudOptions", "(s)", (json.dumps({"private": row.get_active()}),)))
        listing.append(private)
        self.content.append(listing)

    def _cloud_row(self, model: dict, cloud: dict, *, title: str, why: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=f"{why}\n{model['cost_label']} · "
                                                  f"${model['input_per_million']:g} in, ${model['output_per_million']:g} "
                                                  "out per million tokens", use_markup=False)
        row.set_subtitle_lines(3)
        actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        if model["cost_class"] != "low":
            badge = Gtk.Label(label="Expensive" if model["cost_class"] == "high" else "Moderate")
            badge.add_css_class("ari-badge")
            badge.add_css_class(model["cost_class"])
            actions.append(badge)
        if model["id"] == cloud.get("model"):
            badge = Gtk.Label(label="In use")
            badge.add_css_class("ari-badge")
            badge.add_css_class("private")
            actions.append(badge)
        else:
            use = Gtk.Button(label="Use")
            use.add_css_class("ari-pill")
            use.connect("clicked", lambda *_: self._use_cloud_model(model["id"], model))
            actions.append(use)
        row.add_suffix(actions)
        return row

    def _set_key(self, row: Adw.PasswordEntryRow) -> None:
        key = row.get_text()
        row.set_sensitive(False)

        def done(value) -> None:
            row.set_sensitive(True)
            if isinstance(value, GLib.Error):
                self._notice("Ari couldn't save the key.", value.message)
                return
            ok, message = value
            if not ok:
                self._notice("That key didn't work", message)
                return
            if message != "Connected.":
                self._notice("Connected", message)
            self.refresh()
        self.window.daemon.call("SetCloudKey", "(s)", (key,), done, timeout=60000)

    def _use_cloud_model(self, model_id: str, model: dict | None, accept_cost: bool = False) -> None:
        if not model_id:
            return

        def done(value) -> None:
            if isinstance(value, GLib.Error):
                self._notice("Ari couldn't change the model", value.message)
                return
            ok, message = value
            if ok:
                self.refresh()
                self.window.refresh_models()
            elif message == "confirm-cost":
                self._confirm_cost(model_id, model)
            else:
                self._notice("That model can't be used", message)
        self.window.daemon.call("SetCloudModel", "(sb)", (model_id, accept_cost), done, timeout=60000)

    def _confirm_cost(self, model_id: str, model: dict | None) -> None:
        name = model["name"] if model else model_id
        price = (f"{model['cost_label']} (${model['input_per_million']:g} per million tokens in, "
                 f"${model['output_per_million']:g} out). ") if model else ""
        dialog = Adw.AlertDialog(
            heading=f"{name} can get expensive quickly",
            body=f"{price}Building an app or working through a long task can take hundreds of steps, and Ari "
                 "can't know in advance how many a request will need.\n\n"
                 "Your monthly limit here and your key's credit limit on OpenRouter are what stop the spending. "
                 "Luma isn't responsible for charges from OpenRouter. Continue only if you understand the pricing.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("use", "I Understand, Use It")
        dialog.set_response_appearance("use", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "use" and self._use_cloud_model(model_id, model, True))
        dialog.present(self.window)

    def _notice(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self.window)

    def _model_row(self, model: dict, data: dict) -> Adw.ActionRow:
        facts = [model["quantisation"], f"{model['size'] / 1e9:.1f} GB", model["licence"]]
        if model.get("suite"):
            facts.append(f"passed {model['suite']['passed']} of {model['suite']['total']} checks")
        if model["id"] == data["recommended"] and not model["installed"]:
            facts.append("recommended here")
        row = Adw.ActionRow(title=model["name"], subtitle=" · ".join(facts))
        row.set_subtitle_lines(2)
        tile = Gtk.Image(icon_name="drive-harddisk-symbolic", pixel_size=16, valign=Gtk.Align.CENTER)
        tile.add_css_class("ari-step-tile")
        tile.add_css_class("green" if model["installed"] else "slate")
        row.add_prefix(tile)
        actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        if model["installed"] and model["id"] == data["active"]:
            # While Ari thinks through OpenRouter this is the model she comes back to.
            badge = Gtk.Label(label="In use" if data.get("brain", "local") == "local" else "Chosen here")
            badge.add_css_class("ari-badge")
            badge.add_css_class("private")
            actions.append(badge)
        elif model["installed"]:
            use = Gtk.Button(label="Use")
            use.add_css_class("ari-pill")
            use.connect("clicked", lambda *_: self.window.daemon.call(
                "SetActiveModel", "(s)", (model["id"],), lambda _v: (self.refresh(), self.window.refresh_models())))
            remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=f"Remove {model['name']}")
            remove.add_css_class("flat")
            remove.connect("clicked", lambda *_: self._confirm_remove(model))
            actions.append(use)
            actions.append(remove)
        elif model.get("downloading") or model["id"] in self.progress:
            bar = self.progress.get(model["id"]) or Gtk.ProgressBar(show_text=True, text="Starting")
            bar.set_size_request(140, -1)
            if bar.get_parent() is not None:
                bar.get_parent().remove(bar)
            self.progress[model["id"]] = bar
            actions.append(bar)
        else:
            get = Gtk.Button(label="Download")
            get.add_css_class("ari-pill")
            get.connect("clicked", lambda *_: self._download(model))
            actions.append(get)
        row.add_suffix(actions)
        return row

    def _download(self, model: dict) -> None:
        def started(value) -> None:
            if isinstance(value, GLib.Error):
                self.progress.pop(model["id"], None)
                self.refresh()
                return
            self.window.settings_downloads[value[0]] = model["id"]
        self.progress[model["id"]] = Gtk.ProgressBar(show_text=True, text="Starting")
        self.refresh()
        self.window.daemon.call("InstallModel", "(s)", (model["id"],), started)

    def download_event(self, model_id: str, event: dict) -> None:
        bar = self.progress.get(model_id)
        if event["type"] == "download" and bar is not None:
            bar.set_fraction(event["done"] / event["total"])
            bar.set_text(f"{event['done'] / 1e9:.1f} of {event['total'] / 1e9:.1f} GB")
        elif event["type"] in ("installed", "error"):
            self.progress.pop(model_id, None)
            self.refresh()
            self.window.refresh_models()

    def _confirm_remove(self, model: dict) -> None:
        dialog = Adw.AlertDialog(heading=f"Remove {model['name']}?",
                                 body=f"This frees {model['size'] / 1e9:.1f} GB. You can download it again later.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", lambda _d, response: response == "remove" and self.window.daemon.call(
            "RemoveModel", "(s)", (model["id"],), lambda _v: (self.refresh(), self.window.refresh_models())))
        dialog.present(self.window)

    # ── Permissions ──────────────────────────────────────────────────────

    def _permissions(self) -> None:
        def done(value) -> None:
            if isinstance(value, GLib.Error) or self.page != "permissions":
                return
            data = json.loads(value[0])
            self._clear()
            if data.get("schema"):
                self._switches(data)
            self._section("Ari may")
            listing = _boxed()
            later = []
            for tier in data["tiers"]:
                if not tier["available"]:
                    later.append(tier)
                    continue
                row = Adw.SwitchRow(title=tier["name"], subtitle=tier["description"], active=tier["enabled"])
                row.set_sensitive(tier["adjustable"])
                if tier["adjustable"]:
                    row.connect("notify::active", lambda r, _p, t=tier["tier"]: self.window.daemon.call(
                        "SetPermission", "(ub)", (t, r.get_active())))
                listing.append(row)
            self.content.append(listing)
            self.content.append(_caption(
                "Turning something off takes effect at once. Changes Ari already made can still be undone "
                "from Activity."))
            if later:
                self._section("Not available yet")
                listing = _boxed()
                for tier in later:
                    row = Adw.ActionRow(title=tier["name"], subtitle=tier["description"])
                    row.set_sensitive(False)
                    listing.append(row)
                self.content.append(listing)
            self._section("Always")
            listing = _boxed()
            for title, subtitle in (
                    ("What Ari reads can't make changes",
                     "Web pages and search results can inform an answer. A change only happens when you ask for it."),
                    ("Every change is a step you can undo",
                     "Display changes put themselves back unless you keep them."),
                    ("Your activity stays here",
                     "The activity log lives in your own folder and is never uploaded.")):
                row = Adw.ActionRow(title=title, subtitle=subtitle)
                row.set_subtitle_lines(3)
                listing.append(row)
            self.content.append(listing)
        self.window.daemon.call("Settings", None, (), done)

    def _switches(self, data: dict) -> None:
        listing = _boxed()
        master = Adw.SwitchRow(title="Let Ari use this computer",
                               subtitle="Off stops her model and tools at once. Nothing runs until you turn it back on.")
        master.set_active(bool(data.get("enabled", False)))
        master.set_sensitive(bool(data.get("enabled_writable", False)))
        master.connect("notify::active", lambda row, _p: self.window.daemon.call(
            "SetEnabled", "(b)", (row.get_active(),)))
        listing.append(master)
        self.content.append(listing)
        if data.get("locked"):
            self.content.append(_caption("Your administrator manages these settings."))
        self._section("Ask before")
        listing = _boxed()
        first = None
        for mode, title, subtitle in (
                ("system", "System and connection changes",
                 "The time zone, Wi-Fi and Bluetooth. Your own settings change straight away, with Undo."),
                ("all", "Every change", "Ari shows each change and waits for you."),
                ("never", "Don't ask", "Ari makes changes straight away. Undo still works, and anything "
                                       "destructive always asks.")):
            check = Gtk.CheckButton(active=data.get("approval_mode") == mode, valign=Gtk.Align.CENTER)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.connect("toggled", lambda c, m=mode: c.get_active() and self.window.daemon.call("SetApprovalMode", "(s)", (m,)))
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.set_subtitle_lines(2)
            row.add_prefix(check)
            row.set_sensitive(bool(data.get("approval_mode_writable", False)))
            listing.append(row)
        master.bind_property("active", listing, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        self.content.append(listing)

    # ── Activity ─────────────────────────────────────────────────────────

    def _activity(self) -> None:
        def done(value) -> None:
            if isinstance(value, GLib.Error) or self.page != "activity":
                return
            entries = list(reversed(json.loads(value[0])))
            self._clear()
            filters = Gtk.Box(spacing=0, halign=Gtk.Align.START)
            filters.add_css_class("luma-connected-buttons")
            first = None
            for key, label in FILTERS:
                button = Gtk.ToggleButton(label=label, active=key == self.filter)
                if first is None:
                    first = button
                else:
                    button.set_group(first)
                button.connect("toggled", lambda b, k=key: b.get_active() and self._set_filter(k))
                filters.append(button)
            self.content.append(filters)
            shown = [e for e in entries if self._matches(e)]
            if not shown:
                self.content.append(_caption("Nothing here yet." if self.filter == "all"
                                             else "Nothing matches this filter."))
                return
            listing = _boxed()
            for entry in shown[:300]:
                listing.append(self._activity_row(entry))
            self.content.append(listing)
        self.window.daemon.call("Activity", "(u)", (500,), done)

    def _set_filter(self, key: str) -> None:
        if key != self.filter:
            self.filter = key
            self.refresh()

    def _matches(self, entry: dict) -> bool:
        if self.filter == "changes":
            return entry.get("decision") == "ran" and bool(entry.get("step"))
        if self.filter == "refused":
            return entry.get("decision") == "refused"
        return True

    def _activity_row(self, entry: dict) -> Adw.ActionRow:
        from .app import STEP_LOOK  # the same glyphs and tones as the thread's step cards
        tool = entry.get("tool", "")
        arguments = entry.get("arguments") or {}
        glyph, tone, touched = STEP_LOOK.get(tool, (TOOL_GLYPHS.get(tool, "system-search-symbolic"), "slate",
                                                    TOOL_NAMES.get(tool, tool.replace("_", " ").capitalize())))
        decision = entry.get("decision", "")
        if tool in STEP_LOOK:
            title, detail = entry.get("summary") or touched, touched
        else:
            asked = arguments.get("query") or arguments.get("location") or arguments.get("expression") \
                or arguments.get("word") or arguments.get("url") or arguments.get("model") or ""
            title = f"{touched} · {asked}" if asked else touched
            detail = entry.get("summary", "")
        if decision == "refused":
            title, detail, tone, glyph = f"Didn't run: {touched.lower()}", entry.get("reason", ""), "amber", \
                "action-unavailable-symbolic"
        details = [_when(entry.get("time", "")), detail]
        state = entry.get("step_state")
        if decision in ("undone", "reverted"):
            details.append("Put back" if decision == "reverted" else "Undo")
        elif state in ("undone", "reverted"):
            details.append("Undone since" if state == "undone" else "Put back since")
        elif decision == "ran" and entry.get("ok") is False:
            details.append("Didn't work")
        row = Adw.ActionRow(title=title, subtitle=" · ".join(d for d in details if d), use_markup=False)
        row.set_title_lines(1)
        row.set_subtitle_lines(2)
        tile = Gtk.Image(icon_name=glyph, pixel_size=16, valign=Gtk.Align.CENTER)
        tile.add_css_class("ari-step-tile")
        tile.add_css_class(tone)
        row.add_prefix(tile)
        if entry.get("step") and state == "done" and entry.get("undo"):
            undo = Gtk.Button(label="Undo", valign=Gtk.Align.CENTER)
            undo.add_css_class("ari-pill")
            undo.connect("clicked", lambda *_: self.window.daemon.call(
                "Undo", "(s)", (entry["step"],), lambda _v: self.refresh()))
            row.add_suffix(undo)
        return row
