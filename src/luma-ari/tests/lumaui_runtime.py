# SPDX-License-Identifier: Apache-2.0
"""Private-display composition checks; fixture mode must never connect a daemon."""
import os
import json
import time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import unittest
from pathlib import Path
from unittest.mock import patch

from gi.repository import Adw, Gio, GLib, Gtk
from ari_ui.fixture import FixtureSource
from ari_ui import lumaui_app, lumaui_style
from luma_appkit import install_appkit, install_lumaui, add_style_builder

ROOT = Path(__file__).resolve().parents[3]


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def settle(window, seconds):
    """Run the loop, then measure the window's width watches now.

    A watch measures on an idle that can run before the new size is laid out,
    and headless mutter sends no later width change, so measure once more."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(.01)
    window.tier_watch.check()
    window.toggle._watch.check()
    deadline = time.monotonic() + .3  # a redraw, then the kit's phone safe area
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(.01)


class FixtureRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()
        install_appkit()
        install_lumaui()
        add_style_builder(lumaui_style.build)
        cls.application = Adw.Application(application_id="org.projectluma.Ari.LumaUITest")
        cls.application.register(None)

    def setUp(self):
        self.no_daemon = patch.object(lumaui_app, "LiveDaemon", side_effect=AssertionError("Fixture connected live daemon"))
        self.no_daemon.start()
        self.source = FixtureSource.read(ROOT / "tests/fixtures/ari-v70.json")
        self.window = lumaui_app.AriWindow(self.application, self.source)
        self.assertTrue(Gtk.IconTheme.get_for_display(self.window.get_display()).has_icon("ari"))

    def tearDown(self):
        self.window.close()
        self.no_daemon.stop()
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

    def test_message_geometry_at_every_window_tier(self):
        window = self.window
        window.present()
        for width in (1180, 720, 500, 360, 720):
            with self.subTest(width=width):
                window.set_default_size(width, 874)
                settle(window, .5)
                messages = [child for child in _children(window.page)
                            if isinstance(child, lumaui_app.MessageBubble)]
                self.assertTrue(messages)
                for message in messages:
                    ratio = (.8 if window.phone else .72) if message.mine else .88
                    self.assertLessEqual(message.get_allocated_width(), int(window.page.get_width() * ratio) + 1)
                    if not message.mine:
                        self.assertTrue(message.has_css_class('padded'))
                        self.assertEqual(message.get_first_child().get_margin_start(), 0)
                if window.phone:
                    self.assertEqual(window.title_island.lead_button.get_allocated_width(), 48)
                else:
                    self.assertTrue(window.toggle.get_visible())

    def test_all_capture_states_compose_without_real_services(self):
        scenario = json.loads((ROOT / "tools/lumaui-conform/scenarios/ari.json").read_text())
        desktop = {state["name"] for state in scenario["states"]}
        phone = set(scenario["phone_states"])
        self.assertEqual(desktop, phone)
        for width, height in ((1180, 740), (390, 844)):
            for state in sorted(desktop):
                with self.subTest(state=state, width=width), patch.dict(os.environ, {
                        "LUMA_ARI_STATE": state, "LUMAUI_CONFORM_SIZE": f"{width}x{height}"}):
                    source = FixtureSource.read(ROOT / "tests/fixtures/ari-v70.json")
                    window = lumaui_app.AriWindow(self.application, source)
                    try:
                        window.set_default_size(width, height)
                        window.present()
                        settle(window, .15)
                        self.assertEqual(window.get_width(), width)
                        self.assertIsNotNone(window.page.get_first_child())
                        self.assertEqual(window.foot.entry.get_text(), window.state.get("sq", ""))
                        self.assertIsNone(window.daemon)
                        # v71: under 560 the phone shape; the title row's toggle steps aside for the island.
                        self.assertEqual(window.phone, width == 390)
                        self.assertEqual(window.toggle.get_visible(), width != 390)
                        if state == "model-picker":
                            if width == 390:
                                self.assertIsNone(window.popover)
                                self.assertEqual(window.bar.grown, "model")
                            else:
                                self.assertIsNotNone(window.popover)
                        elif state == "places":
                            self.assertEqual(window.title_island.grown, width == 390)
                        elif state in ("activity-filter", "models-filter"):
                            self.assertEqual(window.bar.grown, ("filter" if state == "activity-filter" else "store")
                                             if width == 390 else None)
                        elif state == "providers" or state.startswith("provider-"):
                            self.assertIsNotNone(window.modal)
                            self.assertEqual(isinstance(window.modal, lumaui_app.BarFrame), width == 390)
                    finally:
                        window.close()
                        while GLib.MainContext.default().pending():
                            GLib.MainContext.default().iteration(False)

    def test_approval_draft_body_is_allocated_at_desktop_and_phone(self):
        import time

        for width, height in ((1180, 740), (390, 844), (360, 844)):
            with self.subTest(width=width):
                window = lumaui_app.AriWindow(self.application, self.source)
                try:
                    window.set_default_size(width, height)
                    window._capture_state("budget")
                    window.render()
                    window.present()
                    settle(window, .4)

                    def descendants(widget):
                        yield widget
                        child = widget.get_first_child()
                        while child:
                            yield from descendants(child)
                            child = child.get_next_sibling()

                    approval = next(w for w in descendants(window.page)
                                    if w.get_name() == "ari-approval-budget")
                    copies = [w for w in descendants(approval)
                              if isinstance(w, Gtk.TextView)
                              or w.has_css_class("ari-draft-copy")]
                    self.assertEqual(len(copies), 1)
                    copy = copies[0]
                    expected = " ".join(self.source.data["activity"]["budget"]["ask"]["body"][1:-1])
                    self.assertTrue(expected)
                    if isinstance(copy, Gtk.TextView):
                        self.assertFalse(copy.get_editable())
                        self.assertFalse(copy.get_cursor_visible())
                        buffer = copy.get_buffer()
                        actual = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
                    else:
                        actual = copy.get_label()
                    self.assertEqual(actual, expected)
                    if isinstance(copy, Gtk.Label):
                        self.assertFalse(copy.get_layout().is_ellipsized())
                        if width < 560:
                            self.assertGreater(copy.get_layout().get_line_count(), 2)
                    self.assertGreater(copy.get_width(), 0)
                    self.assertGreaterEqual(copy.get_height(),
                                            2 * copy.create_pango_layout("Ag").get_pixel_size()[1])
                    approve = next(w for w in descendants(approval)
                                   if w.get_name() == "ari-approve-budget")

                    def above_bar():
                        found, button = approve.compute_bounds(window)
                        placed, bar = window.bar.bar.compute_bounds(window)
                        return found and placed and button.get_y() + button.get_height() <= bar.get_y()

                    # On a phone the kit's safe area lands a few frames after the redraw
                    # (the bar takes its phone shape first); the thread then keeps its end.
                    deadline = time.monotonic() + 2
                    while not above_bar() and time.monotonic() < deadline:
                        GLib.MainContext.default().iteration(False)
                        time.sleep(.01)
                    located, bounds = approve.compute_bounds(window)
                    self.assertTrue(located)
                    self.assertGreater(bounds.get_height(), 0)
                    self.assertLessEqual(bounds.get_y() + bounds.get_height(), height)
                    bar_located, composer = window.bar.bar.compute_bounds(window)
                    self.assertTrue(bar_located)
                    self.assertLessEqual(bounds.get_y() + bounds.get_height(), composer.get_y())
                    # A shared receipt taller than the available viewport
                    # requires scrolling. Content that fits must keep its title.
                    adjustment = window.scroll.get_vadjustment()
                    last_located, last_bounds = window.page.get_last_child().compute_bounds(window)
                    self.assertTrue(last_located)
                    if not window.phone and last_bounds.get_y() + last_bounds.get_height() + adjustment.get_value() <= composer.get_y():
                        heading = next(w for w in descendants(window.page)
                                       if w.get_name() == "ari-chat-heading")
                        heading_located, heading_bounds = heading.compute_bounds(window.scroll)
                        self.assertTrue(heading_located)
                        self.assertGreaterEqual(heading_bounds.get_y(), 0)
                finally:
                    window.close()
                    while GLib.MainContext.default().pending():
                        GLib.MainContext.default().iteration(False)

    def test_pending_approval_at_phone_width_keeps_v70_receipt_actions(self):
        self.window.set_default_size(390, 844)
        self.window._capture_state("approval-working")
        self.window.render()
        self.window.present()
        settle(self.window, .3)
        self.assertEqual(self.window.get_width(), 390)
        self.assertTrue(self.window.phone)
        self.assertFalse(self.window.title_island.grown)
        # Navigate through the real phone Places control (the title island's ☰) while approval runs.
        def open_places():
            self.window.title_island.lead_button.emit("clicked")
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline and not self.window.title_island.grown:
                GLib.MainContext.default().iteration(False)
                time.sleep(.01)
        open_places()
        self.assertTrue(self.window.title_island.grown)
        row = self.window.sidebar.list.get_first_child()
        while row and getattr(row, "view", None) != "activity":
            row = row.get_next_sibling()
        self.assertIsNotNone(row)
        self.assertTrue(row.activate())
        self.assertEqual(self.window.state["view"], "activity")
        self.assertFalse(self.window.title_island.grown)
        open_places()
        self.assertTrue(self.window.title_island.grown)
        row = self.window.sidebar.list.get_first_child()
        while row and getattr(row, "chat", None) != "budget":
            row = row.get_next_sibling()
        self.assertIsNotNone(row)
        self.assertTrue(row.activate())
        self.assertEqual(self.window.state["cur"], "budget")
        self.assertFalse(self.window.title_island.grown)
        settle(self.window, .1)
        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()
        widgets = list(descendants(self.window.page))
        names = {widget.get_name() for widget in widgets}
        self.assertNotIn("ari-undo-budget", names)
        self.assertNotIn("ari-approve-budget", names)
        self.assertNotIn("ari-decline-budget", names)
        self.assertTrue(any(isinstance(widget, Gtk.Spinner) and widget.get_spinning() for widget in widgets))
        self.assertTrue(any(isinstance(widget, Gtk.Label) and widget.get_text() == "Working" for widget in widgets))
        receipt = next(widget for widget in widgets if widget.get_name() == "ari-receipt-budget")
        self.assertGreater(receipt.get_width(), 0)
        self.assertLessEqual(receipt.get_width(), self.window.get_width())
        valid, bounds = receipt.compute_bounds(self.window)
        self.assertTrue(valid)
        self.assertGreaterEqual(bounds.get_x(), 0)
        self.assertLessEqual(bounds.get_x() + bounds.get_width(), 390)

        open_places()
        self.window.new_chat()
        self.assertEqual(self.window.state["cur"], "new")
        self.assertFalse(self.window.title_island.grown)

    def test_model_hero_keeps_controls_within_supported_widths(self):
        for width in (360, 390, 500, 1024, 1180):
            with self.subTest(width=width):
                window = lumaui_app.AriWindow(self.application, self.source)
                try:
                    window.set_default_size(width, 844)
                    window._capture_state("models-all")
                    window.render()
                    window.present()
                    settle(window, .3)
                    self.assertEqual(window.get_width(), width)
                    self.assertIsNotNone(window.hero_view)
                    grid, mark, information, fitting, action = window.hero_view
                    self.assertTrue(action.get_sensitive())
                    for widget in (mark, information, fitting, action):
                        self.assertIs(widget.get_parent(), grid)
                        self.assertGreater(widget.get_width(), 0)
                        ok, bounds = widget.compute_bounds(window)
                        self.assertTrue(ok)
                        self.assertGreaterEqual(bounds.get_x(), 0)
                        self.assertLessEqual(bounds.get_x() + bounds.get_width(), width)
                finally:
                    window.close()

    def test_native_capacity_legend_uses_readings_and_ignores_stale_callback(self):
        self.window.read_generation = 3
        before = dict(self.window.data["computer"])
        self.window._capacity_ready(3, {"mem": 99})
        self.assertEqual(self.window.data["computer"], before)
        self.window.fixture = False
        self.window._capacity_ready(2, {"mem": 99})
        self.assertEqual(self.window.data["computer"], before)
        self.window._capacity_ready(3, {"mem": 24, "sys": 8, "free": 16, "disk": 100})
        view = self.window.memory()
        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()
        labels = [widget.get_label() for widget in descendants(view) if isinstance(widget, Gtk.Label)]
        self.assertIn("Luma and apps 8 GB", labels)
        self.assertIn("Free 16 GB", labels)
        self.assertIn("24 GB memory", labels)
        self.assertFalse(any("Qwen" in label for label in labels))

    def test_receipt_thumbnail_keeps_its_small_natural_size(self):
        node = self.source.data["activity"]["shots"]["nodes"][0]
        thumbnail = self.window.receipt_lead(node)
        self.assertEqual(thumbnail.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 22)
        self.assertEqual(thumbnail.measure(Gtk.Orientation.VERTICAL, 22)[1], 22)

    def test_chat_rerender_preserves_composer_focus_and_draft(self):
        self.window.present()
        # BarEntry.set_text deliberately does not emit on_change. Edit the
        # native buffer, as typing does, to exercise Ari's real draft wiring.
        self.window.entry.widget.view.get_buffer().set_text("An unfinished draft")
        self.window.entry.focus()
        self.assertTrue(self.window.get_focus().is_ancestor(self.window.entry.widget))
        self.window.render()
        self.assertEqual(self.window.entry_text(), "An unfinished draft")
        self.assertTrue(self.window.get_focus().is_ancestor(self.window.entry.widget))

    def test_live_preview_has_a_separate_application_identity(self):
        with patch.dict(os.environ, {"LUMA_ARI_PREVIEW": "1"}):
            app = lumaui_app.AriApplication()
            self.assertEqual(app.get_application_id(), lumaui_app.FIXTURE_ID)
            self.assertIsNone(app.source)

    def test_preview_environment_cannot_activate_production_bus_identity(self):
        # run-lumaui-runtime.sh provides a private bus, XDG homes and display.
        production = Adw.Application(application_id=lumaui_app.APP_ID)
        activated = []
        production.connect("activate", lambda _app: activated.append(True))
        production.register(None)
        self.assertFalse(production.get_is_remote())
        from ari_ui import app as public_entry
        # A Photos-style patch to a delegating module's APP_ID does not
        # reach AriApplication. The actual launcher must set its read hook.
        with patch.dict(os.environ, {"LUMA_ARI_PREVIEW": "0"}):
            with patch.object(public_entry, "APP_ID", lumaui_app.FIXTURE_ID, create=True):
                self.assertEqual(public_entry.AriApplication().get_application_id(), lumaui_app.APP_ID)
        with patch.dict(os.environ, {"LUMA_ARI_PREVIEW": "1"}):
            preview = lumaui_app.AriApplication()
        try:
            self.assertEqual(lumaui_app.APP_ID, "org.projectluma.Ari")
            self.assertEqual(preview.get_application_id(), lumaui_app.FIXTURE_ID)
            # Set fixture data after construction, so the ID assertion tests
            # the launcher's environment override rather than the fixture path.
            preview.source = self.source
            preview.register(None)
            self.assertFalse(preview.get_is_remote())
            self.assertNotEqual(preview.get_dbus_object_path(), production.get_dbus_object_path())
            preview.activate()
            self.assertEqual(activated, [])
            self.assertEqual(production.get_windows(), [])
            self.assertEqual(len(preview.get_windows()), 1)
            self.assertEqual(preview.get_windows()[0].get_application().get_application_id(), lumaui_app.FIXTURE_ID)
            from unittest.mock import Mock
            close_command = Mock()
            close_command.get_options_dict.return_value.end.return_value.unpack.return_value = {"close-preview": True}
            self.assertEqual(preview.do_command_line(close_command), 0)
            self.assertEqual(preview.get_windows(), [])
            self.assertEqual(activated, [])
        finally:
            for window in preview.get_windows():
                window.close()
            preview.quit()
            production.quit()
        self.assertEqual(preview.get_windows(), [])
        with patch.dict(os.environ, {"LUMA_ARI_PREVIEW": "0"}):
            unchanged = lumaui_app.AriApplication()
            self.assertEqual(unchanged.get_application_id(), lumaui_app.APP_ID)
            self.assertEqual(unchanged.do_command_line(close_command), 2)

    def test_approval_and_undo_are_memory_only(self):
        self.window.answer("budget", True)
        self.window.undo("budget")
        nodes = self.source.data["activity"]["budget"]["nodes"]
        self.assertEqual([node["st"] for node in nodes], ["done", "undone", "undone", "done"])
        self.assertIsNone(self.window.daemon)

    def test_stop_prevents_late_fixture_completion(self):
        self.window.new_chat()
        self.window.send("Explain local models in two sentences")
        self.assertTrue(self.window.state["busy"])
        self.window.stop()
        settle(self.window, 1.1)
        chat = next(chat for chat in self.source.data["conversations"] if chat["id"] == self.window.state["cur"])
        self.assertEqual(chat["msgs"], [{"u": "Explain local models in two sentences"}])
        self.assertFalse(self.window.state["busy"])
        self.assertIsNone(self.window.daemon)

    def test_picker_and_provider_layers_close_and_clear_credentials(self):
        self.window.present()
        settle(self.window, .3)
        self.window.model_picker()
        self.assertIsNotNone(self.window.popover)
        self.window.close_picker()
        self.assertIsNone(self.window.popover)
        self.window.provider_sheet("openai")
        self.assertIsNotNone(self.window.modal)
        self.assertFalse(self.window.connect_button.get_sensitive())
        self.window._provider_key("short", False)
        self.assertFalse(self.window.connect_button.get_sensitive())
        self.window._provider_key("fixture-test-secret", False)
        self.assertTrue(self.window.connect_button.get_sensitive())
        self.window.close_sheet()
        self.assertEqual(self.window.provider_state["key"], "")
        self.assertIsNone(self.window.modal)
        self.window.provider_sheet("own")
        self.assertTrue(self.window.connect_button.get_sensitive())
        self.window.close_sheet()

    def test_closed_window_ignores_late_action_route_and_download_callbacks(self):
        import copy
        from unittest.mock import Mock
        window = self.window
        window.fixture = False
        window.daemon = Mock()
        callbacks = []
        window.answer("budget", True)
        callbacks.append((window.daemon.call.call_args.args[3], (True,)))
        window.undo("quiet")
        callbacks.append((window.daemon.call.call_args.args[3], (True,)))
        window.keep("quiet")
        callbacks.append((window.daemon.call.call_args.args[3], (True,)))
        window.use_model("qwen8")
        callbacks.append((window.daemon.call.call_args.args[3], (True,)))
        model = next(item for item in window.data["local_models"]
                     if not item.get("have") and item.get("dl") is None)
        window.download(model["id"])
        callbacks.append((window.daemon.call.call_args.args[3], ("late-download",)))
        window.request = "active-request"
        window._closing()
        self.assertTrue(window.closed)
        window.daemon.call.assert_any_call("Stop", "(s)", ("active-request",))
        window.daemon.call.assert_any_call("Release")
        data = copy.deepcopy(window.data)
        with patch.object(window, "render", side_effect=AssertionError("Closed window rendered")), \
                patch.object(window, "notify", side_effect=AssertionError("Closed window notified")):
            for callback, result in callbacks:
                callback(result)
            window.on_event("late-download", json.dumps({"type": "installed"}))
            window.on_event("active-request", json.dumps({"type": "text", "text": "late reply"}))
        self.assertEqual(window.data, data)
        self.assertNotIn("late-download", window.downloads)

    def test_closed_provider_drops_key_and_late_verification(self):
        import copy
        from unittest.mock import Mock
        window = self.window
        window.present()
        window.fixture = False
        window.daemon = Mock()
        window.provider_sheet("openrouter")
        window._provider_key("isolated-test-key", False)
        window._provider_test()
        callback = window.daemon.call.call_args.args[3]
        window._closing()
        self.assertEqual(window.provider_state["key"], "")
        self.assertIsNone(window.provider_field)
        self.assertIsNone(window.modal)
        state = copy.deepcopy(window.provider_state)
        with patch.object(window, "render", side_effect=AssertionError("Closed window rendered")), \
                patch.object(window, "_provider_render", side_effect=AssertionError("Closed sheet rendered")), \
                patch.object(window, "notify", side_effect=AssertionError("Closed window notified")):
            callback((True, "Accepted"))
        self.assertEqual(window.provider_state, state)

    def test_live_model_picker_does_not_invent_speed_from_missing_memory(self):
        from unittest.mock import Mock
        self.window.present()
        self.window.fixture = False
        self.window.daemon = Mock()
        model = dict(next(item for item in self.window.data["local_models"] if item.get("have")),
                     mem=0, wps=0)
        self.window.data["local_models"] = [model]
        self.window.data["providers"] = []
        self.window.model_picker()

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()

        labels = [widget.get_label() for widget in descendants(self.window.popover)
                  if isinstance(widget, Gtk.Label)]
        self.assertIn("On this computer", labels)
        self.assertFalse(any("Quick" in label or "Slower, more capable" in label
                             or "GB memory" in label for label in labels))

    def test_window_closure_clears_focus_restored_by_picker(self):
        self.window.present()
        settle(self.window, .3)
        self.window.model_picker()
        self.assertIsNotNone(self.window.popover)
        self.window._closing()
        self.assertIsNone(self.window.popover)
        self.assertIsNone(self.window.get_focus())

    def test_daemon_close_disconnects_real_proxy_events_and_drops_window_callbacks(self):
        from unittest.mock import Mock
        proxy = Gio.DBusProxy.new_sync(
            Gio.bus_get_sync(Gio.BusType.SESSION, None),
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES | Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None, "org.projectluma.Ari.LumaUITest", "/org/projectluma/AriTest",
            "org.projectluma.AriTest", None)
        ready, event = Mock(), Mock()
        with patch.object(Gio.DBusProxy, "new_for_bus"), patch.object(
                Gio.DBusProxy, "new_for_bus_finish", return_value=proxy):
            daemon = self.no_daemon.temp_original(ready, event)
            daemon._connected(None, None)
        ready.assert_called_once_with(None)
        proxy.emit("g-signal", "test", "Event", GLib.Variant("(ss)", ("request", "payload")))
        event.assert_called_once_with("request", "payload")
        daemon.close()
        proxy.emit("g-signal", "test", "Event", GLib.Variant("(ss)", ("late", "payload")))
        self.assertEqual(event.call_count, 1)
        self.assertIsNone(daemon.ready)
        self.assertIsNone(daemon.event)
        self.assertIsNone(daemon._event_handler)
        self.assertIs(daemon.proxy, proxy)
        daemon.close()

    def test_daemon_connection_after_close_never_restores_window_callbacks(self):
        from unittest.mock import Mock
        ready, event, proxy = Mock(), Mock(), Mock()
        with patch.object(Gio.DBusProxy, "new_for_bus"), patch.object(
                Gio.DBusProxy, "new_for_bus_finish", return_value=proxy):
            daemon = self.no_daemon.temp_original(ready, event)
            daemon.close()
            daemon._connected(None, None)
        ready.assert_not_called()
        event.assert_not_called()
        proxy.connect.assert_not_called()
        self.assertIsNone(daemon.proxy)

    def test_approval_edit_callback_never_claims_an_unopened_live_editor(self):
        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()
        edit = next(widget for widget in descendants(self.window.page)
                    if widget.get_name() == "ari-edit-budget")
        with patch.object(self.window, "notify") as feedback:
            edit.emit("clicked")
            feedback.assert_called_once_with("The draft opens in Charlie")
        self.window.fixture = False
        with patch.object(self.window, "notify") as feedback:
            edit.emit("clicked")
            feedback.assert_called_once_with("Editing this change is not available", error=True)

    def test_provider_failure_keeps_inline_feedback_and_retry(self):
        from unittest.mock import Mock
        self.window.present()
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.provider_sheet("openrouter")
        self.window._provider_key("fixture-test-key", False)
        self.window._provider_test()
        self.window.daemon.call.call_args.args[3]((False, "The key was refused"))
        self.assertEqual(self.window.provider_state["test"], "bad")
        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()
        feedback = [widget for widget in descendants(self.window.modal.card)
                    if widget.has_css_class("ari-provider-bad")]
        self.assertEqual(len(feedback), 1)
        labels = [widget.get_label() for widget in descendants(feedback[0]) if isinstance(widget, Gtk.Label)]
        self.assertIn("That key didn’t work. Check it and try again", labels)
        self.assertTrue(self.window.connect_button.get_sensitive())
        self.assertEqual(self.window.provider_field.entry.get_text(), "fixture-test-key")
        self.assertIsNotNone(self.window.modal)
        self.window._provider_test()
        self.assertEqual(self.window.provider_state["test"], "run")
        self.assertEqual(self.window.daemon.call.call_count, 2)

    def test_phone_width_uses_same_window_and_collapses_sidebar(self):
        self.window.set_default_size(390, 844)
        self.window.present()
        settle(self.window, .4)
        self.assertEqual(self.window.get_width(), 390)
        self.assertTrue(self.window.phone)
        self.assertFalse(self.window.toggle.get_visible())
        # v71 phone: the house grows the bar into the model picker.
        self.window.model_button.emit("clicked")
        self.assertEqual(self.window.bar.grown, "model")
        def named_descendant(widget, name):
            if widget.get_name() == name:
                return widget
            child = widget.get_first_child()
            while child:
                found = named_descendant(child, name)
                if found:
                    return found
                child = child.get_next_sibling()
            return None
        drawer = self.window.bar
        self.assertIsNotNone(named_descendant(drawer, "ari-pick-qwen8"))
        self.assertIsNotNone(named_descendant(drawer, "ari-add-provider"))
        self.assertIsNotNone(named_descendant(drawer, "ari-more-models"))
        self.window.bar.fold()
        self.window.open_chat("shots")
        settle(self.window, .2)
        thumbnail = named_descendant(self.window.page, "ari-receipt-thumbnail")
        self.assertEqual((thumbnail.get_width(), thumbnail.get_height()), (22, 22))
        self.window.provider_sheet("openai")
        self.assertIsInstance(self.window.modal, lumaui_app.BarFrame)
        settle(self.window, .2)
        self.assertLessEqual(self.window.modal.get_width(), self.window.get_width())
        self.window.close_sheet()
        for view in ("activity", "models", "chat"):
            self.window.state["view"] = view
            self.window.render()
            settle(self.window, .2)
            self.assertEqual(self.window.get_width(), 390, view)
            self.assertTrue(self.window.phone, view)
            if view == "chat":
                # Your words are the kit's message bubble, on the right; the title is the island's.
                bubble = named_descendant(self.window.page, "ari-user-0")
                self.assertIsInstance(bubble, lumaui_app.MessageBubble)
                self.assertTrue(bubble.mine)
                self.assertGreater(bubble.get_width(), 0)
                chat = next(c for c in self.window.data["conversations"] if c["id"] == self.window.state["cur"])
                self.assertEqual((self.window.title_island.title, self.window.title_island.subtitle),
                                 (chat["t"], chat["day"]))
                self.assertIsNone(named_descendant(self.window.page, "ari-chat-heading"))

    def test_live_stream_approval_and_revert_use_existing_interface(self):
        import json
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.request = "request-1"
        self.window.state["busy"] = True
        emit = lambda payload: self.window.on_event("request-1", json.dumps(payload))
        emit({"type": "text", "text": "First "})
        emit({"type": "text", "text": "part"})
        chat = next(chat for chat in self.source.data["conversations"] if chat["id"] == self.window.state["cur"])
        self.assertEqual(chat["msgs"][-1]["a"], "First part")
        emit({"type": "done", "text": "Complete"})
        self.assertEqual(chat["msgs"][-1], {"a": "Complete"})
        self.assertFalse(self.window.state["busy"])
        self.window.request = "request-2"
        self.window.on_event("request-2", json.dumps({"type": "approval", "approval": "approval-1",
            "tool": "settings.change", "summary": "Change sound", "detail": "Set volume"}))
        self.assertEqual(self.window.data["activity"]["approval-1"]["nodes"][0]["st"], "ask")
        self.window.answer("approval-1", False)
        args = self.window.daemon.call.call_args.args
        self.assertEqual(args[:3], ("Approve", "(sb)", ("approval-1", False)))
        args[3]((True,))
        self.assertEqual(self.window.data["activity"]["approval-1"]["nodes"][0]["st"], "undone")
        self.window.on_event("request-2", json.dumps({"type": "step", "step": "step-1", "tool": "settings.change",
            "summary": "Volume changed", "undo": True, "confirm_within": 15}))
        self.window.keep("step-1")
        self.assertEqual(self.window.daemon.call.call_args.args[:3], ("KeepStep", "(s)", ("step-1",)))
        self.window.on_event("request-2", json.dumps({"type": "reverted", "step": "step-1"}))
        self.assertEqual(self.window.data["activity"]["step-1"]["nodes"][0]["st"], "undone")

    def test_timed_revert_after_reply_and_navigation_updates_only_its_receipt(self):
        import json
        self.window.request = "completed-request"
        emit = lambda payload: self.window.on_event("completed-request", json.dumps(payload))
        emit({"type": "step", "step": "timed-step", "tool": "settings.change",
              "summary": "Display changed", "undo": True, "confirm_within": 15})
        emit({"type": "done", "text": "Complete"})
        self.window.open_chat("quiet")
        receipt = self.window.data["activity"]["timed-step"]
        self.window.on_event("other-request", json.dumps({"type": "reverted", "step": "timed-step"}))
        self.assertEqual(receipt["nodes"][0]["st"], "done")
        emit({"type": "reverted", "step": "timed-step"})
        self.assertEqual(receipt["nodes"][0]["st"], "undone")
        self.assertEqual(receipt["undo"], "none")
        self.assertTrue(receipt["kept"])
        self.assertEqual(self.window.state["cur"], "quiet")

    def test_reply_signature_uses_actual_route_even_after_selection_changes(self):
        import json
        self.window.request = "cloud-reply"
        self.window.state["model"] = "qwen8"
        model = {"id": "actual-model", "n": "Actual cloud model", "local": False, "via": "OpenRouter"}
        self.window.on_event("cloud-reply", json.dumps({"type": "done", "text": "Reply",
            "meta": {"model_info": model, "elapsed": 3.1}}))
        chat = next(chat for chat in self.window.data["conversations"] if chat["id"] == self.window.state["cur"])
        self.assertEqual(chat["msgs"][-1]["model_info"], model)
        self.assertEqual(chat["msgs"][-1]["s"], 3.1)
        self.assertEqual(self.window.state["model"], "qwen8")
        def labels(widget):
            if isinstance(widget, Gtk.Label):
                yield widget.get_text()
            child = widget.get_first_child()
            while child:
                yield from labels(child)
                child = child.get_next_sibling()
        self.assertIn("Cloud", list(labels(self.window.page)))

    def test_approval_waits_for_success_and_retries_after_failure(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.answer("budget", True)
        callback = self.window.daemon.call.call_args.args[3]
        node = self.source.data["activity"]["budget"]["nodes"][-1]
        self.assertEqual(node["st"], "run")
        self.window.answer("budget", True)
        self.window.daemon.call.assert_called_once()
        callback((False,))
        self.assertEqual(node["st"], "ask")
        self.window.answer("budget", True)
        self.window.daemon.call.call_args.args[3]((True,))
        self.assertEqual(node["st"], "done")

    def test_local_selection_updates_destination_only_after_route_success(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.state["model"] = "sonnet"
        self.window.use_model("qwen8")
        selected = self.window.daemon.call.call_args.args
        self.assertEqual(selected[:3], ("SetActiveModel", "(s)", ("qwen8",)))
        selected[3]((True,))
        local = self.window.daemon.call.call_args.args
        self.assertEqual(local[:3], ("SetBrain", "(s)", ("local",)))
        self.assertEqual(self.window.state["model"], "sonnet")
        local[3]((False, "Unavailable"))
        self.assertEqual(self.window.state["model"], "sonnet")
        self.window.use_model("qwen8")
        self.window.daemon.call.call_args.args[3]((True,))
        self.window.daemon.call.call_args.args[3]((True, ""))
        self.assertEqual(self.window.state["model"], "qwen8")

    def test_late_model_selection_does_not_enable_an_older_route(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.use_model("qwen8")
        old = self.window.daemon.call.call_args.args[3]
        self.window.use_model("qwen30")
        latest = self.window.daemon.call.call_args.args[3]
        old((True,))
        self.assertEqual(self.window.daemon.call.call_count, 2)
        latest((True,))
        self.window.daemon.call.call_args.args[3]((True, ""))
        self.assertEqual(self.window.state["model"], "qwen30")
        old((True,))
        self.assertEqual(self.window.state["model"], "qwen30")

    def test_initial_model_read_does_not_replace_a_new_selection(self):
        import json
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window._live_ready(None)
        callback = next(call.kwargs["callback"] for call in self.window.daemon.call.call_args_list
                        if call.args[0] == "Models")
        self.window.use_model("qwen30")
        self.window.daemon.call.call_args.args[3]((True,))
        self.window.daemon.call.call_args.args[3]((True, ""))
        callback((json.dumps({"models": [], "active": "qwen8", "hardware": {"ram_gb": 32}}),))
        self.assertEqual(self.window.state["model"], "qwen30")

    def test_early_stream_before_ask_reply_is_replayed_for_its_request(self):
        import json
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.send("start")
        callback = self.window.daemon.call.call_args.args[3]
        self.window.on_event("other", json.dumps({"type": "text", "text": "unrelated"}))
        self.window.on_event("early", json.dumps({"type": "text", "text": "First"}))
        self.window.on_event("early", json.dumps({"type": "done", "text": "Complete"}))
        callback(("stored", "early"))
        chat = next(chat for chat in self.window.data["conversations"] if chat["id"] == "stored")
        self.assertEqual(chat["msgs"][-1], {"a": "Complete"})
        self.assertFalse(self.window.state["busy"])
        self.assertEqual(self.window.pending_request_events, [])

    def test_late_ask_error_does_not_stop_a_newer_request(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.send("first")
        old = self.window.daemon.call.call_args.args[3]
        self.window.new_chat()
        self.window.send("second")
        current = self.window.daemon.call.call_args.args[3]
        old(RuntimeError("late"))
        self.assertTrue(self.window.state["busy"])
        current(("new-live-conversation", "new-request"))
        self.assertEqual(self.window.request, "new-request")

    def test_failed_existing_request_restores_draft_and_new_chat_recovers(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        existing = self.window.data["conversations"][0]
        self.window.state["cur"] = existing["id"]
        before = list(existing["msgs"])
        self.window.send("What is the weather in Kansas City?")
        failed = self.window.daemon.call.call_args.args[3]
        failed(RuntimeError("Ari is not running"))
        self.assertEqual(existing["msgs"], before)
        self.assertEqual(self.window.draft, "What is the weather in Kansas City?")
        self.assertEqual(self.window.entry.text, self.window.draft)
        self.assertFalse(self.window.state["busy"])
        self.window.new_chat()
        self.window.send("What is the weather in Kansas City?")
        current = self.window.daemon.call.call_args.args[3]
        self.assertEqual(self.window.data["conversations"][0]["t"], "What is the weather in Kansas City?")
        current(("new-live-conversation", "new-request"))
        self.window.on_event("new-request", json.dumps({"type": "done", "text": "Clear skies"}))
        self.assertEqual(self.window.data["conversations"][0]["msgs"][-1], {"a": "Clear skies"})
        self.assertFalse(self.window.state["busy"])

    def test_failed_first_request_removes_phantom_chat_and_keeps_draft(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.state["cur"] = "new"
        self.window.send("Please help me")
        self.window.daemon.call.call_args.args[3](RuntimeError("ServiceUnknown"))
        self.assertEqual(self.window.state["cur"], "new")
        self.assertEqual(self.window.draft, "Please help me")
        self.assertFalse(any(chat["t"] == "Please help me" for chat in self.window.data["conversations"]))

    def test_sidebar_foot_and_compose_controls_have_visible_gutters(self):
        self.window.set_default_size(1180, 740)
        self.window.present()
        settle(self.window, .35)
        def bounds(widget):
            found, rect = widget.compute_bounds(self.window)
            self.assertTrue(found)
            return rect
        sidebar, search, add = map(bounds, (self.window.sidebar, self.window.foot.field,
                                             self.window.foot.add_button))
        self.assertGreaterEqual(search.get_x() - sidebar.get_x(), 9)
        self.assertGreaterEqual(sidebar.get_x() + sidebar.get_width() - add.get_x() - add.get_width(), 9)
        model, field = map(bounds, (self.window.model_button, self.window.entry.widget.well))
        self.assertGreater(field.get_x(), model.get_x() + model.get_width())
        # v71: one row; Attach is inside the field, and there is no expand key.
        self.assertIsNone(self.window.entry.widget.grow_button)
        tools = [getattr(child, "bar_item", None) for child in _children(self.window.entry.widget.well)]
        self.assertIn("paperclip", [getattr(item, "icon", None) for item in tools])

    def test_long_chat_title_leaves_space_for_the_date_across_widths(self):
        self.window.data["conversations"].insert(0, {
            "id": "title-check", "t": "What is the weather in Kansas City this afternoon?",
            "day": "Today", "msgs": [{"u": "What is the weather in Kansas City this afternoon?"}],
        })
        self.window.state["cur"] = "title-check"
        for width in (600, 1024, 1180):
            with self.subTest(width=width):
                self.window.set_default_size(width, 740)
                self.window.render()
                self.window.present()
                settle(self.window, .18)
                heading = self.window.page.get_first_child()
                title = heading.get_first_child()
                day = title.get_next_sibling()
                title_ok, title_rect = title.compute_bounds(self.window)
                day_ok, day_rect = day.compute_bounds(self.window)
                self.assertTrue(title_ok and day_ok)
                self.assertLessEqual(title_rect.get_x() + title_rect.get_width() + 8, day_rect.get_x())
                self.assertLessEqual(day_rect.get_x() + day_rect.get_width(), width)

    def test_initial_chat_read_does_not_replace_a_new_draft(self):
        import json
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        self.window.state.update(view="chat", cur="new")
        self.window._live_ready(None)
        callback = next(call.kwargs["callback"] for call in self.window.daemon.call.call_args_list
                        if call.args[0] == "ListConversations")
        self.window.new_chat()
        self.window.draft = "a draft while chats load"
        callback((json.dumps([{"id": "stored", "title": "Stored chat", "updated": 1}]),))
        self.assertEqual(self.window.state["cur"], "new")
        self.assertEqual(self.window.draft, "a draft while chats load")
        self.assertEqual(self.window.data["conversations"][0]["id"], "stored")

    def test_stale_events_do_not_change_another_chat(self):
        import json
        before = list(self.source.data["conversations"][0]["msgs"])
        self.window.request = "current"
        self.window.on_event("previous", json.dumps({"type": "text", "text": "stale"}))
        self.assertEqual(self.source.data["conversations"][0]["msgs"], before)

    def test_download_events_before_install_reply_reach_the_correct_model(self):
        import json
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        models = {item["id"]: item for item in self.window.data["local_models"]}
        for identity in ("qwen8", "qwen30"):
            models[identity]["have"] = False
            models[identity].pop("dl", None)
            self.window.download(identity)
        first, second = [call.args[3] for call in self.window.daemon.call.call_args_list]
        self.window.download("qwen8")
        self.assertEqual(self.window.daemon.call.call_count, 2)
        self.window.on_event("first", json.dumps({"type": "download", "done": 35, "total": 100}))
        self.window.on_event("second", json.dumps({"type": "installed"}))
        self.window.on_event("unrelated", json.dumps({"type": "installed"}))
        second(("second",))
        self.assertTrue(models["qwen30"]["have"])
        self.assertNotIn("dl", models["qwen30"])
        first(("first",))
        self.assertEqual(models["qwen8"]["dl"], 35)
        self.assertFalse(models["qwen8"]["have"])
        self.assertEqual(self.window.pending_download_events, [])
        self.window.on_event("first", json.dumps({"type": "error", "message": "Download failed"}))
        self.assertNotIn("dl", models["qwen8"])
        self.assertEqual(self.window.downloads, {})

    def test_failed_install_reply_releases_model_for_retry(self):
        from unittest.mock import Mock
        self.window.fixture = False
        self.window.daemon = Mock()
        model = next(item for item in self.window.data["local_models"] if item["id"] == "qwen14")
        model.pop("dl", None)
        self.window.download("qwen14")
        self.window.daemon.call.call_args.args[3](RuntimeError("Unavailable"))
        self.assertNotIn("dl", model)
        self.assertEqual(self.window.pending_model_installs, set())
        self.window.download("qwen14")
        self.assertEqual(self.window.daemon.call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
