#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the installed shared v71 GTK window with isolated fixture mail.

Replaces source-string assertions about former monolithic widget classes.
No account enrollment, network sync, authentication, or sending is performed.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gio, Gtk

from charlie_luma.application import CharlieApplication
from charlie_luma.mime import message_presentation
from luma_appkit import icons


def pump(seconds=.25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def wait(predicate, message):
    deadline = time.monotonic() + 12
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(message)
        pump(.04)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def named(window, name):
    found = next((w for w in walk(window) if w.get_name() == name), None)
    assert found is not None, f"missing native widget: {name}"
    return found


def visible_copy(widget):
    result = []
    for child in walk(widget):
        if isinstance(child, Gtk.Label):
            result.append(child.get_text())
        if text := child.get_tooltip_text():
            result.append(text)
    return "\n".join(result)


def screenshot(window, name):
    directory = os.environ.get('LUMA_CREATOR_REVIEW_CAPTURE')
    if not directory:
        return
    from gi.repository import Gsk
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    assert node is not None
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(window.get_surface())
    try:
        renderer.render_texture(node, None).save_to_png(str(destination / (name + '.png')))
    finally:
        renderer.unrealize()


def main():
    with tempfile.TemporaryDirectory(prefix="charlie-v71-runtime-") as directory:
        os.environ.update(XDG_DATA_HOME=directory, XDG_CONFIG_HOME=directory,
                          XDG_CACHE_HOME=directory, LUMA_CHARLIE_FIXTURE="v71",
                          CHARLIE_TEST_WIDTH="360", CHARLIE_TEST_HEIGHT="828")
        schema = Path(os.environ['LUMA_CREATOR_SCHEMA_FILE'])
        schema_dir = Path(directory) / 'schema'
        schema_dir.mkdir()
        (schema_dir / schema.name).write_bytes(schema.read_bytes())
        subprocess.run(['glib-compile-schemas', str(schema_dir)], check=True)
        os.environ.update(GSETTINGS_SCHEMA_DIR=str(schema_dir), GSETTINGS_BACKEND='memory')
        appearance = os.environ.get('CHARLIE_TEST_COLOR_SCHEME', 'light')
        Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment', appearance)
        application = CharlieApplication(data_home=Path(directory))
        application.did_initial_sync = True
        failures = []
        passed = []

        def check(name, assertion):
            assert assertion, name
            passed.append(name)
            print("native v71 PASS:", name, flush=True)

        def exercise():
            try:
                window = application.window
                wait(lambda: window is not None and window.get_mapped() and window._threads,
                     "installed shared window did not map fixture mailbox")
                check("actual selected appearance", Adw.StyleManager.get_default().get_dark() == (appearance == 'dark'))
                check("identity", window.get_title() == "Charlie")
                check("fixture is memory-only", application.fixture_mode and application.fixture is not None)
                check("real mailbox rows", bool(window._rows))
                # Return through both phone widths after the full desktop
                # split: a wide child minimum must not trap the real surface
                # before the shared owner can change presentation.
                for width in (360, 500, 1024, 1440, 500, 360):
                    window.set_default_size(width, 828)
                    wait(lambda: window.get_surface().get_width() == width,
                         f"actual native surface did not reach {width}")
                    pump(.35)
                    # GtkWindow's content allocation excludes native CSD
                    # shadows: Xvfb measured490px content in a500px surface.
                    # The responsive owner consumes real available content.
                    available = window.get_width()
                    check(f"real native content allocation {width}",
                          0 < available <= width and window.width == available)
                    check(f"responsive presentation {width}", window.phone == (available < 560))
                    check(f"sidebar availability {width}", window.nav.get_visible() == (available > 1060))
                    for name, host, island, content in (
                        ("mailbox", window.list_host, window.list_island_widget, window.list_content),
                        ("conversation", window.thread_host, window.thread_island_widget, window.body_scroller),
                    ):
                        check(f"native {name} content owner {width}",
                              content.get_parent() is (host if window.phone else island))
                        check(f"native {name} host child {width}",
                              host.get_child() is (content if window.phone else island))
                    if not window.phone:
                        island = window.list_island_widget
                        padding = island.get_style_context().get_padding()
                        check(f"shared Island has no caller CSS padding {width}",
                              (padding.top, padding.right, padding.bottom, padding.left) == (0, 0, 0, 0))
                        ok, bounds = island.compute_bounds(window.list_host)
                        check(f"Island surface starts at its host top/left {width}", ok and
                              bounds.get_x() == 0 and bounds.get_y() == 0 and
                              bounds.get_width() == window.list_host.get_width() and
                              bounds.get_height() <= window.list_host.get_height())
                        ok, child = window.list_content.compute_bounds(island)
                        check(f"inner list spacing preserves content {width}", ok and
                              child.get_x() == 8 and child.get_y() == 12 and
                              child.get_width() == island.get_width() - 16)
                    else:
                        check(f"phone has no duplicate desktop inset {width}",
                              window.list_content.get_margin_top() == 0 and
                              window.list_content.get_margin_start() == 0 and
                              window.list_content.get_margin_end() == 0)
                    screenshot(window, f'charlie-list-{width}-{appearance}')
                    if window.phone:
                        window._back_to_list()
                        check(f"list-first {width}", window.list_first.showing == "list")
                    thread = next(t for t in window._threads if not t.brand)
                    window.open_thread(thread.id)
                    pump(.25)
                    check(f"real thread selection {width}", window.thread.id == thread.id)
                    check(f"message bubbles {width}", bool(window._bubbles))
                    if window.phone:
                        check(f"push detail {width}", window.list_first.showing == "detail")
                        window._back_to_list()
                        check(f"back preserves selection {width}", window.thread.id == thread.id)
                    for message in window.thread.messages:
                        bubble = window._bubbles[message.id]
                        check(f"sent/received alignment {width}/{message.id}",
                              bubble.get_halign() == (Gtk.Align.FILL if message.body_html and message_presentation(message).designed_html
                                                     else Gtk.Align.END if message.outgoing else Gtk.Align.START))
                quit_command = window.commands.get("mail.quit")
                check("Quit has a canonical renderable icon", bool(quit_command.icon) and
                      Gtk.IconTheme.get_for_display(window.get_display()).has_icon(icons.icon_name(quit_command.icon)))
                check("Quit keeps the existing application behavior", quit_command.execute == application.quit)
                # Real native action panels preserve all phone overflow actions.
                window.set_default_size(500, 828)
                wait(lambda: window.get_surface().get_width() == 500, "phone surface resize failed")
                window.open_thread(next(t.id for t in window._threads if not t.brand))
                pump(.25)
                panel = window._more_panel()
                copy = visible_copy(panel)
                for action in ("Details", "Mark unread", "Move to", "Print", "Delete thread"):
                    check(f"native phone overflow {action}", action in copy)
                # Search changes the real list model through its editable widget.
                mailbox_count = len(window._threads)
                application.activate_action("mail-search", None)
                pump(.4)
                # ActionCenter opens a native field of its own; the retained
                # BarSearch item points at the hidden normal-row well.
                def mapped_search_fields():
                    return [w for w in walk(window.list_bar)
                            if isinstance(w, Gtk.Editable) and w.get_mapped()]
                wait(lambda: len(mapped_search_fields()) == 1,
                     "one real native search field did not map")
                editable = mapped_search_fields()[0]
                check("real search editable", editable.get_mapped())
                check("search focuses the native field", window.get_focus() is editable)
                editable.set_text("Launch")
                wait(lambda: window.query == "Launch", "real search signal did not reach model")
                check("search filters catalog", bool(window._threads) and len(window._threads) < mailbox_count)
                pump(.3)
                check("typing preserves the mapped search field",
                      editable.get_mapped() and mapped_search_fields() == [editable])
                check("typing preserves native focus", window.get_focus() is editable)
                editable.set_text("")
                wait(lambda: window.query == "", "search clear did not restore model")
                check("search restores mailbox", len(window._threads) == mailbox_count)
                pump(.2)
                check("clearing preserves the mapped focused search field",
                      editable.get_mapped() and window.get_focus() is editable)
                window.list_bar.close_search()
                pump(.2)
                check("closing restores the normal phone controls",
                      not window.list_bar.searching and window.list_search.entry.get_mapped())
                # Cached photo and initials share the native person's dimensions.
                person = next(p.address for p in window.fixture.people.values()
                              if p.address.casefold() not in window.people.own and p.brand is None)
                before = window.people.avatar(person, 32)
                pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
                pixbuf.fill(0x336699ff)
                window.people._faces[person.casefold()] = Gdk.Texture.new_for_pixbuf(pixbuf)
                after = window.people.avatar(person, 32)
                check("cached face alignment", before.get_halign() == after.get_halign() == Gtk.Align.CENTER)
                for orientation in (Gtk.Orientation.HORIZONTAL, Gtk.Orientation.VERTICAL):
                    before_size = before.get_first_child().measure(orientation, -1)
                    after_size = after.get_first_child().measure(orientation, -1)
                    check(f"cached face native measurement {orientation.value_nick}",
                          before_size[:2] == after_size[:2] == (32, 32))
                # The real provider picker is rendered without choosing a provider.
                application.show_account_editor(None, None)
                pump(.3)
                account = application.account_editor
                check("native enrollment picker", account is not None and account.get_mapped())
                copy = visible_copy(account)
                check("real account providers", "Google" in copy and "Microsoft" in copy and "Other" in copy)
                account.close()
                # Close/minimize preserves task state and the same shared window.
                original = window
                # Fixture captures deliberately destroy on close; run the
                # normal resident-task branch against the same memory store.
                fixture = window.fixture
                window.fixture = None
                window.close()
                window.fixture = fixture
                pump(.2)
                check("close preserves native task", application.window is original)
                application.activate()
                pump(.2)
                check("reopen preserves native task", application.window is original and original.get_mapped())
            except BaseException as error:
                failures.append(error)
            application.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(700, exercise)
        result = application.run(["org.projectluma.Charlie"])
        if failures:
            raise failures[0]
        print(f"native v71 installed runtime: {len(passed)} assertions passed")
        return result


if __name__ == "__main__":
    raise SystemExit(main())
