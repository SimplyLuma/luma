# SPDX-License-Identifier: Apache-2.0
"""LumaUI bar family (KB-B): items for the action center, sharing and export.

`Sources` and `Pure` read files and pure functions and run anywhere GTK's
introspection data is installed. `Parts` builds real widgets on stock GTK 4
and needs a display (it never presents a window); it is skipped otherwise.
"""
from __future__ import annotations

import json
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
SHEET = APPKIT / "luma-appkit-bar.css"
FRAGMENT = ROOT / "config/shared/design-tokens.d/bar.json"
GALLERY = ROOT / "src/luma-platform/tools/lumaui-gallery/pages_bar.py"

#: Part → (CSS banner, exported names).
BAR_PARTS = {
    "Bar entry": ("BarEntry", "ENTRY_KINDS"),
    "Modes in the bar": (),
    "Bar search": ("BarSearch",),
    "Selected subject": ("BarThumbnail",),
    "Bar readout": ("BarReadout",),
    "Split action and bar menu": ("SplitAction", "BarMenu"),
    "Zoom control": ("ZoomControl", "ZOOM_KINDS"),
    "Document header": ("DocumentHeader",),
    "Export sheet": ("ExportSheet", "ExportChoice"),
    "Share sheet": ("ShareSheet", "ShareSubject", "ShareTarget", "Collaborator", "SHARE_CHOICES"),
}

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


def _spin(times: int = 60) -> None:
    context = GLib.MainContext.default()
    for _ in range(times):
        context.iteration(False)


