#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the v70 Messages window on a private headless Wayland display."""

from pathlib import Path
import os
import sys
import time
from types import SimpleNamespace

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src/luma-platform/appkit"))
sys.path.insert(0, str(ROOT / "src/prairie-core"))

from luma_appkit import install_appkit, install_lumaui  # noqa: E402
from prairie_apps.messages_port import FixtureMessagesWindow, _unique_participants  # noqa: E402
from prairie_apps.messages_app_port import LumaUIMessagesWindow  # noqa: E402
from prairie_apps.messages import MessagesWindow, install_messages_theme  # noqa: E402


def main() -> int:
    if os.environ.get("LUMA_MESSAGES_SMOKE_PHONE"):
        # The v71 phone uses a title island and grown composer panels. Keep
        # this older entry point on that contract, not the retired v70 header.
        from messages_v71_phone_runtime import main as phone_main
        return phone_main()
    assert os.environ.get("LUMA_MESSAGES_FIXTURE"), "set LUMA_MESSAGES_FIXTURE to the v70 JSON"
    fixture_path = Path(os.environ["LUMA_MESSAGES_FIXTURE"])
    fixture_bytes = fixture_path.read_bytes()
    app = Adw.Application(application_id="org.projectluma.Messages.PortSmoke",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    install_appkit()
    install_lumaui()
    install_messages_theme()
    window = FixtureMessagesWindow(app)
    phone = bool(os.environ.get("LUMA_MESSAGES_SMOKE_PHONE"))
    if phone:
        window.set_default_size(360, 820)
    else:
        window.set_default_size(1180, 740)
    window.present()
    for _ in range(10):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
    view = window.surface
    assert len(view._entries()) == 7
    people = _unique_participants((
        {"id": "one", "name": "Nicholas", "phone": "+1 (510) 555-0101"},
        {"id": "two", "name": "Nicholas", "phone": "+15105550101"},
        {"id": "three", "name": "Nicholas", "phone": "+15105550102"},
        {"name": "Alex"}, {"name": "Alex"},
    ))
    assert tuple(people) == ("one", "three", "row:3", "row:4"), people
    assert view.current and view.current["id"] == "crew"
    assert view.sidebar.get_name() == "msg-sidebar"
    assert view.center.state == "bar"
    assert view.history_clamp.get_maximum_size() == 760, "wide threads should keep a readable column"
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    adjustment = view.scroller.get_vadjustment()
    bottom = max(0, adjustment.get_upper() - adjustment.get_page_size())
    assert abs(adjustment.get_value() - bottom) <= 2, "thread did not settle at its final bottom"
    assert bottom > 200, "fixture needs enough history to test scroll retention"
    view.current["meta"]["messages"].append({"id": "same-day-again", "from": "me", "text": "Still today", "day": "Today"})
    view.render_thread()
    day_labels = []
    child = view.history.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label) and child.get_label() == "Today":
            day_labels.append(child)
        child = child.get_next_sibling()
    assert len(day_labels) == 1, f"same day was shown {len(day_labels)} times"
    view.current["meta"]["messages"].pop()
    view.render_thread()
    assert view._photos and all(photo.get_parent().__gtype_name__ == "LumaMessagesPhotoClip"
                                for photo in view._photos), "photo corners lack a rounded clip"
    # A programmatic adjustment reset can also come from GTK relayout; use the
    # same wheel intent as the reader before moving to an earlier message.
    controllers = view.scroller.observe_controllers()
    wheel = next(controllers.get_item(i) for i in range(controllers.get_n_items())
                 if isinstance(controllers.get_item(i), Gtk.EventControllerScroll))
    wheel.emit("scroll", 0.0, -1.0)
    adjustment.set_value(bottom / 3)
    retained = adjustment.get_value()
    view.render_thread()
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert abs(adjustment.get_value() - retained) < 40, \
        f"thread redraw moved reader from {retained} to {adjustment.get_value()}"
    view._scroll_to_end()
    if phone:
        view._phone_open_current()
        assert view.title_island.get_visible() and view.title_island.lead_button.get_visible(), \
            "phone conversation Back lead is hidden"
        assert view._phone_mode, "phone conversation sizing was not applied"
        assert view.history.get_margin_start() == view.history.get_margin_end() == 16
        assert all(column.get_maximum_size() == 300 for column in view._columns)
        assert all(picture.get_width() <= 266 for picture in view._photos)
    else:
        assert view.history.get_margin_start() == view.history.get_margin_end() == 26
    view._emoji_menu()
    assert view._emoji_pop.get_visible()
    view._emoji_pop.popdown()
    view._insert_emoji("🎉")
    assert view.bar_entry.text == "🎉"
    view.bar_entry.clear()
    view._attach()
    def named(widget, target):
        if widget.get_name() == target:
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = named(child, target)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None
    photo_bubble = named(view.history, "msg-bubble-5")
    reaction_flow = photo_bubble.get_parent()
    assert reaction_flow.get_height() == photo_bubble.get_allocated_height() + 18, \
        "reactions must contribute 18px below the photo instead of covering it"
    photo_row = named(view.history, "msg-message-5")
    assert photo_row.get_first_child().get_valign() == Gtk.Align.END
    link = named(view.history, "msg-link-11")
    assert link is not None and 150 < link.get_width() < 350, f"link card grew to {link.get_width()} px"
    assert 50 <= link.get_height() <= 60, \
        f"link card height: {link.get_height()} px"
    reply_bubble = named(view.history, "msg-bubble-10")
    assert reply_bubble is not None and 275 <= reply_bubble.get_allocated_width() <= (300 if phone else 330), \
        f"reply bubble width: {reply_bubble.get_allocated_width() if reply_bubble else 'missing'}"
    quote = named(reply_bubble, "msg-quote-10")
    assert quote is not None
    assert all(named(view.history, "msg-select-" + key) is not None
               for key in view._message_bubbles), "fixture activation targets are missing"
    quote_button = quote.get_parent()
    assert isinstance(quote_button, Gtk.Button)
    assert quote_button.get_accessible_role() == Gtk.AccessibleRole.BUTTON
    quote_origin = quote_button.translate_coordinates(reply_bubble, 0, 0)
    assert quote_origin is not None
    view._show_quick(reply_bubble, view._messages()[9], False,
                     quote_origin[0] + 4, quote_origin[1] + 4)
    assert view._quick_pop is None, "quote action acquired a duplicate hover toolbar"
    view._bubble_clicked(reply_bubble, view._messages()[9],
                         quote_origin[0] + 4, quote_origin[1] + 4)
    assert view.selected_message is None, "quote tap selected the enclosing message"
    link_bubble = named(view.history, "msg-bubble-11")
    if phone:
        assert 250 <= link_bubble.get_width() <= 275, f"phone link bubble width: {link_bubble.get_width()}"
        assert 118 <= link_bubble.get_allocated_height() <= 126, f"phone link bubble height: {link_bubble.get_height()}"
    else:
        assert 98 <= link_bubble.get_allocated_height() <= 106, f"desktop link bubble height: {link_bubble.get_height()}"
    assert link_bubble.get_first_child().get_first_child().get_next_sibling() is link
    before_jump = view.scroller.get_vadjustment().get_value()
    quote_button.emit("clicked")
    assert view._message_bubbles["7"].has_css_class("selected")
    if phone:
        assert view.scroller.get_vadjustment().get_value() < before_jump, "quote did not scroll to its source"
    selected_anchor = named(view.history, "msg-bubble-7")
    assert selected_anchor is not None
    view._show_quick(selected_anchor, view._messages()[6], False)
    assert view._quick_pop is not None and view._quick_pop.get_visible()
    view._dismiss_quick()
    view._search("no match xyz")
    assert named(view.sidebar, "msg-no-results") is not None
    view._search("homepage hero")
    assert tuple(view._rows) == ("crew",)
    view._search("")
    view.new_message()
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    assert view._new_pop.get_visible()
    picker_search = named(view._new_pop, "msg-new-search")
    assert picker_search is not None and picker_search.get_accessible_role() == Gtk.AccessibleRole.SEARCH_BOX, \
        f"picker role: {picker_search.get_accessible_role() if picker_search else 'missing'}"
    assert picker_search.get_icon_name(Gtk.EntryIconPosition.PRIMARY) == "lumaui-user-symbolic"
    assert named(view._new_pop, "msg-own-code") is not None
    chooser = named(view._new_pop, "msg-new-results")
    chooser_scroll = chooser.get_ancestor(Gtk.ScrolledWindow) if chooser is not None else None
    assert isinstance(chooser_scroll, Gtk.ScrolledWindow)
    assert chooser_scroll.get_max_content_height() == 5 * 48
    view._new_pop.popdown()
    view._pin()
    assert not view.current["pinned"] and not view.fixture.by_id["crew"]["pinned"]
    view._mute()
    assert view.current["muted"] and view.fixture.by_id["crew"]["muted"]
    view.details.open(view.current["id"])
    assert view.details.shown
    buttons = view.details.body.get_last_child()
    assert buttons.get_ancestor(Gtk.ScrolledWindow) is not None, "details actions must scroll with the content"
    assert buttons.has_css_class("lumaui-stacked-buttons")
    first_button = buttons.get_first_child()
    second_button = first_button.get_next_sibling()
    assert first_button.get_accessible_role() == second_button.get_accessible_role() == Gtk.AccessibleRole.BUTTON
    assert first_button.get_property("hexpand") and second_button.get_property("hexpand")
    assert second_button.get_next_sibling() is None, "bottom actions should be an equal pair"
    if not phone:
        for _ in range(5):
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
        main_x = view.host.compute_bounds(view.owner)[1].get_x() + view.host.get_width()
        details_x = view.details.sheet.compute_bounds(view.owner)[1].get_x()
        assert abs(details_x - main_x - 8) <= 1, f"details must have one shared gutter: {details_x - main_x}px"
        assert buttons.has_css_class("tile"), "details actions must use the shared tile presentation"
    view._select_message(view._messages()[6])
    assert view.center.state == "bar" and view.selected_message["id"] == 7
    selected_actions = []
    action = view.center.bar_row.get_first_child()
    while action is not None:
        selected_actions.append(getattr(action, "bar_item", None))
        action = action.get_next_sibling()
    assert all(getattr(action, "icon", None) != "copy" for action in selected_actions)
    if phone:
        assert all(action.label is None for action in selected_actions
                   if getattr(action, "icon", None) in {"reply", "smile"})
    view._react_popup()
    assert view._reaction_pop.get_visible()
    view._reaction_pop.popdown()
    view._select_message(view.selected_message)
    assert view.bar_entry is not None and view.bar_entry.widget is not None
    view.bar_entry.set_text("A draft in the bar")
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    view._scroll_to_end()
    before_editor_scroll = view.scroller.get_vadjustment().get_value()
    view.center.grow()
    assert view.center.state == "editor"
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert view.history.get_margin_bottom() == view.center.editor.get_height() + 30
    adjustment = view.scroller.get_vadjustment()
    advance = adjustment.get_value() - before_editor_scroll
    if phone:
        assert advance > 180, f"phone editor advanced only {advance} px"
        assert abs(adjustment.get_value() - max(0, adjustment.get_upper() - adjustment.get_page_size())) <= 2
    else:
        assert 55 <= advance <= 75, f"desktop editor advanced {advance} px"
    view._insert_emoji("🎉")
    assert view.center.editor.draft.endswith("🎉")
    view.center.editor.text_view.get_buffer().set_text("A draft in the bar")
    assert view.center.editor.draft == "A draft in the bar"
    view.center.editor.text_view.get_buffer().set_text("A draft in the editor")
    rich_buffer = view.center.editor.text_view.get_buffer()
    rich_buffer.select_range(rich_buffer.get_iter_at_offset(0), rich_buffer.get_iter_at_offset(7))
    view._format_draft("bold")
    html, markup = view._rich_draft()
    assert html == "<strong>A draft</strong> in the editor", html
    assert markup == "<b>A draft</b> in the editor", markup
    assert view.center.editor.draft == "A draft in the editor"
    view._remember_draft()
    assert view._rich_drafts["crew"][1], view._rich_drafts
    view.open("priya")
    view.open("crew")
    view.center.grow()
    assert view._rich_draft() == (html, markup), (view._rich_draft(), (html, markup), view._rich_drafts)
    view.center.fold()
    view.open("priya")
    view.open("crew")
    view.center.grow()
    assert view._rich_draft() == (html, markup), "folding lost the formatted draft"
    view.center.editor.text_view.get_buffer().set_text("A draft in the editor")
    view.center.fold()
    assert view.bar_entry.text == "A draft in the editor"
    view._remember_draft()
    assert view._drafts["crew"] == "A draft in the editor"
    for conversation in ("priya", "mom", "theo", "climb", "alex", "nora"):
        view.open(conversation)
        assert view.current["id"] == conversation and view.bar_entry is not None
    view.open("priya")
    assert view.bar_entry.placeholder == "Message Priya"
    view.open("crew")
    view.new_message()
    assert named(view._new_pop, "msg-own-code").activate()
    deadline = time.monotonic() + 1.1
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert view._code_pop.get_visible(), "own-code popover closed after the picker switched"
    assert named(view._code_pop, "msg-own-code-card") is not None
    view.current["request"] = True
    view._composer()
    assert view.bar_entry is None and view.center.bar_row.get_first_child() is not None
    view.current.pop("request")
    view._composer()
    view.bar_entry.set_text("Bold hello")
    view.center.grow()
    rich_buffer = view.center.editor.text_view.get_buffer()
    rich_buffer.select_range(rich_buffer.get_start_iter(), rich_buffer.get_iter_at_offset(4))
    view._format_draft("bold")
    view._send()
    rich_message = view.current["meta"]["messages"][-1]
    assert rich_message["text"] == "Bold hello"
    assert rich_message["html"] == "<strong>Bold</strong> hello"
    assert rich_message["markup"] == "<b>Bold</b> hello"
    view.bar_entry.set_text("one\ntwo")
    view.center.grow()
    list_buffer = view.center.editor.text_view.get_buffer()
    list_buffer.select_range(list_buffer.get_start_iter(), list_buffer.get_end_iter())
    view._format_draft("list")
    assert view.center.editor.draft == "• one\n• two"
    list_buffer.set_text("")
    view.center.fold()
    original_count = len(view.current["meta"]["messages"])
    view._send("Fixture-only hello")
    view._choose_attachment("photo")
    view._choose_attachment("file")
    view._share_location()
    added = view.current["meta"]["messages"][original_count:]
    assert [item.get("kind", "text") for item in added] == ["text", "photo", "file", "text"]
    assert added[-1]["text"] == "📍 Shared location · Oakland, CA"
    view.current["meta"]["typing"] = "PR"
    view.render_thread()
    assert named(view.history, "msg-typing") is not None
    view.current["meta"].pop("typing")
    view.render_thread()
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not any(
            item.get("text") == "Perfect, I’ll flag anything that looks off"
            for item in view.current["meta"]["messages"]):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert added[0]["status"] == "read"
    assert any(item.get("text") == "Perfect, I’ll flag anything that looks off"
               for item in view.current["meta"]["messages"])
    view.current["meta"]["messages"].append({"id": "empty-live-shape", "from": "me", "text": ""})
    view.render_thread()
    assert named(view.history, "msg-bubble-empty-live-shape") is not None
    part = SimpleNamespace(part="image0", noun="photo", state="failed", error="no_full_size",
                           size=3000, preview=fixture_path.parent / "nonexistent-preview", mime="image/jpeg")
    saved_fixture = view.fixture
    preview_path = saved_fixture.asset("messages-v70/life-hero-studio.webp")
    view.fixture = None
    retry_calls = []
    provider = SimpleNamespace(retry_media=lambda *args: retry_calls.append(args),
                               status={"network_state": "connected"}, capabilities={"media_fetch": True})
    view.current["service"] = SimpleNamespace(provider=provider)
    view.current["service"].label = "Google Messages"
    direct = {**view.current, "group": (), "is_group": False, "sms": False,
              "person": None, "person_data": {}}
    assert view._subtitle(direct) == "Google Messages"
    media_card = view._media_card({"id": "media-shape", "record": SimpleNamespace(uid="media")}, part)
    assert media_card.get_name() == "msg-media-media-shape-image0"
    assert media_card.has_css_class("messages-media-pending")
    assert "hasn't made" in media_card.get_first_child().get_next_sibling().get_label()
    photos_before = len(view._photos)
    image_card = view._media_card({"id": "media-preview", "record": SimpleNamespace(uid="media")},
                                  SimpleNamespace(**{**vars(part), "preview": preview_path}))
    assert image_card.get_name() == "msg-media-media-preview-image0"
    assert len(view._photos) == photos_before, "a failed transfer displayed a blurred preview"
    provider.status["network_state"] = "offline"
    view._retry_live_media(SimpleNamespace(uid="media"), part)
    assert not retry_calls, "offline retry was passed to a provider that cannot fetch"
    provider.status["network_state"] = "connected"
    view._retry_live_media(SimpleNamespace(uid="media"), part)
    assert retry_calls == [("media", "image0")]
    attachment_only = {"id": "attachment-only", "from": "me", "text": "", "record": SimpleNamespace(uid="media"),
                       "attachments": (), "media": (part,)}
    assert view._message_content(attachment_only) is not None, "attachment-only record became an empty bubble"
    view._composer()
    assert view.center.editor is None and view.bar_entry.widget.grow_button is None, \
        "live composition still offers the rich editor"
    view.fixture = saved_fixture
    assert fixture_path.read_bytes() == fixture_bytes, "fixture interactions wrote to disk"
    completed = []
    def unavailable_modem():
        raise GLib.Error.new_literal(GLib.quark_from_string("modem"), "no Messaging interface", 1)
    poller = SimpleNamespace(native_service=SimpleNamespace(transport=SimpleNamespace(snapshot=unavailable_modem)),
                             _poll_complete=lambda records: completed.append(records) or False)
    MessagesWindow._collect_received(poller)
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    assert completed == [()], "missing modem Messaging interface left the poll worker unfinished"
    window.close()
    live_shape = LumaUIMessagesWindow(app)
    live_shape.present()
    for _ in range(20):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
    assert live_shape.surface is not None
    assert live_shape.surface.current and live_shape.surface.current["id"] == "crew"
    assert live_shape.surface.bar_entry is not None
    priya = next(record for record in live_shape.store.threads() if record.address == "+15105550102")
    # A conversation click must draw the selected surface once, without also
    # rebuilding the hidden legacy conversation/list or briefly drawing the old one.
    from unittest.mock import patch
    with patch.object(MessagesWindow, "_render_messages", side_effect=AssertionError("hidden thread render")), \
            patch.object(MessagesWindow, "_reload_threads", side_effect=AssertionError("hidden sidebar render")), \
            patch.object(live_shape.surface, "render_thread", wraps=live_shape.surface.render_thread) as draw, \
            patch.object(live_shape.surface, "render_sidebar", wraps=live_shape.surface.render_sidebar) as sidebar:
        live_shape._open_thread(priya, reveal=True, service=live_shape.service)
        assert draw.call_count == 1, draw.call_count
        assert sidebar.call_count == 1, sidebar.call_count
    assert live_shape.surface.details.body.get_last_child().get_margin_top() == 16
    assert live_shape.surface.identity.who_button.has_css_class("lumaui-corner-people")

    assert live_shape.surface.current["id"] == "priya"
    assert live_shape.surface.bar_entry.placeholder == "Message Priya"
    live_shape.close()
    print("Messages LumaUI fixture runtime OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
