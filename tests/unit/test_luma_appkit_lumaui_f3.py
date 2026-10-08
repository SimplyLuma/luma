# SPDX-License-Identifier: Apache-2.0
"""LumaUI F3: the action and content parts.

The first classes read source and pure functions and run anywhere GTK's
introspection data is installed. `Parts` builds real widgets on stock GTK 4
and needs a display (it never presents a window); it is skipped otherwise.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
BASE = APPKIT / "luma-appkit-base.css"
TOKENS = json.loads((ROOT / "config/shared/design-tokens.json").read_text())["lumaui"]
GALLERY = ROOT / "src/luma-platform/tools/lumaui-gallery/lumaui_gallery.py"

F3_PARTS = ("Action center", "Selection bubble", "Open in", "File card", "Contact actions", "Mini cards",
            "Account card", "Cards on content-lit backgrounds", "Place search", "Field", "Message bubble",
            "Content-lit header", "Hero title field", "Status pill")
F3_EXPORTS = ("ActionCenter", "ActionEditor", "BarAction", "BarChip", "BarContext", "BarPrompt", "SEPARATOR", "SPACER",
              "SelectionBubble", "FileCard", "OpenButton", "OpenInMenu", "split_name", "ContactActions", "Person",
              "ContactCard", "EventCard", "SongCard", "PlaceCard", "AccountCard", "Card", "ContentLitCard",
              "PlaceSearch", "PlaceResult", "rank_places", "TextField", "MessageBubble", "ContentLitHeader",
              "HeroTitleField", "StatusPill", "PersonAvatar")

sys.path.insert(0, str(APPKIT))
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    HAVE_GTK = True
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_GTK = HAVE_DISPLAY = False


def _css() -> str:
    text = BASE.read_text()
    return text[text.index("/* ══ LumaUI F3"):]


class Sources(unittest.TestCase):
    def test_every_part_has_a_css_banner_and_is_exported(self):
        css = _css()
        for part in F3_PARTS:
            self.assertIn(f"/* LumaUI: {part} */", css)
        init = (KIT / "__init__.py").read_text()
        for name in F3_EXPORTS:
            self.assertIn(f'"{name}"', init, name)

    def test_reduced_motion_turns_growth_into_a_fade(self):
        css = _css()
        block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        for selector in ("box.lumaui-ac-editor", "box.lumaui-bubble", "box.lumaui-ac-bar.morph > box",
                         "box.lumaui-place-list", "box.lumaui-message"):
            self.assertIn(selector, block)
        self.assertIn("transform: none", block)

    def test_the_parts_use_tokens_not_numbers_for_their_signature_metrics(self):
        css = _css()
        for variable in ("--lumaui-ac-editor-width", "--lumaui-ac-bar-radius", "--lumaui-bubble-radius",
                         "--lumaui-file-face", "--lumaui-msg-join", "--lumaui-field-height", "--lumaui-place-row-height"):
            if variable == "--lumaui-ac-editor-width":  # a width the kit applies from Python
                self.assertEqual(TOKENS["action_center"]["editor_width"], 720)
                continue
            self.assertIn(f"var({variable})", css)

    def test_v70_numbers(self):
        ac, bubble, card = TOKENS["action_center"], TOKENS["selection_bubble"], TOKENS["file_card"]
        self.assertEqual((ac["editor_width"], ac["double_width"], ac["editor_radius"], ac["bar_radius"]), (720, 700, 20, 16))
        self.assertEqual((ac["text_min_height"], ac["text_max_pct"], ac["phone_radius"]), (132, 40, 26))
        self.assertEqual((bubble["offset"], bubble["edge"], bubble["inset"]), (10, 12, 8))
        self.assertEqual((card["face"], card["width"], card["height"]), (44, 300, 60))
        self.assertEqual((TOKENS["message_bubble"]["radius"], TOKENS["message_bubble"]["join"]), (18, 6))
        self.assertEqual((TOKENS["motion"]["grow_ms"], TOKENS["motion"]["search_debounce_ms"]), (320, 180))

    def test_a_disabled_stacked_button_stays_readable(self):
        css = BASE.read_text()
        rule = css[css.index("button.lumaui-stacked-button:disabled {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("opacity: 1", rule)
        self.assertIn("color: @luma_muted", rule)
        self.assertIn("background: @luma_hover", rule)

        def rgb(value):
            value = value.strip()
            if value.startswith("#"):
                return [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)], 1.0
            parts = [float(p) for p in value[value.index("(") + 1:-1].split(",")]
            return [p / 255 for p in parts[:3]], parts[3]

        def luminance(channels):
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for sheet in ("luma-appkit-tokens.css", "luma-appkit-dark-tokens.css"):
            text = (APPKIT / sheet).read_text()
            colour = lambda name: re.search(rf"@define-color {name} ([^;]+);", text).group(1)
            ground, _ = rgb(colour("luma_content"))
            fill, alpha = rgb(colour("luma_hover"))
            tile = [f * alpha + g * (1 - alpha) for f, g in zip(fill, ground)]
            ink, _ = rgb(colour("luma_muted"))
            a, b = luminance(ink), luminance(tile)
            ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            self.assertGreaterEqual(ratio, 3.0, f"{sheet}: disabled ink {ratio:.2f}:1")

    def test_gallery_has_a_page_for_every_f3_part(self):
        text = GALLERY.read_text()
        for key in ("parity", "bubble", "ac", "open", "psearch", "acct", "field", "bubblemsg", "hero"):
            self.assertRegex(text, rf'\("{key}", [^\n]*, None\)', key)
            self.assertIn(f'"{key}": page_', text)

    def test_contacts_actions_are_the_kits(self):
        # Promoted: the order and the rule that an action that can't work is disabled live in the kit now.
        code = (KIT / "content_contact.py").read_text()
        self.assertIn('("message", "message-square", "Message"), ("call", "phone", "Call"),', code)
        self.assertIn('("video", "video", "Video"), ("email", "mail", "Email")', code)


@unittest.skipUnless(HAVE_GTK, "needs GTK 4 introspection data")
class PureFunctions(unittest.TestCase):
    def test_split_name_keeps_the_extension(self):
        from luma_appkit.content_file import split_name
        # v70 lMid: the extension and up to five characters before it always show.
        self.assertEqual(split_name("press-release.write"), ("press-re", "lease.write"))
        self.assertEqual(split_name("launch-deck.stage"), ("launch", "-deck.stage"))
        self.assertEqual(split_name("README"), ("README", ""))
        head, tail = split_name("luma-1.0-nightly-x86_64.iso")
        self.assertTrue(tail.endswith(".iso") and head + tail == "luma-1.0-nightly-x86_64.iso")

    def test_rank_apps_puts_the_best_luma_app_first(self):
        from luma_appkit.content_file import rank_apps

        class App:
            def __init__(self, app_id, name):
                self.app_id, self.name = app_id, name

            def get_id(self):
                return self.app_id

            def get_name(self):
                return self.name

        apps = [App("gimp.desktop", "GIMP"), App("org.projectluma.Photos.desktop", "Photos"),
                App("org.projectluma.Installed.rpm-google-chrome-stable-8af1.desktop", "Google Chrome"),
                App("org.projectluma.Viewer.desktop", "Viewer"), App("org.projectluma.Canvas.desktop", "Canvas"),
                App("eog.desktop", "Image Viewer"), App("gimp.desktop", "GIMP")]
        ranked = [a.get_name() for a in rank_apps(apps, "org.projectluma.Viewer.desktop")]
        self.assertEqual(ranked, ["Viewer", "Canvas", "Photos", "GIMP", "Google Chrome", "Image Viewer"],
                         "a package Luma installed is not a Luma app")
        ranked = [a.get_name() for a in rank_apps(apps, "eog.desktop")]
        self.assertEqual(ranked, ["Canvas", "Photos", "Viewer", "Image Viewer", "GIMP", "Google Chrome"])

    def test_rank_places_starts_before_contains(self):
        from luma_appkit.content_place import PlaceResult, parse_coordinates, rank_places
        places = [PlaceResult("North Kansas City", "MO"), PlaceResult("Kansas City", "MO"), PlaceResult("Arkansas", "")]
        self.assertEqual([p.name for p in rank_places(places, "kansas")],
                         ["Kansas City", "North Kansas City", "Arkansas"])
        self.assertEqual(rank_places(places, "  "), [])
        self.assertEqual(len(rank_places(places, "a", limit=2)), 2)
        self.assertEqual(parse_coordinates("37.32, -122.03"), (37.32, -122.03))
        self.assertIsNone(parse_coordinates("91, 0"))
        self.assertIsNone(parse_coordinates("Kansas"))

    def test_contact_links(self):
        from luma_appkit.content_contact import Person, contact_uri
        priya = Person("Priya Raman", phone="+1 (555) 010-1", email="priya@example.com", username="priya")
        self.assertEqual(contact_uri("call", priya), "tel:+15550101")
        self.assertEqual(contact_uri("message", priya), "sms:+15550101")
        self.assertEqual(contact_uri("email", priya), "mailto:priya@example.com")
        self.assertEqual(contact_uri("video", priya), "")
        self.assertEqual(contact_uri("email", Person("Nora")), "")
        with self.assertRaises(ValueError):
            contact_uri("fax", priya)

    def test_bar_actions_refuse_to_be_unnamed(self):
        from luma_appkit.action_center import BarAction
        with self.assertRaises(ValueError):
            BarAction("bold")
        self.assertEqual(BarAction("bold", tooltip="Bold").tooltip, "Bold")


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class Parts(unittest.TestCase):
    def setUp(self):
        from luma_appkit import action_bubble, action_center, action_toast, content_cards, content_contact, \
            content_field, content_file, content_message, content_place, lumaui, structure_layers
        self.lumaui = lumaui
        self.ac, self.bubble, self.toast = action_center, action_bubble, action_toast
        self.cards, self.contact, self.field = content_cards, content_contact, content_field
        self.file, self.message, self.place, self.layers = content_file, content_message, content_place, structure_layers
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def _window(self, child, width=1000, height=700):
        window = Gtk.Window()
        window.set_default_size(width, height)
        window.set_child(child)
        self.windows.append(window)
        return window

    @staticmethod
    def _spin(ms=60):
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, loop.quit)
        loop.run()

    # ── action center ─────────────────────────────────────────────────────

    def _center(self, sent=None):
        ac = self.ac
        host = self.toast.ToastHost(Gtk.Box())
        self._window(host)
        editor = ac.ActionEditor("Reply all", "reply-all", summary="to {}", summary_emphasis="Priya, Nora",
                                 modes=[("one", "Reply", "reply"), ("all", "Reply all", "reply-all")], mode="all",
                                 tools=[ac.BarAction("bold", tooltip="Bold"), ac.SEPARATOR, ac.SPACER],
                                 placeholder="Write your reply",
                                 primary=ac.BarAction("send-horizontal", "Send",
                                                      on_activate=lambda: sent.append(1) if sent is not None else None))
        center = ac.ActionCenter(editor).attach(host)
        return center, host, editor

    def test_action_center_states_and_layout(self):
        ac = self.ac
        center, host, _editor = self._center()
        states = []
        center.connect("state-changed", lambda _c, s: states.append(s))
        self.assertIn(center, host._bars, "toasts clear the bar")
        self.assertEqual(center.state, "hidden")
        center.show_bar([ac.BarAction("share-2", "Share"), ac.BarAction("trash-2", tooltip="Delete", danger=True)])
        self.assertEqual(center.state, "bar")
        self.assertEqual(center.layout(1000), (False, -1, 20, 24), "a plain bar is as wide as its actions")
        center.show_bar([ac.BarPrompt("Reply to Priya and Nora…")])
        self.assertEqual(center.layout(1000), (False, 700, 20, 24))
        center.show_bar([ac.BarPrompt("Reply…")], context=ac.BarContext("reply", "Replying to {}’s message", "Priya"))
        self.assertEqual(center.state, "double")
        self.assertEqual(center.layout(375), (True, 351, 20, 34),
                         "v71: on a phone the bar spans the width less 12 a side, 34 above the foot")
        center.grow()
        self.assertEqual(center.state, "editor")
        self.assertEqual(center.layout(1000), (False, 720, 20, 20))
        self.assertEqual(center.layout(700), (False, 652, 20, 20))
        self.assertEqual(center.layout(375), (True, 343, 60, 34),
                         "v71 rule 15: on a phone the editor is the bar grown: 16 a side, 34 up")
        center.show_split([ac.BarChip("3 photos", icon="image")], [ac.BarAction("heart", "Favourite")])
        self.assertEqual(center.state, "split")
        self.assertEqual(states, ["bar", "bar", "double", "editor", "split"])
        with self.assertRaises(TypeError):
            ac.make_control(ac.BarPrompt("x"))

    def test_esc_folds_and_keeps_the_draft(self):
        ac = self.ac
        sent = []
        center, _host, editor = self._center(sent)
        center.show_bar([ac.BarPrompt("Reply to Priya and Nora…")])
        center.prompt_button.emit("clicked")
        self.assertEqual(center.state, "editor", "the prompt grows into the editor")
        editor.text_view.get_buffer().set_text("Looks great.\nOne note on slide 4.")
        self.assertTrue(center._editor_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0)))
        self.assertEqual(center.state, "bar")
        self.assertEqual(center.prompt_button.prompt_label.get_label(), "Draft: Looks great. One note on slide 4.")
        center.grow()
        self.assertEqual(editor.draft, "Looks great.\nOne note on slide 4.", "growing again restores the draft")
        self.assertTrue(center._editor_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType.CONTROL_MASK))
        self.assertEqual(sent, [1], "Ctrl+Return sends")
        center.discard()
        self.assertEqual((center.state, editor.draft), ("bar", ""))
        self.assertEqual(center.prompt_button.prompt_label.get_label(), "Reply to Priya and Nora…")

    def test_editor_is_keyboard_complete_and_named(self):
        _center, _host, editor = self._center()
        self.assertEqual(editor.mode_button.get_accessible_role(), Gtk.AccessibleRole.BUTTON)
        self.assertTrue(editor.mode_button.get_can_focus())
        self.assertEqual(editor.discard_button.get_tooltip_text(), "Discard")
        editor.set_mode("one")
        self.assertEqual((editor.mode, editor.title), ("one", "Reply"))
        with self.assertRaises(KeyError):
            editor.set_mode("fax")
        fields = self.ac.ActionEditor("New event", "calendar", fields=[("Title", "Launch rehearsal")],
                                      body=Gtk.Box(), primary=self.ac.BarAction("check", "Add"))
        self.assertIsInstance(fields.field("Title"), Gtk.Entry)
        self.assertIsNone(fields.text_view, "a custom body replaces the composer")

    # ── selection bubble ──────────────────────────────────────────────────

    def test_bubble_floats_over_the_selection_and_flips(self):
        view = Gtk.TextView()
        view.get_buffer().set_text("Freeze strings on the 10th. Launch day is October 1.")
        host = self.layers.LayerHost(view)
        window = self._window(host)
        window.present()
        self._spin(150)
        bubble = self.bubble.SelectionBubble(view, [self.ac.BarAction("bold", tooltip="Bold"), self.ac.SEPARATOR,
                                                    self.ac.BarAction("link", tooltip="Link")])
        self.assertEqual(bubble.get_accessible_role(), Gtk.AccessibleRole.TOOLBAR)
        buffer = view.get_buffer()
        buffer.select_range(buffer.get_iter_at_offset(7), buffer.get_iter_at_offset(14))
        bubble._settled()
        self.assertIs(bubble.get_parent(), host)
        self.assertEqual(bubble.side, "below", "text at the very top flips the bubble below it")
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 200, 300, 80, 18
        bubble.show_for(rect)
        self.assertEqual(bubble.side, "above")
        height = bubble.measure(Gtk.Orientation.VERTICAL, -1)[1] - bubble.get_margin_top()
        self.assertEqual(bubble.get_margin_top(), 300 - height - 10, "10 above the selection")
        bubble.set_active("bold", True)
        self.assertTrue(bubble._buttons["bold"].has_css_class("on"))
        buffer.insert_at_cursor("x")
        self.assertFalse(bubble.get_visible(), "typing hides it")
        with self.assertRaises(ValueError):
            self.bubble.SelectionBubble(view, [])

    def test_floating_stays_inside_the_host(self):
        box = Gtk.Label(label="A floating thing")
        host = self.layers.LayerHost(Gtk.Box())
        window = self._window(host, 500, 400)
        window.present()
        self._spin(150)
        host.add_overlay(box)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 200, 10, 10
        self.bubble.float_at(host, box, rect)
        self.assertGreaterEqual(box.get_margin_start(), 8)
        rect.x = host.get_width() - 5
        self.bubble.float_at(host, box, rect)
        width = box.measure(Gtk.Orientation.HORIZONTAL, -1)[1] - box.get_margin_start()
        self.assertLessEqual(box.get_margin_start() + width, host.get_width() - 8)

    # ── files ─────────────────────────────────────────────────────────────

    def test_file_sizes_and_kinds_are_v71s(self):
        from luma_appkit.content_file import file_kind, file_size
        self.assertEqual([file_size(n) for n in (412_000, 18_400_000, 96_000, 1_200_000, 2_000_000, 640)],
                         ["412 KB", "18.4 MB", "96 KB", "1.2 MB", "2 MB", "640 bytes"])
        self.assertEqual(file_kind("application/x-luma-stage", "deck.stage"), "Stage presentation")
        self.assertEqual(file_kind("application/octet-stream", "launch-walkthrough-v3.stage"), "Stage presentation")
        self.assertEqual(file_kind("application/pdf", "milestones.pdf"), "PDF document")
        self.assertEqual(file_kind("image/png", "crop.png"), "Image")
        self.assertEqual(file_kind("application/x-never-registered", "blob"), "File")

    def test_file_card(self):
        File = self.file.FileCard
        card = File("/nonexistent/quarterly-press-release.write", size=24_000, kind="Write document",
                    content_type="text/plain", on_open=lambda: None)
        self.assertEqual(card.name_tail.get_label(), "lease.write")
        self.assertEqual(card.name_head.get_ellipsize(), 3)  # END
        self.assertIn("Write document", card.second_line.get_label())
        self.assertIsNotNone(card.open_button)
        big = Gdk.MemoryTexture.new(400, 300, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(400 * 300 * 4)), 1600)
        photo = File("/nonexistent/terrace.webp", content_type="image/webp", thumbnail=big)
        self.assertTrue(photo.face.get_visible())
        self.assertEqual(photo.face.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 44, "never a giant image")
        self.assertEqual(photo.face.measure(Gtk.Orientation.VERTICAL, -1)[1], 44)
        busy = File("/nonexistent/a.iso", actions=[self.ac.BarAction("pause", tooltip="Pause")], compact=True,
                    subtitle="Failed", tone="danger")
        self.assertIsNone(busy.open_button)
        self.assertTrue(busy.has_css_class("compact") and busy.second_line.has_css_class("danger"))
        with self.assertRaises(ValueError):
            File("/nonexistent/a.iso", tone="purple")

    def test_open_button_and_menu(self):
        button = self.file.OpenButton(content_type="text/plain", on_open=lambda: None)
        self.assertTrue(button.get_tooltip_text().startswith("Open"))
        if button.app is not None:
            self.assertEqual(button.get_tooltip_text(), f"Open in {button.app.get_name()}")
        menu = self.file.OpenInMenu("/nonexistent/notes.txt")
        rows = menu.rows()
        self.assertEqual(rows[0], "Open in")
        self.assertEqual(rows[-1].label, "Other app…")
        self.assertIsNone(rows[-2], "a separator before Other app")
        defaults = [r for r in rows[1:-2] if r.note == "Default"]
        self.assertLessEqual(len(defaults), 1)
        only_type = self.file.OpenInMenu(content_type="text/plain")
        self.assertNotIn("Other app…", [getattr(r, "label", None) for r in only_type.rows()])

    # ── people and things ─────────────────────────────────────────────────

    def test_contact_actions(self):
        Person, Actions = self.contact.Person, self.contact.ContactActions
        nora = Person("Nora Feld", phone="+1 555 0102")
        row = Actions(nora)
        labels = [b.get_accessible_role() and b.get_child().get_last_child().get_label() for b in row.buttons.values()]
        self.assertEqual(labels, ["Message", "Call", "Video", "Email"])
        self.assertEqual([b.get_sensitive() for b in row.buttons.values()], [True, True, False, False],
                         "what can't work is disabled")
        seen = []
        priya = Person("Priya", username="priya", email="p@example.com")
        handled = Actions(priya, small=True, handler=lambda a, p: seen.append((a, p.name)) or True)
        self.assertTrue(all(b.get_sensitive() for b in handled.buttons.values()), "the app handles them in place")
        self.assertTrue(handled.activate_action("video"))
        self.assertEqual(seen, [("video", "Priya")])
        self.assertTrue(handled.has_css_class("lumaui-stack-small"))

    def test_mini_cards(self):
        Person = self.contact.Person
        card = self.contact.ContactCard(Person("Priya Raman", username="priya"))
        self.assertIsInstance(card.actions, self.contact.ContactActions)
        event = self.contact.EventCard("Launch rehearsal", datetime(2026, 9, 29, 10, 0), where="Studio", tone="play",
                                       on_add=lambda: None)
        self.assertTrue(event.has_css_class("event"))
        with self.assertRaises(ValueError):
            self.contact.EventCard("x", datetime(2026, 9, 29), tone="purple")
        song = self.contact.SongCard("God Only Knows", "The Beach Boys", "Pet Sounds")
        place = self.contact.PlaceCard("Duende", "468 19th St", eta="9 min", icon="utensils")
        for mini in (card, event, song, place):
            self.assertTrue(mini.has_css_class("lumaui-mini-card"))

    def test_event_card_matches_v71(self):
        # v71 `.lmini.event` (Messages' run, hue 285) is border-box: 300 x 60 with the padding in it, the date
        # tile in the calendar's hue (rgba(58, 55, 96, .75) in dark). The conform scenario rows-message-run
        # measures the drawn card; here the numbers that make it (a host's GTK may not apply the sheets).
        from luma_appkit import lumaui

        event = self.contact.EventCard("Launch walkthrough", datetime(2026, 9, 25, 14, 0), where="Studio", hue=285,
                                       on_add=lambda: None)
        self.assertEqual(event.get_layout_manager().width + 8 + 10, 300, "the card's width, padding included")
        self.assertEqual(self.contact.ContactCard(self.contact.Person("Priya Raman", username="priya"))
                         .get_layout_manager().width + 12 + 12, 320)
        self.assertIn("min-height: calc(var(--lumaui-mini-min-height) - 16px); padding: 8px 10px 8px 8px;",
                      BASE.read_text(), "a 60 card: the minimum height is the border box")
        tile = event.row.get_first_child().get_first_child()
        self.assertTrue(tile.has_css_class("lumaui-hue-285"))
        self.assertFalse(tile.has_css_class("work"), "a hue replaces the category tone")
        self.assertIn("box.lumaui-event-tile.lumaui-hue-285 { background: rgba(58, 55, 96, 0.75); "
                      "box-shadow: inset 3px 0 0 rgba(157, 152, 242, 1); }", lumaui._hue_css({285}, True))
        self.assertIn("box.lumaui-event-tile.lumaui-hue-285 { background: rgba(", lumaui._hue_css({285}, False))
        self.assertTrue(self.contact.EventCard("x", datetime(2026, 9, 29)).row.get_first_child()
                        .get_first_child().has_css_class("work"), "no hue: the tone, as before")

    def test_person_avatar_is_its_size_not_its_initials(self):
        for size in (116, 44, 34):
            face = self.cards.PersonAvatar("Priya Raman", size)
            box = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.START)
            box.append(face)
            window = self._window(box, 400, 300)
            window.present()
            self._spin(120)
            self.assertEqual((face.get_width(), face.get_height()), (size, size))
            label = face.get_first_child()
            self.assertEqual((label.get_width(), label.get_height()), (size, size), "the initials fill the round face")

    def test_cards_and_account(self):
        account = self.cards.AccountCard("Nick")
        self.assertTrue(account.has_css_class("lumaui-account-card"))
        self.assertEqual(self.cards.initials("Priya Raman"), "PR")
        self.assertEqual(self.cards.initials("(555) 010"), "")
        lit = self.cards.ContentLitCard(Gtk.Label(label="80%"))
        self.assertTrue(lit.has_css_class("lumaui-lit-card") and not lit.has_css_class("lumaui-card"))
        self.assertTrue(self.cards.Card().has_css_class("lumaui-card"))

    # ── place search ──────────────────────────────────────────────────────

    def test_place_search(self):
        Result, rank = self.place.PlaceResult, self.place.rank_places
        places = [Result(n, r) for n, r in (("Kansas City", "MO"), ("Kansas City", "KS"), ("North Kansas City", "MO"),
                                             ("Arkansas City", "KS"), ("Kansas", "OK"), ("Kansasville", "WI"),
                                             ("Oakland", "CA"))]
        picked = []
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END)
        search = self.place.PlaceSearch(lambda q: rank(places, q), on_pick=picked.append,
                                        exclude=lambda p: p.name == "Kansas")
        side.append(search)
        host = self.layers.LayerHost(side)
        window = self._window(host, 400, 500)
        window.present()
        self._spin(150)
        search.set_text("kansas")
        self.assertTrue(search._debounce, "waits for a pause in typing")
        search.search_now()
        self.assertTrue(search.searching, "the source is asked off the main thread")
        self._spin(150)
        self.assertEqual(len(search.results), 5)
        self.assertNotIn("Kansas", [p.name for p in search.results], "places already added are left out")
        self.assertTrue(search.list_shown)
        self.assertIs(search.list.get_parent(), host)
        self.assertEqual(search.list.get_accessible_role(), Gtk.AccessibleRole.LIST_BOX)
        self.assertTrue(search._key(None, Gdk.KEY_Down, 0, Gdk.ModifierType(0)))
        self.assertEqual(search.selected, 1)
        self.assertTrue(search._key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0)))
        self.assertEqual((picked[0].name, picked[0].subtitle), ("Kansas City", "KS"))
        self.assertEqual(search.text, "")
        self.assertFalse(search.list_shown)
        search.set_text("Atlantis")
        search.search_now()
        self._spin(150)
        none = search.list.get_first_child()
        self.assertEqual(none.get_label(), self.place.NO_PLACES)
        self.assertFalse(search.pick(), "Enter with no match adds nothing")
        self.assertTrue(search._key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0)))
        self.assertFalse(search.list_shown)
        self.assertTrue(search._key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0)))
        self.assertEqual(search.text, "")
        search.set_text("oak")
        self.assertTrue(search._key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0)), "Enter before the pause adds the top match")
        self._spin(150)
        self.assertEqual(picked[-1].name, "Oakland")

    def test_place_search_failure_is_distinct_and_stale_failure_is_ignored(self):
        def broken(_query):
            raise OSError("offline")
        search = self.place.PlaceSearch(broken, on_pick=lambda _place: None)
        window = self._window(self.layers.LayerHost(search), 400, 500)
        window.present()
        self._spin(100)
        search.set_text("60601")
        search.search_now()
        generation = search._generation
        self._spin(200)
        self.assertEqual(search.list.get_first_child().get_label(), self.place.SEARCH_ERROR)
        self.assertFalse(search.searching)
        self.assertFalse(search.pick())
        search.provider = lambda _query: [self.place.PlaceResult("Chicago")]
        search.set_text("Chicago")
        search.search_now()
        self._spin(200)
        search._failed(generation, "60601")
        self.assertEqual([place.name for place in search.results], ["Chicago"])
        self.assertIsNone(search._error)

    def test_place_search_outside_press_dismisses_without_consuming_action(self):
        search = self.place.PlaceSearch(None, on_pick=lambda _place: None)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        action = Gtk.Button(label="Add city")
        column.append(action)
        column.append(search)
        window = self._window(self.layers.LayerHost(column), 400, 500)
        window.present()
        self._spin(150)
        search.set_text("Chicago")
        search.search_now()
        search.set_results("Chicago", [self.place.PlaceResult("Chicago")])
        self._spin(100)
        self.assertTrue(search.list_shown)
        search._outside_pressed(None, 1, 399, 0)
        self.assertFalse(search.list_shown)
        self.assertIsNone(search._dismiss_controller)
        activated = []
        action.connect("clicked", lambda _button: activated.append(True))
        action.emit("clicked")
        self.assertEqual(activated, [True])

    def test_place_search_real_outside_click_activates_the_other_control(self):
        """Dispatch the pointer through GTK, including capture/bubble and focus."""
        if not shutil.which("xdotool"):
            self.skipTest("real X11 input requires xdotool")
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
        except (ImportError, ValueError):
            self.skipTest("real X11 input requires GdkX11")
        search = self.place.PlaceSearch(None, on_pick=lambda _place: None)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        action = Gtk.Button(label="Add city", focus_on_click=False)
        activated = []
        action.connect("clicked", lambda _button: activated.append(True))
        column.append(action)
        column.append(Gtk.Box(vexpand=True))
        column.append(search)
        window = self._window(self.layers.LayerHost(column), 400, 500)
        window.present()
        self._spin(150)
        surface = window.get_surface()
        if not isinstance(surface, GdkX11.X11Surface):
            self.skipTest("real X11 input requires an X11 display")
        search.set_text("Chicago")
        search.search_now()
        search.set_results("Chicago", [self.place.PlaceResult("Chicago")])
        self._spin(150)
        self.assertTrue(search.list_shown)
        ok, bounds = action.compute_bounds(window)
        self.assertTrue(ok)
        # Widget allocations exclude the transparent CSD shadow margin.
        x = bounds.get_x() + bounds.get_width() / 2 + (surface.get_width() - window.get_width()) / 2
        y = bounds.get_y() + bounds.get_height() / 2 + (surface.get_height() - window.get_height()) / 2
        subprocess.run(["xdotool", "windowfocus", str(surface.get_xid()), "mousemove", "--window",
                        str(surface.get_xid()), str(round(x)), str(round(y))], check=True, timeout=5)
        self._spin(100)
        subprocess.run(["xdotool", "mousedown", "1"], check=True, timeout=5)
        self._spin(100)
        subprocess.run(["xdotool", "mouseup", "1"], check=True, timeout=5)
        self._spin(150)
        self.assertFalse(search.list_shown)
        self.assertEqual(activated, [True], "the dismissing click must also reach Add city")

    def test_place_search_answered_by_the_app(self):
        # KB2: an asynchronous source answers the `search` signal with set_results(); stale answers are dropped.
        Result = self.place.PlaceResult
        picked, asked = [], []
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END)
        search = self.place.PlaceSearch(None, on_pick=picked.append)
        search.connect("search", lambda _s, query: asked.append(query))
        side.append(search)
        host = self.layers.LayerHost(side)
        window = self._window(host, 400, 500)
        window.present()
        self._spin(150)
        search.set_text("par")
        search.search_now()
        search.set_text("paris")
        search.search_now()
        self.assertEqual(asked, ["par", "paris"])
        self.assertFalse(search.set_results("par", [Result("Parma", "IT")]), "an answer for an old query is ignored")
        self.assertTrue(search._key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0)), "Enter waits for the answer")
        self.assertEqual(picked, [])
        self.assertTrue(search.set_results("paris", [Result("Paris", "FR")]))
        self.assertEqual([p.name for p in picked], ["Paris"])
        search.set_text("x")
        search.search_now()
        search.set_text("")
        self.assertFalse(search.set_results("x", [Result("Xi'an", "CN")]), "clearing the field cancels the search")
        from luma_appkit.structure_sidebar import SidebarFoot
        field = self.place.PlaceSearch(lambda q: [], on_pick=picked.append)
        foot = SidebarFoot(search=field, add=("Add city", "plus", lambda: None))
        self.assertIs(foot.field, field)
        self.assertIs(foot.entry, field.entry)
        self.assertIs(field.get_parent(), foot)

    # ── field and message ─────────────────────────────────────────────────

    def test_text_field(self):
        Field = self.field.TextField
        field = Field("Username", value="nick", hint="Luma signs in with a token.")
        self.assertEqual(field.text, "nick")
        self.assertTrue(field.hint.get_visible())
        field.text = "nora"
        self.assertEqual(field.entry.get_text(), "nora")
        field.set_hint(None)
        self.assertFalse(field.hint.get_visible())
        self.assertEqual(Field("PIN", purpose="pin").entry.get_visibility(), False)
        with self.assertRaises(ValueError):
            Field("", value="x")
        with self.assertRaises(ValueError):
            Field("Age", purpose="age")

    def test_message_bubble(self):
        Bubble = self.message.MessageBubble
        mine = Bubble("Works for me.", mine=True, joined_below=True)
        self.assertTrue(mine.has_css_class("mine") and mine.has_css_class("join-below"))
        self.assertEqual(mine.get_halign(), Gtk.Align.END)
        theirs = Bubble("Freeze strings?")
        self.assertTrue(theirs.has_css_class("theirs") and not theirs.has_css_class("join-above"))
        theirs.set_joins(True, False)
        self.assertTrue(theirs.has_css_class("join-above"))
        with self.assertRaises(ValueError):
            Bubble("x", child=Gtk.Box())

    # ── Contacts' hero ────────────────────────────────────────────────────

    def test_content_lit_header(self):
        Header = self.cards.ContentLitHeader
        header = Header.for_person(self.contact.Person("Priya Raman"))
        # The wash wears the person's hue (v70 .mwash), from the name when none is given.
        self.assertEqual(header.tone, f"lumaui-hue-{self.lumaui.person_hue('Priya Raman')}")
        self.assertTrue(header.wash.has_css_class(header.tone))
        header.set_source(name="Priya Raman", hue=330)
        self.assertTrue(header.wash.has_css_class("lumaui-hue-330"))
        self.assertFalse(header.wash.has_css_class(f"lumaui-hue-{self.lumaui.person_hue('Priya Raman')}"))
        self.assertFalse(header.picture.get_visible(), "no photo, only the wash")
        self.assertFalse(header.get_can_target(), "decoration takes no input")
        self.assertEqual(header.measure(Gtk.Orientation.VERTICAL, -1)[1], 380)
        photo = Gdk.MemoryTexture.new(8, 8, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(8 * 8 * 4)), 32)
        header.set_source(picture=photo, tone="media")
        self.assertTrue(header.picture.get_visible() and header.wash.has_css_class("media"))
        self.assertFalse(header.wash.has_css_class("lumaui-hue-330"), "a tone replaces the hue")
        window = self._window(header, 600, 400)
        window.present()
        self._spin(120)
        paintable = Gtk.WidgetPaintable.new(header)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, 600, 380)
        self.assertIsNotNone(snapshot.to_node(), "the blurred light renders")
        with self.assertRaises(ValueError):
            header.set_source(tone="purple")

    def test_hero_title_field(self):
        field = self.field.HeroTitleField("Priya Raman", on_commit=lambda text: committed.append(text))
        committed = []
        self.assertTrue(field.has_css_class("lumaui-t-hero") and field.has_css_class("editable"))
        field.set_text("Priya R.")
        field.commit()
        self.assertEqual(committed, ["Priya R."])
        field.set_text("oops")
        self.assertTrue(field._key(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(field.text, "Priya R.", "Esc puts back what was there")
        field.set_editable_title(False)
        self.assertFalse(field.get_can_focus() or field.has_css_class("editable"))

    def test_status_pill(self):
        Pill = self.contact.StatusPill
        pill = Pill("on-luma")
        self.assertEqual((pill.label.get_label(), pill.tone), ("On Luma", "good"))
        self.assertTrue(pill.dot.get_visible())
        pill.set_kind("not-on-luma")
        self.assertFalse(pill.dot.get_visible() or pill.has_css_class("good"))
        self.assertEqual(Pill("syncing", "Syncing 214 songs").label.get_label(), "Syncing 214 songs")
        with self.assertRaises(ValueError):
            Pill("sleeping")


if __name__ == "__main__":
    unittest.main()