class Sources(unittest.TestCase):
    def test_every_part_has_a_banner_an_export_and_a_gallery_page(self):
        css = SHEET.read_text()
        init = (KIT / "__init__.py").read_text()
        gallery = GALLERY.read_text()
        block = init[init.index("# ── LumaUI bar"):init.index("# ── end LumaUI bar")]
        for part, names in BAR_PARTS.items():
            self.assertIn(f"/* LumaUI: {part} */", css)
            for name in names:
                self.assertIn(f'"{name}"', block, name)

    def test_every_part_has_a_gallery_page(self):
        gallery = GALLERY.read_text()
        for title in ("Bar entry", "Share sheet", "Modes in the bar", "Search, readouts, split key and zoom",
                      "Document header and export"):
            self.assertIn(f'"{title}"', gallery)

    def test_every_glyph_the_family_names_is_in_the_icon_set(self):
        actions = ROOT / "assets/icon-theme/Prairie/symbolic/actions"
        sources = "".join(p.read_text() for p in KIT.glob("bar_*.py")) + GALLERY.read_text()
        names = set(re.findall(r'(?:icons\.image|BarAction|_icon_key|MenuItem\([^)]*icon=)\(?"([a-z0-9-]+)"', sources))
        names |= set(re.findall(r'icon="([a-z0-9-]+)"', sources))
        missing = sorted(n for n in names if not (actions / f"lumaui-{n}-symbolic.svg").is_file())
        self.assertEqual(missing, [])

    def test_the_sheet_names_its_colours_and_reads_its_metrics(self):
        css = re.sub(r"/\*.*?\*/", "", SHEET.read_text(), flags=re.S)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(", css), [])
        used = set(re.findall(r"--lumaui-bar-([a-z0-9-]+)", css))
        spec = importlib.util.spec_from_file_location(
            "bar_token_source", ROOT / "scripts/developer/generate-luma-platform-tokens.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        groups = generator.load_document()["lumaui"]["bar"]
        defined = {f"{g.replace('_', '-')}-{k.replace('_', '-')}" for g, values in groups.items() if isinstance(values, dict)
                   for k in values if not k.startswith("_")}
        self.assertEqual(used - defined, set(), "the sheet reads bar tokens the fragment does not define")

    def test_the_fragment_is_numbers_in_named_groups(self):
        groups = json.loads(FRAGMENT.read_text())["lumaui"]["bar"]
        for name, values in groups.items():
            if name.startswith("_"):
                continue
            for key, value in values.items():
                if not key.startswith("_"):
                    self.assertIsInstance(value, (int, float), f"{name}.{key}")


@unittest.skipUnless(HAVE_GTK, "needs GTK 4 introspection data")
class Pure(unittest.TestCase):
    def test_chip_parts(self):
        from luma_appkit.bar_entry import chip_parts

        self.assertEqual(chip_parts("Tomorrow"), (None, "Tomorrow"))
        self.assertEqual(chip_parts(("clock", "3 pm")), ("clock", "3 pm"))
        with self.assertRaises(TypeError):
            chip_parts(3)

    def test_suggestions(self):
        from luma_appkit.bar_entry import accept_suggestion, ghost_tail

        self.assertEqual(accept_suggestion("git", "git status"), "git status")
        self.assertEqual(accept_suggestion("git st", "atus"), "git status")
        self.assertEqual(accept_suggestion("ls", None), "ls")
        self.assertEqual(ghost_tail("git", "git status"), " status")
        self.assertEqual(ghost_tail("", "git status"), "")

    def test_return_sends_unless_shift_or_composing(self):
        from luma_appkit.bar_entry import is_submit

        none, shift = Gdk.ModifierType(0), Gdk.ModifierType.SHIFT_MASK
        self.assertTrue(is_submit("compose", Gdk.KEY_Return, none, False))
        self.assertFalse(is_submit("compose", Gdk.KEY_Return, shift, False))
        self.assertFalse(is_submit("compose", Gdk.KEY_Return, none, True))
        self.assertTrue(is_submit("quick", Gdk.KEY_KP_Enter, none, False))
        self.assertFalse(is_submit("compose", Gdk.KEY_a, none, False))

    def test_prefix_parts(self):
        from luma_appkit.bar_entry import prefix_parts

        self.assertEqual(prefix_parts("~ $"), ("~ $", None, False))
        self.assertEqual(prefix_parts(("~/luma", "main")), ("~/luma", "main", True))
        self.assertEqual(prefix_parts(("~/luma", None)), ("~/luma", None, True))

    def test_bar_entry_validates(self):
        from luma_appkit import BarAction, BarEntry

        with self.assertRaises(ValueError):
            BarEntry("chat", placeholder="x")
        with self.assertRaises(ValueError):
            BarEntry("compose")
        with self.assertRaises(ValueError):
            BarEntry("quick", placeholder="x", prefix="$")
        with self.assertRaises(ValueError):
            BarEntry("quick", placeholder="x", voice=lambda: None)
        with self.assertRaises(TypeError):
            BarEntry("compose", placeholder="x", tools=[BarAction("smile", "Emoji")])
        with self.assertRaises(ValueError):
            BarEntry("quick", placeholder="x", span="huge")
        with self.assertRaises(ValueError):
            BarEntry("quick", placeholder="x", close_label="Done")
        self.assertEqual(BarEntry("quick", placeholder="x").span, "narrow")
        self.assertEqual(BarEntry("compose", placeholder="x").span, "regular")

    def test_share_defaults_follow_what_is_shared(self):
        from luma_appkit import Person, ShareSubject
        from luma_appkit.bar_share import default_targets, share_actions

        picture = ShareSubject("Terrace", image=True)
        self.assertEqual([t.key for t in default_targets(picture)],
                         ["messages", "mail", "nearby", "notes", "photos", "canvas", "ari"])
        self.assertIn("write", [t.key for t in default_targets(ShareSubject("Plan.md"))])
        self.assertNotIn("write", [t.key for t in default_targets(ShareSubject("Café", kind="link"))])
        self.assertEqual([a[0] for a in share_actions(ShareSubject("Plan.md"))], ["copy", "save", "print"])
        self.assertEqual(share_actions(ShareSubject("Café", kind="link")), [],
                         "a local subject must not invent a public link")
        self.assertEqual([a[0] for a in share_actions(ShareSubject(
            "Café", kind="link", share_link_available=True))], ["copy-link", "code"])
        card = ShareSubject("Priya", kind="contact", person=Person("Priya Raman", username="priya"))
        self.assertEqual([a[0] for a in share_actions(card)], ["copy-card", "save"])
        card.share_link_available = True
        self.assertEqual(share_actions(card)[0][0], "copy-link")
        with self.assertRaises(ValueError):
            ShareSubject("x", kind="song")

    def test_zoom_steps(self):
        from luma_appkit.bar_items import zoom_step, zoom_text

        self.assertEqual(zoom_step(1.0, +1), 1.5)
        self.assertEqual(zoom_step(1.0, -1), 0.75)
        self.assertEqual(zoom_step(0.8, -1), 0.75)
        self.assertEqual(zoom_step(8.0, +1), 8.0)
        self.assertEqual(zoom_step(0.1, -1), 0.1)
        self.assertEqual(zoom_text(0.125), "13%")
        self.assertEqual(zoom_text(0.425), "43%")
        self.assertEqual(zoom_text(1), "100%")

    def test_subject_name(self):
        from luma_appkit import BarChip
        from luma_appkit.bar_items import subject_name

        self.assertEqual(subject_name(BarChip("Viola", meta="PID 3802")), "Selected: Viola, PID 3802")

    def test_items_validate(self):
        from luma_appkit import BarAction, BarReadout, ZoomControl

        with self.assertRaises(ValueError):
            BarReadout("3 of 9", on_previous=lambda: None)
        with self.assertRaises(ValueError):
            ZoomControl(1.0, print, kind="dial")
        with self.assertRaises(ValueError):
            BarAction("")
        self.assertEqual(BarAction("", "Save").label, "Save")

    def test_tokens_load(self):
        from luma_appkit import bar_tokens

        self.assertEqual(bar_tokens.metric("entry", "height"), 36)
        generated = (APPKIT / "luma-appkit-tokens.css").read_text()
        self.assertIn("--lumaui-bar-entry-height: 36px;", generated, "rerun generate-luma-platform-tokens.py")


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class Parts(unittest.TestCase):
    def _center(self, editor=None):
        from luma_appkit.action_center import ActionCenter
        from luma_appkit.structure_layers import LayerHost

        host = LayerHost()
        host.set_child(Gtk.Box())
        return ActionCenter(editor).attach(host)

    def test_compose_in_the_bar(self):
        from luma_appkit import BarAction, BarEntry

        center = self._center()
        sent, voiced = [], []
        entry = BarEntry("compose", placeholder="Message Priya", on_submit=sent.append, voice=lambda: voiced.append(1))
        center.show_bar([BarAction("plus", tooltip="Attach"), entry])
        self.assertTrue(center.bar.has_css_class("wide"), "a bar with a field is the wide bar")
        widget = entry.widget
        self.assertEqual(widget.key.key_role, "voice")
        widget.key.emit("clicked")
        self.assertEqual(voiced, [1])
        widget.buffer.set_text("hello")
        self.assertEqual(entry.text, "hello")
        self.assertEqual(widget.key.key_role, "send")
        self.assertTrue(widget.key.has_css_class("go"))
        widget.key.emit("clicked")
        self.assertEqual(sent, ["hello"])
        entry.set_busy(True)
        self.assertEqual(widget.key.key_role, "stop")
        self.assertTrue(widget.key.has_css_class("stop"))
        self.assertFalse(widget.key.get_sensitive(), "no Stop without on_stop")
        entry.set_busy(False)
        self.assertFalse(widget.key.has_css_class("stop"))

    def test_flat_compose_and_inline_action_are_opt_in(self):
        from luma_appkit import BarAction, BarEntry
        from luma_appkit.action_center import make_control

        with self.assertRaises(ValueError):
            BarEntry("quick", placeholder="Search", flat=True)
        center = self._center()
        entry = BarEntry("compose", placeholder="Ask", flat=True)
        center.show_bar([BarAction("house", tooltip="Model"), entry])
        self.assertTrue(center.bar.has_css_class("flat-entry"))
        self.assertFalse(center.bar_row.get_first_child().has_css_class("image-button"))
        self.assertTrue(entry.widget.well.has_css_class("flat"))
        inline = make_control(BarAction("copy", tooltip="Copy"), size="inline")
        self.assertTrue(inline.has_css_class("inline"))
        self.assertFalse(inline.has_css_class("image-button"))
        center.show_bar([BarAction("house", tooltip="Model")])
        self.assertFalse(center.bar.has_css_class("flat-entry"))

    def test_the_draft_carries_into_the_editor_and_back(self):
        from luma_appkit import ActionEditor, BarAction, BarEntry

        editor = ActionEditor("Message", "message-square", primary=BarAction("send-horizontal", "Send"))
        center = self._center(editor)
        entry = BarEntry("compose", placeholder="Message Priya")
        center.show_bar([entry])
        entry.set_text("See you at the review")
        center.grow()
        self.assertEqual(editor.draft, "See you at the review")
        editor.text_view.get_buffer().set_text("See you at the review, with the deck")
        center.fold()
        self.assertEqual(entry.text, "See you at the review, with the deck")
        center.grow()
        center.discard()
        self.assertEqual(entry.text, "")

    def test_quick_chips_and_escape(self):
        from luma_appkit import BarEntry

        center = self._center()
        closed = []
        entry = BarEntry("quick", icon="plus", placeholder="Add a task", chips=["Today"], close_label="Done",
                         on_close=lambda: closed.append(1))
        center.show_bar([entry])
        self.assertEqual(center.layout(1000)[1], 600)
        self.assertTrue(entry.widget.chip_box.get_visible())
        entry.set_chips([("clock", "3 pm"), "Tomorrow"])
        labels = []
        chip = entry.widget.chip_box.get_first_child()
        while chip is not None:
            labels.append(chip.get_last_child().get_label())
            chip = chip.get_next_sibling()
        self.assertEqual(labels, ["3 pm", "Tomorrow"])
        entry.set_text("Call Theo")
        self.assertTrue(entry._escape())
        self.assertEqual(entry.text, "")
        self.assertEqual(closed, [])
        entry._escape()
        self.assertEqual(closed, [1])
        entry.widget.close_button.emit("clicked")
        self.assertEqual(closed, [1, 1])

    def test_command_ghost_and_busy(self):
        from luma_appkit import BarEntry

        center = self._center()
        taken = []
        entry = BarEntry("command", prefix=("~/Projects/luma", "main"), placeholder="Command",
                         suggestion="git status", on_accept_suggestion=taken.append)
        center.show_bar([entry])
        entry.set_text("git")
        self.assertEqual(entry.widget.ghost_rest.get_label(), " status")
        self.assertEqual(center._bar_span, (860, 32, 32))
        self.assertEqual(center.layout(1000)[1], 860)
        self.assertEqual(center.layout(390)[1], 390 - 64, "Terminal keeps its 32 sides on a phone")
        key = Gtk.EventControllerKey()
        entry.widget._key(key, Gdk.KEY_Tab, 0, Gdk.ModifierType(0))
        self.assertEqual(entry.text, "git status")
        self.assertEqual(taken, ["git status"])
        entry.set_busy("Running make…  Ctrl C to stop")
        self.assertEqual(entry.widget.line.get_placeholder_text(), "Running make…  Ctrl C to stop")

    def test_compose_grows_to_the_cap_then_scrolls(self):
        from luma_appkit import BarEntry
        from luma_appkit.bar_entry import _EntryWidget

        entry = BarEntry("compose", placeholder="Message")
        widget = _EntryWidget(entry)
        window = Gtk.Window(child=widget, default_width=500, default_height=200)
        window.present()
        _spin(200)
        entry.set_text("word " * 300)
        import time
        deadline = time.monotonic() + 5
        while widget.scroller.get_policy()[1] != Gtk.PolicyType.AUTOMATIC and time.monotonic() < deadline:
            _spin(20)
            time.sleep(0.01)
        _spin(50)
        self.assertEqual(widget.scroller.get_policy()[1], Gtk.PolicyType.AUTOMATIC)
        self.assertLessEqual(widget.well.get_height(), 140)
        window.destroy()


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class Items(unittest.TestCase):
    def _center(self):
        from luma_appkit.action_center import ActionCenter
        from luma_appkit.structure_layers import LayerHost

        host = LayerHost()
        host.set_child(Gtk.Box())
        return ActionCenter().attach(host)

    def test_search(self):
        from luma_appkit import BarSearch

        center, got = self._center(), []
        search = BarSearch("Search places", on_change=got.append, on_activate=lambda t: got.append(("go", t)))
        center.show_bar([search])
        self.assertFalse(center.bar.has_css_class("wide"), "as wide as its actions on a computer")
        self.assertEqual(center.layout(400)[1], -1, "as wide as its content on a phone too (capped by the island)")
        natural = search.widget.measure(Gtk.Orientation.HORIZONTAL, -1)
        self.assertEqual(natural[1], 250, "the regular well is 250 when there is room")
        self.assertLess(natural[0], 250, "and gives way when the bar is capped")
        search.entry.set_text("Blue Bottle")
        self.assertTrue(search.clear_button.get_visible())
        search.entry.emit("activate")
        self.assertEqual(got, ["Blue Bottle", ("go", "Blue Bottle")])
        search.clear_button.emit("clicked")
        self.assertEqual(search.text, "")
        self.assertFalse(search.clear_button.get_visible())
        search.set_text("Coffee")
        self.assertTrue(search.clear_button.get_visible())
        search._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertFalse(search.clear_button.get_visible())

    def test_subject_chip(self):
        from luma_appkit import BarChip, BarThumbnail, Person

        center, done = self._center(), []
        center.show_bar([BarChip("A long message " * 8, lead=Person("Priya Raman"), meta="2:14 pm",
                                 on_dismiss=lambda: done.append(1))])
        chip = center.bar_row.get_first_child()
        self.assertTrue(chip.has_css_class("lumaui-bar-subject"))
        self.assertLessEqual(chip.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 240 + 2, "240 and its 2 px margin")
        chip.get_last_child().emit("clicked")
        self.assertEqual(done, [1])
        center.show_bar([BarChip("Clip", lead=BarThumbnail(None, duration="0:12")), BarThumbnail(None, "0:04")])
        self.assertTrue(center.bar_row.get_last_child().has_css_class("lumaui-bar-thumb-button"))
        with self.assertRaises(TypeError):
            center.show_bar([BarChip("x", lead=3)])

    def test_readout_and_pager(self):
        from luma_appkit import BarReadout

        center, steps = self._center(), []
        eta = BarReadout("12 min", "3.4 mi")
        pager = BarReadout("3 of 120", on_previous=lambda: steps.append(-1), on_next=lambda: steps.append(1))
        center.show_bar([eta, pager])
        eta.set("11 min", "3.1 mi")
        self.assertEqual(eta._labels[1].get_label(), "3.1 mi")
        pager._keys[1].emit("clicked")
        pager.set_bounds(False, True)
        self.assertFalse(pager._keys[0].get_sensitive())
        self.assertEqual(steps, [1])

    def test_split_and_menu(self):
        from luma_appkit import BarAction, BarMenu, SplitAction

        center, saved, opened = self._center(), [], []
        split = SplitAction("Save", lambda: saved.append(1), lambda anchor: opened.append(anchor))
        style = BarMenu("Text", lambda anchor: opened.append("style"), name="Text style")
        center.show_bar([split, style, BarAction("", "Done", primary=True)])
        split.main.emit("clicked")
        split.more.emit("clicked")
        style.widget.emit("clicked")
        self.assertEqual(saved, [1])
        self.assertEqual(opened, [split.more, "style"])
        split.set_sensitive(False)
        self.assertFalse(split.main.get_sensitive())
        style.set_label("Heading")
        self.assertEqual(style._text.get_label(), "Heading")
        self.assertTrue(center.bar_row.get_last_child().has_css_class("text"))

    def test_zoom(self):
        from luma_appkit import ZoomControl

        center, asked, fits = self._center(), [], []
        zoom = ZoomControl(1.0, asked.append, on_fit=lambda: fits.append(1))
        center.show_bar([zoom])
        zoom.in_key.emit("clicked")
        zoom.out_key.emit("clicked")
        zoom.fit_key.emit("clicked")
        self.assertEqual(asked, [1.5, 0.75])
        self.assertEqual(fits, [1])
        zoom.set_value(0.425)
        self.assertEqual(zoom.percent.get_label(), "43%")
        zoom.set_value(8.0)
        self.assertEqual(zoom.percent.get_label(), "800%")
        self.assertFalse(zoom.in_key.get_sensitive())
        slider = ZoomControl(1.0, asked.append, kind="range", minimum=0.5, maximum=2.0)
        center.show_bar([slider])
        slider.scale.set_value(1.5)
        self.assertEqual(asked[-1], 1.5)
        slider.set_value(0.5)
        self.assertEqual(asked[-1], 1.5, "set_value does not ask")


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class Sharing(unittest.TestCase):
    def setUp(self):
        from luma_appkit.structure_layers import LayerHost

        self.button = Gtk.Button(label="Share", halign=Gtk.Align.END, valign=Gtk.Align.START)
        self.host = LayerHost(self.button)
        self.window = Gtk.Window(child=self.host, default_width=1000, default_height=700)
        self.window.present()
        _spin(100)

    def tearDown(self):
        self.window.destroy()

    def _people(self):
        from luma_appkit import Person

        return [Person("Priya Raman", username="priya"), Person("Nora Feld", username="nora"),
                Person("Theo Hart", username="theo")]

    def test_send_a_copy(self):
        from luma_appkit import ShareResult, ShareSheet, ShareSubject

        got = []
        completed = False

        def on_choice(choice, value):
            got.append((choice, value))
            return ShareResult(completed)

        sheet = ShareSheet.present(self.button, document=ShareSubject("Plan.pdf", "PDF"), people=self._people(),
                                   on_choice=on_choice)
        _spin()
        self.assertIsNotNone(sheet.get_parent(), "the sheet floats in the window")
        self.assertIsNone(sheet.mode_switch, "no collaboration store: no Work together")
        tile = sheet.people_row.get_child().get_child().get_child().get_first_child()
        tile.emit("clicked")
        self.assertEqual(got[0][0], "send-to")
        self.assertFalse(tile.has_css_class("sent"), "a refused send must not announce delivery")
        self.assertEqual(sheet.sent, set())
        completed = True
        tile.emit("clicked")
        self.assertEqual(got[1], got[0], "retry must dispatch the same person")
        self.assertTrue(tile.has_css_class("sent"))
        self.assertEqual(sheet.sent, {self._people()[0].name})
        sheet.action_buttons["copy"].emit("clicked")
        self.assertEqual(got[-1], ("copy", None))
        self.assertLessEqual(sheet.get_margin_top() + sheet.get_height(), 700)
        ShareSheet.present(self.button, document=ShareSubject("Plan.pdf"))  # the same button again closes it
        self.assertIsNone(sheet.get_parent())

    def test_work_together(self):
        from luma_appkit import Collaborator, Person, ShareSheet, ShareSubject

        got = []
        people = self._people()
        sheet = ShareSheet.present(self.button, document=ShareSubject("Deck", "Stage", kind="doc"),
                                   choices=("work-together", "send-copy"),
                                   collaborators=[Collaborator(people[0], "edit")],
                                   suggest=lambda q: [p for p in people if q.lower() in p.name.lower()],
                                   on_choice=lambda c, v: got.append((c, v)))
        _spin()
        self.assertEqual(sheet.mode, "work-together")
        sheet.invite.set_text("no")
        self.assertTrue(sheet.suggestions.get_visible())
        sheet.invite.emit("activate")
        self.assertEqual(got[-1][0], "invite")
        self.assertEqual([c.person.name for c in sheet.collaborators], ["Priya Raman", "Nora Feld"])
        sheet.invite.set_text("zzz")
        self.assertTrue(sheet.none_label.get_visible())
        sheet.mode_switch.buttons["send-copy"].set_active(True)
        self.assertEqual(sheet.mode, "send-copy")
        self.assertTrue(sheet.subtitle.get_label().startswith("A copy"))
        with self.assertRaises(ValueError):
            ShareSheet(ShareSubject("x.pdf"), choices=("work-together",))
        sheet.close()
        self.assertIsNone(ShareSheet._open)

    def test_document_header(self):
        from luma_appkit import DocumentHeader, Person

        shared, keyed = [], []
        header = DocumentHeader("Launch film", icon="org.projectluma.Reel", subtitle="Reel · Saved",
                                people=[Person("Nora Feld")], share=shared.append, primary="Export",
                                on_primary=lambda: keyed.append(1), more=lambda anchor: None)
        self.window.set_child(header)
        _spin()
        header.controls["share"].emit("clicked")
        header.controls["primary"].emit("clicked")
        self.assertEqual(shared, [header.controls["share"]])
        self.assertEqual(keyed, [1])
        header._width(600)
        self.assertTrue(header.narrow)
        self.assertFalse(header.meta.get_visible())
        self.assertFalse(header.faces.get_visible())
        self.assertFalse(header.controls["share"].word.get_visible())
        header._width(1000)
        self.assertTrue(header.faces.get_visible())
        with self.assertRaises(ValueError):
            DocumentHeader("x", primary="Publish", on_primary=print)
        with self.assertRaises(ValueError):
            DocumentHeader("x", primary="Export")

    def test_export_sheet(self):
        from luma_appkit import ExportChoice, ExportSheet

        got, cancelled = [], []
        choices = [ExportChoice("standard", "Standard", "1080p", "about 84 MB"),
                   ExportChoice("audio", "Audio only", shape="audio")]
        sheet = ExportSheet.present(self.button, title="Export “Launch film”", choices=choices,
                                    on_export=got.append, on_cancel=lambda: cancelled.append(1))
        _spin()
        self.assertEqual(sheet.selected, "standard")
        sheet.rows["audio"].set_active(True)
        self.assertTrue(sheet.rows["audio"].has_css_class("on"))
        sheet.export_button.emit("clicked")
        self.assertEqual(got, ["audio"])
        self.assertFalse(sheet.is_open)
        self.assertIsNone(sheet.get_parent(), "Export closes the sheet")
        again = ExportSheet.present(self.button, title="Export", choices=choices)
        again.on_cancel = lambda: cancelled.append(1)
        _spin()
        self.assertTrue(again._scrim.get_parent() is not None, "a scrim covers the window")
        again._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertEqual(cancelled, [1])
        self.assertIsNone(again._scrim.get_parent())
        with self.assertRaises(ValueError):
            ExportSheet(title="x", choices=[])
        with self.assertRaises(ValueError):
            ExportChoice("x", "X", shape="round")

    def test_escape_closes(self):
        from luma_appkit import ShareSheet, ShareSubject

        sheet = ShareSheet.present(self.button, document=ShareSubject("Plan.pdf"))
        _spin()
        sheet.floater._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertIsNone(sheet.get_parent())

    def test_text_segments(self):
        from luma_appkit import ModeSwitch

        seg = ModeSwitch([("fit", "Fit"), ("100", "100%")], labels_only=True)
        self.assertTrue(seg.labels_only)
        self.assertTrue(seg.has_css_class("labels-only"))
        seg._width_changed(400)
        self.assertTrue(all(b.label_widget.get_visible() for b in seg.buttons.values()))

    def test_modes_in_the_bar(self):
        from luma_appkit import ActionCenter, BarAction, ModeSwitch

        center = ActionCenter().attach(self.host)
        places = ModeSwitch([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock")])
        center.show_bar([BarAction("plus", "Add city")], modes=places)
        self.assertIs(center.bar_row.get_first_child(), places)
        self.assertTrue(places.has_css_class("in-bar"))
        places._width_changed(700)
        self.assertTrue(places.buttons["alarms"].label_widget.get_visible(), "a bar folds labels only on a phone")
        places._width_changed(400)
        self.assertFalse(places.buttons["alarms"].label_widget.get_visible())
        aspect = ModeSwitch([("free", "Free"), ("4:3", "4:3")])
        center.show_bar([aspect, BarAction("check", "Done", primary=True)])
        self.assertIs(center.bar_row.get_first_child(), aspect, "a segment is a bar item too")


if __name__ == "__main__":
    unittest.main()
