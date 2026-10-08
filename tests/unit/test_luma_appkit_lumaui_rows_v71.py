# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows, v71 (K-ROWS): SwipeRow, AZIndex, message runs and the phone file picker.

`Sources` runs anywhere; `Parts` builds real widgets on stock GTK 4 with the
kit's sheets loaded and checks them against luma-next-71.html's numbers
(skipped without a display).
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
SHEET = APPKIT / "luma-appkit-rows.css"
FRAGMENT = json.loads((ROOT / "config/shared/design-tokens.d/rows.json").read_text())["lumaui"]

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

EXPORTS = ("SWIPE_COLORS", "SwipeAction", "SwipeRow", "AZ_LETTERS", "AZIndex", "MessageRun", "run_corners", "FILE_KINDS", "FILE_PLACES", "FileRequest", "ask_for_file", "file_dialog", "outcome_text")


class Sources(unittest.TestCase):
    def test_banners_and_exports(self):
        css = SHEET.read_text()
        for part in ("Swipe row", "A-Z index", "Message run"):
            self.assertRegex(css, rf"/\* LumaUI: {re.escape(part)} \(v71\)", part)
        init = (KIT / "__init__.py").read_text()
        block = init[init.index("# ── LumaUI rows (KB-A)"):]
        for name in EXPORTS:
            self.assertIn(f'"{name}"', block, name)

    def test_details_photos_take_columns(self):
        """messages-04 (2): DetailsPhotos(items, columns=4); structure_details needs the Luma typelibs, so read it."""
        import ast
        tree = ast.parse((KIT / "structure_details.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DetailsPhotos")
        init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        self.assertIn("columns", [a.arg for a in init.args.kwonlyargs])
        self.assertIn("index % self.columns", (KIT / "structure_details.py").read_text())

    def test_tokens_are_v71s(self):
        self.assertEqual(FRAGMENT["swipe_row"]["commit"], 90)
        self.assertEqual(FRAGMENT["swipe_row"]["radius"], 14)
        self.assertEqual(FRAGMENT["az_index"]["gutter"], 26)
        self.assertEqual(FRAGMENT["az_index"]["width"], 20)
        self.assertEqual(FRAGMENT["message_run"], {**FRAGMENT["message_run"], "radius": 18, "join": 5, "gap": 2})
        for colour in ("green", "orange", "blue", "red", "gray", "yellow"):
            self.assertIn(f"swipe_{colour}", FRAGMENT["colors"]["dark"])

    def test_run_corners_follow_v71_mshape(self):
        if not HAVE_GTK:
            self.skipTest("no GTK")
        from luma_appkit import run_corners
        # theirs: (tl, tr, br, bl); the sender's side is the left
        self.assertEqual(run_corners([272, 300], mine=False), [(False, False, True, True), (True, False, False, False)])
        # a wider bubble above a narrower one: the narrow one's top far corner joins, the wide one's bottom stays round
        self.assertEqual(run_corners([300, 200], mine=False), [(False, False, False, True), (True, True, False, False)])
        # within 3 px counts as as wide
        self.assertEqual(run_corners([200, 197], mine=False)[0], (False, False, True, True))
        self.assertEqual(run_corners([200, 196], mine=False)[0], (False, False, False, True))
        # yours: the sender's side is the right
        self.assertEqual(run_corners([180, 240, 120], mine=True),
                         [(False, False, True, True), (False, True, True, False), (True, True, False, False)])
        self.assertEqual(run_corners([150], mine=True), [(False, False, False, False)])
        # right to left mirrors the sides
        self.assertEqual(run_corners([272, 300], mine=False, rtl=True), [(False, False, True, True), (False, True, False, False)])

    def test_file_request_becomes_the_portal_request(self):
        if not HAVE_GTK:
            self.skipTest("no GTK")
        from luma_appkit import FileRequest, file_dialog
        photos = FileRequest("Add photos", start="pictures", show="images", many=True)
        dialog = file_dialog(photos)
        self.assertEqual(dialog.get_title(), "Add photos")
        self.assertEqual(dialog.get_accept_label(), "Open")
        self.assertEqual(dialog.get_default_filter().get_name(), "Images")
        self.assertEqual(dialog.get_filters().get_n_items(), 2, "Images, then All files")
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures:
            self.assertEqual(dialog.get_initial_folder().get_path(), pictures)
        save = file_dialog(FileRequest("Save a copy", mode="save", start="documents", name="Launch notes copy.md"))
        self.assertEqual((save.get_accept_label(), save.get_initial_name()), ("Save", "Launch notes copy.md"))
        self.assertIsNone(save.get_filters())
        default_save = file_dialog(FileRequest("Save a copy", mode="save"))
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        expected = downloads or str(Path(GLib.get_home_dir()) / "Downloads")
        self.assertEqual(default_save.get_initial_folder().get_path(), expected)
        explicit = file_dialog(FileRequest("Save elsewhere", mode="save", start=Path("/tmp")))
        self.assertEqual(explicit.get_initial_folder().get_path(), "/tmp")
        self.assertIsNone(file_dialog(FileRequest("Open a file")).get_initial_folder())
        self.assertEqual(file_dialog(FileRequest("Add a folder", mode="folder")).get_accept_label(), "Choose")
        with self.assertRaises(ValueError):
            FileRequest("Add photos", start="attic")
        with self.assertRaises(ValueError):
            FileRequest("Add photos", mode="pick")

    def test_outcome_words_are_v71s(self):
        if not HAVE_GTK:
            self.skipTest("no GTK")
        from gi.repository import Gio
        from luma_appkit import FileRequest, outcome_text
        GLib.set_application_name("Photos")
        f = Gio.File.new_for_path("/home/nick/Documents/Launch notes copy.md")
        self.assertEqual(outcome_text(FileRequest("Save a copy", mode="save"), [f]), "Saved “Launch notes copy.md” to Documents")
        self.assertEqual(outcome_text(FileRequest("Add photos"), [Gio.File.new_for_path("/p/a.jpg")]), "Photos opened “a.jpg”")
        self.assertEqual(outcome_text(FileRequest("Add a folder", mode="folder"), [Gio.File.new_for_path("/m/Jazz")]),
                         "Photos is adding “Jazz”")

    def test_app_icon_sizes_for_depot_and_valet(self):
        """Kit requests depot-03 and valet-01: 32, 44, 88 and 96, with Depot's own corners."""
        if not HAVE_GTK:
            self.skipTest("no GTK")
        from luma_appkit import rows_identity
        for size in (32, 44, 88, 96):
            self.assertIn(size, rows_identity.APP_ICON_SIZES)
        self.assertEqual([rows_identity.app_icon_corner(s) for s in (32, 44, 88)], [9, 12, 22])
        self.assertAlmostEqual(rows_identity.app_icon_corner(96), 96 * 0.22)
        css = SHEET.read_text()
        for size in (32, 44, 88, 96):
            self.assertIn(f".lumaui-appicon.s{size} ", css)

    def test_phone_app_tokens(self):
        """camera-03 and phone-13: generated in every appearance."""
        appkit = ROOT / "src/luma-platform/appkit"
        for sheet in ("luma-appkit-tokens.css", "luma-appkit-dark-tokens.css", "luma-appkit-high-contrast-tokens.css"):
            text = (appkit / sheet).read_text()
            for name in ("@define-color luma_media_capture #fed252;", "@define-color luma_call ", "@define-color luma_call_ink ",
                         "--lumaui-radius-round: 999px;", "--lumaui-call-panel-radius: 30px;"):
                self.assertIn(name, text, sheet)

    def test_the_python_constants_match_the_tokens(self):
        if not HAVE_GTK:
            self.skipTest("no GTK")
        from luma_appkit import rows_index, rows_swipe
        self.assertEqual(rows_swipe.COMMIT, FRAGMENT["swipe_row"]["commit"])
        self.assertEqual(rows_swipe.SLOP, FRAGMENT["swipe_row"]["slop"])
        self.assertEqual(rows_swipe.FACE_MIN, FRAGMENT["swipe_row"]["face_min"])
        self.assertEqual(rows_swipe.SETTLE_MS, FRAGMENT["swipe_row"]["settle_ms"])
        self.assertEqual(rows_index.MINIMUM, FRAGMENT["az_index"]["minimum"])
        self.assertEqual(rows_index.BUBBLE, FRAGMENT["az_index"]["bubble"])
        self.assertEqual(rows_index.BUBBLE_GAP, FRAGMENT["az_index"]["bubble_gap"])


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class Parts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from luma_appkit import lumaui
        lumaui.install(Gdk.Display.get_default())

    def setUp(self):
        self.windows: list[Gtk.Window] = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def test_sender_marks_and_the_island_disclosure(self):
        # v71 Charlie: .crbrand (a rounded square, one letter, bold, .44 of the side), the Updates
        # bundle's .crstk.sq (marks 26 on 38) and the subject pill's chevron (.crsubj).
        from gi.repository import Pango
        from luma_appkit import GroupFace, PersonAvatar, TitleIsland
        mark = PersonAvatar("Coastline Rail", 26, hue=200, mark=True)
        self.assertTrue(mark.has_css_class("mark"))
        label = mark.get_first_child()
        self.assertEqual(label.get_label(), "C")
        attrs = label.get_attributes().to_string()
        self.assertIn(f"absolute-size {round(26 * 0.44) * Pango.SCALE}", attrs)
        self.assertIn("weight bold", attrs)
        self.assertFalse(PersonAvatar("Coastline Rail", 26).has_css_class("mark"))
        self.assertIn(f"absolute-size {10 * Pango.SCALE}", PersonAvatar("Ben Okoro", 28).get_first_child().get_attributes().to_string())
        stranger = PersonAvatar("Maya Singh", 38, stranger=True).get_first_child().get_attributes().to_string()
        self.assertIn(f"absolute-size {14 * Pango.SCALE}", stranger)
        self.assertIn("weight 650", stranger)
        branded = PersonAvatar("C", 26, mark=True, colour="#0F5D73")
        self.assertTrue(branded.has_css_class("lumaui-paint-0f5d73"))
        with self.assertRaises(ValueError):
            PersonAvatar("C", 26, mark=True, colour="teal")
        stack = GroupFace([("C", None), ("S", None)], size=38, decorative=True, mark=True, hues=[200, 30])
        faces = [stack.get_first_child(), stack.get_first_child().get_next_sibling()]
        self.assertTrue(all(f.has_css_class("mark") and f.size == 26 for f in faces))
        self.assertEqual([f.hue for f in faces], [200, 30])
        self.assertFalse(TitleIsland("Deck", "Priya").disclosure.get_visible())
        self.assertTrue(TitleIsland("Deck", "Priya", disclosure=True).disclosure.get_visible())
        from luma_appkit import ListFirst
        self.assertTrue(ListFirst(Gtk.Box(), Gtk.Box(), status_inset=False).list_screen.has_css_class("full-height"))
        from luma_appkit import DetailsFacts, DetailsRow
        self.assertTrue(DetailsRow("Priya Raman", "priya@simplyluma.com", prominent=True).has_css_class("prominent"))
        self.assertTrue(DetailsFacts([("Messages", "4")], prominent=True).has_css_class("prominent"))
        from luma_appkit import AvatarStack
        subject = AvatarStack([("Priya", None), ("Nora", None)], size="header", face=36, hues=[330, 45])
        faces = [subject.get_first_child(), subject.get_first_child().get_next_sibling()]
        self.assertEqual([f.size for f in faces], [36, 36])
        self.assertEqual([f.hue for f in faces], [330, 45])
        from luma_appkit.content_type import apply_type
        subject = apply_type(Gtk.Label(label="Launch walkthrough deck"), "body", ink="secondary")
        self.assertTrue(subject.has_css_class("lumaui-t-secondary"))
        apply_type(subject, "body", ink="accent")
        self.assertTrue(subject.has_css_class("lumaui-t-accent") and not subject.has_css_class("lumaui-t-secondary"))
        with self.assertRaises(ValueError):
            apply_type(subject, "body", muted=True, ink="secondary")
        # An account's heading (.crnh): dot in its hue, name, address under it, ruled above.
        from luma_appkit.rows_navigation import SidebarSection
        heading = SidebarSection("Luma", subtitle="nick@simplyluma.com", hue=250)
        self.assertTrue(heading.has_css_class("account") and heading.has_css_class("ruled"))
        line = heading.get_child()
        dot = line.get_first_child()
        self.assertTrue(dot.has_css_class("lumaui-section-dot") and dot.has_css_class("lumaui-hue-250"))
        words = dot.get_next_sibling()
        self.assertEqual([w.get_label() for w in (words.get_first_child(), words.get_last_child())],
                         ["Luma", "nick@simplyluma.com"])
        # A computer's floating island keeps its glass; a phone-only island on a computer is flat.
        pill = TitleIsland("Deck", "Priya", lead=None, phone_only=False, disclosure=True)
        pill._tiered("regular")
        self.assertFalse(pill.has_css_class("flat"))
        self.assertTrue(pill.has_css_class("desk"), "a computer's floating island takes .flo's metrics")
        row = TitleIsland("Deck", "Priya")
        row._tiered("regular")
        self.assertTrue(row.has_css_class("flat"))

    def _spin(self, ms: int = 120) -> None:
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, loop.quit)
        loop.run()

    def _show(self, child: Gtk.Widget, width: int = 402, height: int = 874) -> Gtk.Window:
        window = Gtk.Window(default_width=width, default_height=height)
        window.set_child(child)
        self.windows.append(window)
        window.present()
        self._spin()
        return window

    # ── SwipeRow ───────────────────────────────────────────────────────────
    def _swipe(self, width: int = 402, **actions):
        from luma_appkit import SwipeAction, SwipeRow
        done: list[str] = []
        start = SwipeAction("pin", "yellow", lambda row: done.append("pin"), label="Pin")
        end = SwipeAction("bell-off", "blue", lambda row: done.append("mute"), label="Mute")
        kwargs = {"start": start, "end": end}
        kwargs.update(actions)
        row = SwipeRow(Gtk.Label(label="Ari Novak", height_request=64), **kwargs)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        box.append(row)
        self._show(box, width=width)
        return row, done

    def test_a_short_swipe_springs_back(self):
        row, done = self._swipe()
        row._on = False
        row.drag_to(60, 2)
        self._spin(30)
        self.assertEqual(row.offset, 60)
        self.assertTrue(row.reveal.get_child_visible())
        self.assertIn("yellow", row.reveal.get_css_classes())
        self.assertEqual(row.face_label.get_label(), "Pin")
        self.assertFalse(row.committing)
        row.release()
        self._spin(400)
        self.assertEqual(row.offset, 0)
        self.assertFalse(row.reveal.get_child_visible())
        self.assertEqual(done, [])

    def test_past_90_it_commits_and_acts_after_sliding_away(self):
        row, done = self._swipe()
        row.drag_to(-100, 4)
        self.assertTrue(row.committing)
        self.assertIn("go", row.get_css_classes())
        self.assertIn("blue", row.reveal.get_css_classes())
        # the reveal is on the end edge, as wide as the slide
        # Await its first allocation, not an assumed 30ms compositor frame.
        # The complete suite can defer that frame on a loaded build server.
        for _ in range(100):
            if row.reveal.get_width() > 0:
                break
            self._spin(10)
        ok, bounds = row.reveal.compute_bounds(row)
        self.assertTrue(ok)
        self.assertAlmostEqual(bounds.get_x(), row.get_width() - 100, delta=1)
        self.assertAlmostEqual(bounds.get_width(), 100, delta=1)
        row.release()
        self.assertEqual(done, [], "the action waits for the slide")
        self._spin(400)
        self.assertEqual(done, ["mute"])
        self.assertEqual(row.offset, 0)

    def test_exactly_90_does_not_commit(self):
        row, done = self._swipe()
        row.drag_to(90, 0)
        self.assertFalse(row.committing)
        row.drag_to(91, 0)
        self.assertTrue(row.committing)

    def test_a_vertical_drag_stays_a_scroll(self):
        row, done = self._swipe()
        row.drag_to(5, 20)
        self.assertEqual(row.offset, 0)
        row.drag_to(80, 20)     # once read as vertical, the sideways part is never taken
        self.assertEqual(row.offset, 0)
        row.release()
        self._spin(300)
        self.assertEqual(done, [])

    def test_slop_and_ratio(self):
        row, _ = self._swipe()
        row.drag_to(10, 0)
        self.assertEqual(row.offset, 0, "12 px of slop")
        row.drag_to(15, 11)
        self.assertEqual(row.offset, 0, "not sideways enough (1.4x): wait")
        row.drag_to(30, 11)
        self.assertEqual(row.offset, 30)

    def test_a_side_with_no_action_rubber_bands(self):
        from luma_appkit import SwipeAction
        row, done = self._swipe(start=None, end=SwipeAction("trash-2", "red", lambda r: done.append("x"), label="Remove"))
        row.drag_to(200, 0)
        self.assertEqual(row.offset, 12)
        self.assertFalse(row.committing)
        row.release()
        self._spin(300)
        self.assertEqual(done, [])

    def test_off_at_desktop_width(self):
        row, _ = self._swipe(width=900)
        self.assertFalse(row.enabled())
        row.phone_only = False
        self.assertTrue(row.enabled())

    def test_colour_is_checked(self):
        from luma_appkit import SwipeAction
        with self.assertRaises(ValueError):
            SwipeAction("pin", "purple", lambda r: None)

    # ── kit requests ───────────────────────────────────────────────────────
    def test_row_face_takes_the_persons_hue(self):
        """phone-14: RowLead.face(hue=) reaches the initials face."""
        from luma_appkit import RowLead
        lead = RowLead.face("Aaron Fischer", size="medium", hue=20)
        self._show(lead, width=200, height=100)
        faces = []
        def walk(w):
            if type(w).__name__ == "PersonAvatar":
                faces.append(w)
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()
        walk(lead)
        self.assertTrue(faces)
        other = RowLead.face("Aaron Fischer", size="medium", hue=67)
        self._show(other, width=200, height=100)
        css = lambda root: sorted(c for w in [root] for c in self._classes(root) if "hue" in c)
        self.assertNotEqual(css(lead), css(other), "a different hue draws a different face")

    def _classes(self, root):
        out, stack = [], [root]
        while stack:
            w = stack.pop()
            out.extend(w.get_css_classes())
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        return out

    def test_weight_300_and_weather_phone_roles(self):
        """weather-03 and valet-05: the roles exist, 300 is a weight, a weight beats its role in an app window."""
        from luma_appkit import apply_type
        from luma_appkit.content_type import TYPE_ROLES, WEIGHTS
        self.assertIn(300, WEIGHTS)
        for role in ("weather-phone-temperature", "weather-phone-sentence", "weather-phone-title", "weather-phone-tile",
                     "weather-phone-kicker", "weather-phone-hour-temperature"):
            self.assertIn(role, TYPE_ROLES)
        window = Gtk.Window(default_width=400, default_height=200)
        window.add_css_class("luma-app-window")
        label = Gtk.Label(label="Signed by Kiln Labs")
        apply_type(label, "body", weight=600)
        window.set_child(label)
        self.windows.append(window)
        window.present()
        self._spin()
        weight = label.get_pango_context().get_font_description().get_weight()
        self.assertEqual(int(weight), 600)

    def test_glass_cards_for_weather_on_a_phone(self):
        """weather-02: ContentLitCard(glass=True, shape=...)."""
        from luma_appkit.content_cards import ContentLitCard
        card = ContentLitCard(Gtk.Label(label="Now"), glass=True)
        lst = ContentLitCard(glass=True, shape="list")
        now = lst.append_row(Gtk.Label(label="Now  72°"), current=True)
        lst.append_row(Gtk.Label(label="3 PM  74°"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(card)
        box.append(lst)
        self._show(box)
        self.assertIn("glass", card.get_css_classes())
        self.assertIn("current", now.get_css_classes())
        self.assertAlmostEqual(now.compute_bounds(lst)[1].get_height(), 52, delta=0.5)
        with self.assertRaises(ValueError):
            ContentLitCard(glass=True, shape="sheet")

    def test_a_bubble_child_can_take_the_text_padding_and_a_wider_cap(self):
        from luma_appkit import MessageBubble
        photo = MessageBubble(child=Gtk.Label(label="photo"))
        reply = MessageBubble(child=Gtk.Label(label="reply"), padded=True, max_width=680)
        self.assertIn("media", photo.get_css_classes(), "a child is in the 3 px media padding by default")
        self.assertNotIn("media", reply.get_css_classes())
        self.assertIn("padded", reply.get_css_classes(), "padded: the text padding (Ari's reply)")
        self.assertEqual(reply.get_layout_manager().cap, 680)
        self.assertEqual(photo.get_layout_manager().cap, 520)

    # ── MessageRun ─────────────────────────────────────────────────────────
    def test_a_run_measures_after_layout_and_redraws_its_corners(self):
        from luma_appkit import MessageBubble, MessageRun
        short = MessageBubble("Will do")
        long = MessageBubble("Are we still on for the walkthrough at 2? Bring the Fairphone build.")
        run = MessageRun([long, short], mine=False)
        self._show(run, width=900)
        self.assertEqual(run.corners(), [(False, False, False, True), (True, True, False, False)])
        self.assertIn("run-bl", long.get_css_classes())
        self.assertNotIn("run-br", long.get_css_classes())
        self.assertNotIn("join-above", short.get_css_classes(), "the run draws every corner")
        # 2 apart
        ok_a, a = long.compute_bounds(run)
        ok_b, b = short.compute_bounds(run)
        self.assertAlmostEqual(b.get_y() - (a.get_y() + a.get_height()), 2, delta=0.5)
        # the short one grows wider than the one above: it redraws
        short.label.set_label("Will do, and I will bring the Fairphone build and the spare charger too, promise.")
        self._spin(150)
        self.assertEqual(run.corners()[0], (False, False, True, True))

    def test_a_card_takes_part_as_it_is(self):
        from luma_appkit import MessageBubble, MessageRun
        card = Gtk.Box(width_request=300, height_request=60)
        run = MessageRun(mine=True)
        run.append(MessageBubble("Yes", mine=True))
        run.append(card)
        self._show(run, width=900)
        self.assertEqual(card.get_halign(), Gtk.Align.END)
        self.assertIn("lumaui-run-shaped", card.get_css_classes())
        self.assertIn("run-tr", card.get_css_classes(), "the sender's side joins")
        self.assertNotIn("run-tl", card.get_css_classes(), "a narrower bubble above leaves the far top round")
        self.assertIn("run-bl", run.shapes[0].get_css_classes(), "the narrow bubble rests on the wider card")

    # ── AZIndex ────────────────────────────────────────────────────────────
    NAMES = ("Ada Lovelace", "Ari Novak", "Ben Okafor", "Cleo Park", "Dana Ruiz", "Eli Sato", "Gus Hale",
             "Hana Ito", "Jo March", "Kai Lund", "Mia Chen", "Noor Ali", "Theo Grant", "Zoe Quinn") * 3

    def _az(self, names=NAMES, width: int = 402):
        from luma_appkit import AZIndex
        listbox = Gtk.ListBox()
        for name in sorted(names):
            row = Gtk.ListBoxRow(child=Gtk.Label(label=name, height_request=56, xalign=0))
            row.sort_name = name
            listbox.append(row)
        scroller = Gtk.ScrolledWindow(child=listbox, vexpand=True)
        overlay = Gtk.Overlay(child=scroller)
        index = AZIndex(listbox, key=lambda r: r.sort_name)
        overlay.add_overlay(index)
        self._show(overlay, width=width)
        self._spin(200)
        return index, scroller, listbox

    def test_index_shows_at_phone_width_with_dimmed_letters_and_a_lane(self):
        index, scroller, _ = self._az()
        self.assertTrue(index.get_visible())
        self.assertIn("lumaui-az-gutter", scroller.get_css_classes())
        self.assertNotIn("none", index.letters["A"].get_css_classes())
        self.assertIn("none", index.letters["F"].get_css_classes())
        # 20 wide at 3 from the right, 150 from the top and bottom
        window = index.get_root()
        ok, bounds = index.compute_bounds(window)
        self.assertAlmostEqual(bounds.get_width(), 20, delta=1)
        self.assertAlmostEqual(window.get_width() - (bounds.get_x() + bounds.get_width()), 3, delta=1)
        self.assertAlmostEqual(bounds.get_y(), 150, delta=1)
        # the list stops short of the index
        ok, list_bounds = scroller.get_child().compute_bounds(window)
        self.assertLessEqual(list_bounds.get_x() + list_bounds.get_width(), window.get_width() - 26 + 0.5)

    def test_touching_a_letter_jumps_to_it_or_the_next(self):
        index, scroller, listbox = self._az()
        height = index.get_height()
        letter = index.jump_at(height * (3.5 / 26))     # D
        self.assertEqual(letter, "D")
        self.assertEqual(index.bubble.get_label(), "D")
        self.assertIn("on", index.get_css_classes())
        self._spin(50)
        value_d = scroller.get_vadjustment().get_value()
        self.assertGreater(value_d, 0)
        self.assertEqual(index.jump_at(height * (5.5 / 26)), "G", "F has no one: the next letter that does")
        self.assertGreater(scroller.get_vadjustment().get_value(), value_d)
        index.let_go()
        self.assertNotIn("on", index.get_css_classes())
        self.assertFalse(index.bubble.get_child_visible())

    def test_index_is_placed_from_the_window_not_its_overlay(self):
        """Contacts' gate: the overlay sits under a large title; the index still starts 150 from the window top."""
        from luma_appkit import AZIndex
        listbox = Gtk.ListBox()
        for name in sorted(self.NAMES):
            row = Gtk.ListBoxRow(child=Gtk.Label(label=name, height_request=56))
            row.sort_name = name
            listbox.append(row)
        overlay = Gtk.Overlay(child=Gtk.ScrolledWindow(child=listbox, vexpand=True), vexpand=True)
        index = AZIndex(listbox, key=lambda r: r.sort_name)
        overlay.add_overlay(index)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(Gtk.Label(label="Contacts", height_request=100))
        page.append(overlay)
        window = self._show(page)
        self._spin(200)
        ok, bounds = index.compute_bounds(window)
        self.assertAlmostEqual(bounds.get_y(), 150, delta=1)
        self.assertAlmostEqual(bounds.get_y() + bounds.get_height(), window.get_height() - 150, delta=1)
        self.assertAlmostEqual(window.get_width() - bounds.get_x() - bounds.get_width(), 3, delta=1)

    def test_an_empty_key_is_not_a_heading(self):
        """contacts-05: a row with no key (a heading row, a loading row) is skipped."""
        from luma_appkit import AZIndex
        listbox = Gtk.ListBox()
        for name in ("",) + tuple(sorted(self.NAMES)):
            row = Gtk.ListBoxRow(child=Gtk.Label(label=name or "A"))
            row.sort_name = name
            listbox.append(row)
        index = AZIndex(listbox, key=lambda r: r.sort_name, phone_only=False)
        self.assertNotIn("", [letter for letter, _ in index.headings()])
        self.assertEqual(index.headings()[0][0], "A")

    def test_list_first_rows_are_64_on_a_phone(self):
        """v71 .phstack.navopen :is(.crow, .ctrow) { min-height: 64px }."""
        from luma_appkit import RowLead, SidebarRow
        screen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        screen.add_css_class("lumaui-list-first-list")
        listbox = Gtk.ListBox()
        row = SidebarRow("Aaron Fischer", lead=RowLead.face("Aaron Fischer", size="small"), subtitle="@aaronf")
        listbox.append(row)
        screen.append(listbox)
        self._show(screen)
        ok, b = row.compute_bounds(listbox)
        self.assertAlmostEqual(b.get_height(), 64, delta=0.5)

    def test_selected_sidebar_row_has_room_for_its_outer_stroke(self):
        """The scroller must not clip either side of a selected page's raised chip."""
        from luma_appkit import NavigationSidebar, RowLead, SidebarRow
        sidebar = NavigationSidebar(variant="tree")
        row = SidebarRow("Tester", lead=RowLead.icon("file-text"))
        sidebar.append_row(row)
        sidebar.list.select_row(row)
        self._show(sidebar, width=900, height=400)
        viewport = sidebar._scroll.get_child()
        ok, bounds = row.compute_bounds(viewport)
        self.assertTrue(ok)
        self.assertGreaterEqual(bounds.get_x(), 2, "left stroke/drop reaches the viewport clip")
        self.assertGreaterEqual(viewport.get_width() - bounds.get_x() - bounds.get_width(), 2,
                                "right stroke/drop reaches the viewport clip")
        self.assertTrue(row.is_selected())

    def test_index_hides_with_few_letters_or_on_the_desktop(self):
        index, scroller, _ = self._az(names=("Ada", "Ben", "Cleo"))
        self.assertFalse(index.get_child_visible())
        self.assertNotIn("lumaui-az-gutter", scroller.get_css_classes())
        wide, wide_scroller, _ = self._az(width=900)
        self.assertFalse(wide.get_child_visible())

    def test_app_hidden_index_stays_hidden_across_refresh_and_resize(self):
        index, scroller, _ = self._az()
        window = index.get_root()
        self.assertTrue(index.get_mapped())
        index.set_visible(False)
        for width in (1180, 402, 720, 360):
            window.set_default_size(width, 874)
            index.schedule()
            self._spin(250)
            self.assertFalse(index.get_visible())
            self.assertFalse(index.get_mapped())
            self.assertNotIn("lumaui-az-gutter", scroller.get_css_classes())
        index.set_visible(True)
        self._spin(100)
        self.assertTrue(index.get_mapped())
        self.assertIn("lumaui-az-gutter", scroller.get_css_classes())
        window.set_default_size(1180, 874)
        self._spin(250)
        self.assertTrue(index.get_visible())
        self.assertFalse(index.get_child_visible())
        self.assertFalse(index.get_mapped())
        self.assertNotIn("lumaui-az-gutter", scroller.get_css_classes())
        # Explicitly hide while already suppressed by its width, then return.
        index.set_visible(False)
        window.set_default_size(402, 874)
        self._spin(250)
        self.assertFalse(index.get_visible())
        self.assertFalse(index.get_mapped())

    def test_index_follows_a_list_view_model(self):
        from luma_appkit import AZIndex
        model = Gtk.StringList.new(sorted(self.NAMES))
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(Gtk.Label(height_request=56)))
        factory.connect("bind", lambda _f, item: item.get_child().set_label(item.get_item().get_string()))
        view = Gtk.ListView(model=Gtk.NoSelection(model=model), factory=factory)
        scroller = Gtk.ScrolledWindow(child=view, vexpand=True)
        overlay = Gtk.Overlay(child=scroller)
        index = AZIndex(view, key=lambda item: item.get_string())
        overlay.add_overlay(index)
        self._show(overlay)
        self._spin(200)
        self.assertTrue(index.get_visible())
        self.assertEqual(index.target_for("Y"), ("Z", len(self.NAMES) - 3))


if __name__ == "__main__":
    unittest.main()
