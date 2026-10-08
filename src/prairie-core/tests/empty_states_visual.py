#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Disposable source/packaged GTK captures. No real contacts or modem access."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk
from empty_states_runtime_smoke import descendants, settle
from prairie_apps import contacts, messages, notes
from prairie_apps.eds_backend import ContactRecord


def capture(window, output):
    settle(lambda: window.get_mapped() and window.get_width() > 0)
    # Let the frame clock paint the state before snapshotting its actual node.
    until = time.monotonic() + .15
    while time.monotonic() < until:
        settle()
        time.sleep(.01)
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(window.get_surface())
    try:
        texture = renderer.render_texture(node, None)
        texture.save_to_png(str(output))
    finally:
        renderer.unrealize()
    geometry = []
    for widget in descendants(window):
        if not widget.get_mapped():
            continue
        if any(name.startswith(("luma-empty", "luma-list-empty", "notes-folder-empty", "messages-intro")) for name in widget.get_css_classes()):
            ok, bounds = widget.compute_bounds(window)
            if ok:
                geometry.append(dict(classes=list(widget.get_css_classes()),
                    text=widget.get_text() if isinstance(widget, Gtk.Label) else "",
                    bounds=[bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()],
                    font=widget.get_pango_context().get_font_description().to_string() if isinstance(widget, Gtk.Label) else ""))
    output.with_suffix(".json").write_text(json.dumps(geometry, indent=2))


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        os.environ.update(XDG_DATA_HOME=directory+"/data", XDG_STATE_HOME=directory+"/state", XDG_CONFIG_HOME=directory+"/config", PRAIRIE_EDS_MODE="disabled")
        # Force each requested appearance independently of the host desktop preference.
        patch("luma_appkit.widgets._prefers_dark", lambda: Adw.StyleManager.get_default().get_dark()).start()
        app = notes.NotesApplication()
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN)
        assert app.register(None)
        apps = {"notes": app}
        for name, cls in (("messages", messages.MessagesApplication), ("contacts", contacts.ContactsApplication)):
            instance = cls()
            instance.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
            assert instance.register(None)
            apps[name] = instance
        contacts._install_contacts_style(); messages.install_messages_theme()
        icon_path = Path(__file__).resolve().parents[2] / "luma-platform/appkit/icons"
        if icon_path.is_dir():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(icon_path))
        for dark in (False, True):
            mode = "dark" if dark else "light"
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
            # Application-owned styles reparse token references for each treatment.
            notes._install_notes_style(); contacts._install_contacts_style()
            if messages._messages_style_provider:
                messages._messages_style_provider.load_from_path(os.environ.get("LUMA_MESSAGES_STYLE_PATH", "/usr/share/prairie-core/messages.css"))
            with patch.object(messages.MessagesWindow,"_start_external_context_load",return_value=False), patch.object(messages.MessagesWindow,"_restore_last_conversation",return_value=False):
                w = messages.MessagesWindow(apps["messages"]); w.set_default_size(880,620); w.present()
                for item in w.store.threads(): w.store.delete_thread(item.address)
                w._show_nothing_selected(); capture(w,output/f"{mode}-messages-fresh.png")
                row = w._recipient_row("+12025550100","Test Person","+12025550100")
                w._recipient_activated(w.recipient_list,row); capture(w,output/f"{mode}-messages-new.png")
                w.store.add("+12025550100","A test message",direction="incoming")
                w._show_nothing_selected(); capture(w,output/f"{mode}-messages-unselected.png")
                w.search.set_text("zzz"); w._reload_threads(); capture(w,output/f"{mode}-messages-search.png")
                w.close()
            w = notes.NotesWindow(app); w.set_default_size(880,620); w.present()
            for note in w.store.list_notes(): w.store.soft_delete_note(note.id)
            for folder in w.store.list_folders(): w.store.delete_folder(folder.id)
            w._show_empty(); w._reload_sidebar(); capture(w,output/f"{mode}-notes-fresh.png")
            w.create_note(); w.create_folder(); capture(w,output/f"{mode}-notes-folder.png")
            w.close()
            with patch.object(contacts,"load_contacts",return_value=()):
                w=contacts.ContactsWindow(apps["contacts"]);w.set_default_size(880,620);w.present()
                capture(w,output/f"{mode}-contacts-fresh.png")
                w.all_records={"test":ContactRecord("test","Test Person")};w._show_empty()
                capture(w,output/f"{mode}-contacts-unselected.png")
                with patch.object(contacts,"load_contacts", side_effect=lambda search="": () if search else tuple(w.all_records.values())):
                    w.search_text="zzz";w._reload()
                capture(w,output/f"{mode}-contacts-search.png");w.close()
    print("PASS: 18 source/packaged GTK empty-state captures and geometry written to",output)

if __name__ == "__main__": main()
